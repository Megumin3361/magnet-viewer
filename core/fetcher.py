"""libtorrent 会话管理：磁力链元数据获取、种子加入、预览调度、状态快照。

本模块不依赖 Qt：回调由后台线程触发，UI 层负责通过 Qt 信号转发。
"""
from __future__ import annotations

import os
import threading
import time

import libtorrent as lt

from .config import lt_proxy_settings
from .models import ParseResult, TorrentFile, safe_rel_path
from .parser import is_torrent_path, parse_torrent_file
from .scheduler import PreviewScheduler

METADATA_TIMEOUT = 90.0  # 秒，超时判定为资源无做种

# 公共 tracker，提升冷门磁力链的 peer 发现率
BOOTSTRAP_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]

STATE_NAMES = {
    getattr(lt.torrent_status, k, None): k.replace("_", " ")
    for k in ("checking_files", "downloading_metadata", "downloading",
              "finished", "seeding", "allocating", "checking_resume_data")
    if getattr(lt.torrent_status, k, None) is not None
}


class SessionManager:
    """持有 libtorrent 会话与后台 alert 循环线程。"""

    def __init__(self, cache_dir: str, listen_port: int = 6881):
        self.cache_dir = os.path.abspath(cache_dir)
        self.listen_port = listen_port
        os.makedirs(self.cache_dir, exist_ok=True)

        self.on_metadata = None   # callback(ParseResult) —— 后台线程触发
        self.on_error = None      # callback(str)
        self.on_file_completed = None  # callback(int file_index) 预览文件完成

        self._ses: lt.session | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        self._handle: lt.torrent_handle | None = None
        self._metadata_timeout = METADATA_TIMEOUT
        self._result: ParseResult | None = None
        self._resolving = False
        self._resolve_started = 0.0
        self._last_timeout_check = 0.0

        self.scheduler = PreviewScheduler()

    # ---------- 生命周期 ----------

    def start(self, proxy: dict | None = None,
              metadata_timeout: float | None = None):
        """启动会话。proxy 见 core.config.lt_proxy_settings 的输入格式。"""
        # libtorrent 2.1.x：settings_pack 已被 session_params / dict 配置取代
        settings = {
            "listen_interfaces": f"0.0.0.0:{self.listen_port}",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "connections_limit": 300,
            "alert_queue_size": 5000,
            "active_downloads": 1,
        }
        if metadata_timeout:
            self._metadata_timeout = float(metadata_timeout)
        settings.update(lt_proxy_settings(proxy))
        # 显式订阅必要告警类别（默认掩码过窄可能漏掉 metadata / file_progress）
        cat = lt.alert.category_t
        mask = 0
        for name in ("status_notification", "error_notification",
                     "file_progress_notification", "storage_notification",
                     "tracker_notification", "connect_notification"):
            try:
                mask |= int(getattr(cat, name))
            except Exception:
                pass
        if mask:
            settings["alert_mask"] = mask
        self._ses = lt.session(settings)
        self._running = True
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()

    @property
    def metadata_timeout(self) -> float:
        return self._metadata_timeout

    def apply_proxy(self, proxy: dict) -> None:
        """运行时切换代理（无需重建会话）。"""
        if self._ses is None:
            return
        try:
            self._ses.apply_settings(lt_proxy_settings(proxy))
        except Exception:
            pass

    def shutdown(self):
        self._running = False
        self.scheduler.stop()
        self._ses = None

    # ---------- 解析入口 ----------

    def resolve(self, source: str):
        """解析磁力链或 .torrent 文件（后台线程内执行网络部分）。"""
        self._reset_current()
        if is_torrent_path(source):
            self._resolve_torrent_file(source)
        else:
            self._resolve_magnet(source)

    def _reset_current(self):
        with self._lock:
            self.scheduler.stop()
            if self._handle is not None and self._ses is not None:
                try:
                    self._ses.remove_torrent(self._handle, 1)
                except Exception:
                    pass
            self._handle = None
            self._result = None
            self._resolving = True
            self._resolve_started = time.time()

    def _resolve_torrent_file(self, path: str):
        try:
            result = parse_torrent_file(path)
        except Exception as e:
            self._emit_error(f"种子文件解析失败：{e}")
            return
        # 必须注入 cache_dir：主窗口据此建立「磁盘绝对路径 -> TorrentFile」映射，
        # 分块可用性判定与按需补拉都依赖它；缺失会导致键退化成相对路径而全部查不到，
        # 预览随即退化为「按完整静态文件服务」，把未下载的稀疏零数据喂给播放器。
        result.cache_dir = self.cache_dir
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(path)
        atp.save_path = self.cache_dir
        atp.flags |= lt.torrent_flags.upload_mode  # 只解析不下载
        try:
            handle = self._ses.add_torrent(atp)
            handle.pause()
        except Exception as e:
            self._emit_error(f"加入会话失败：{e}")
            return
        with self._lock:
            self._handle = handle
            self._result = result
            self._resolving = False
        self._emit_metadata(result)

    def connect_peer(self, ip: str, port: int) -> None:
        """手动添加 Peer（跳过 DHT 发现）。

        用于：本地回环验证、已知 Peer 直连，或网络屏蔽 DHT 时提高成功率。
        """
        with self._lock:
            handle = self._handle
        if handle is not None:
            try:
                handle.connect_peer((ip, int(port)))
            except Exception:
                pass

    def _resolve_magnet(self, uri: str):
        try:
            p = lt.parse_magnet_uri(uri)
        except Exception as e:
            self._emit_error(f"磁力链接无效：{e}")
            return
        p.save_path = self.cache_dir
        p.flags |= lt.torrent_flags.upload_mode  # 只取元数据，不下载资源
        if hasattr(p, "trackers") and not p.trackers:
            p.trackers = BOOTSTRAP_TRACKERS
        try:
            handle = self._ses.add_torrent(p)
        except Exception as e:
            self._emit_error(f"加入 DHT 会话失败：{e}")
            return
        with self._lock:
            self._handle = handle
            self._result = None

    # ---------- 预览 ----------

    def start_preview(self, f: TorrentFile):
        """开始预览某个文件（边下边播 / 图片下载）。"""
        with self._lock:
            handle, result = self._handle, self._result
        if handle is None or result is None:
            raise RuntimeError("请先解析种子")
        self.scheduler.begin(handle, f)

    def stop_preview(self):
        self.scheduler.stop()

    # ---------- 状态 ----------

    def have_piece(self, piece: int) -> bool:
        """指定种子分块是否已完整落盘（供流服务/调度器判定可读区间）。"""
        with self._lock:
            handle = self._handle
        if handle is None:
            return False
        try:
            return bool(handle.have_piece(int(piece)))
        except Exception:
            return False

    def piece_length(self) -> int | None:
        """当前种子分块大小；元数据未就绪时返回 None。"""
        with self._lock:
            handle = self._handle
        if handle is None:
            return None
        try:
            if not handle.has_metadata():
                return None
            return handle.torrent_file().piece_length()
        except Exception:
            return None

    def status(self) -> dict | None:
        """线程安全的状态快照，供 UI 定时轮询。"""
        with self._lock:
            handle = self._handle
        if handle is None:
            return None
        try:
            s = handle.status()
        except Exception:
            return None
        st = {
            "state": STATE_NAMES.get(s.state, str(s.state)),
            "num_peers": s.num_peers,
            "num_seeds": s.num_seeds,
            "download_rate": s.download_payload_rate,
            "total_done": s.total_done,
            "metadata_ready": handle.has_metadata(),
            "buffer": self.scheduler.buffer_progress() if self.scheduler.active else 0.0,
            "contiguous": self.scheduler.contiguous_progress()
            if self.scheduler.active else 0,
            "tail_ready": self.scheduler.tail_ready()
            if self.scheduler.active else True,
            "preview_file": self.scheduler.file,
            "resolving": self._resolving,
            "elapsed": time.time() - self._resolve_started if self._resolving else 0.0,
        }
        try:
            st["file_progress"] = list(handle.file_progress())
        except Exception:
            st["file_progress"] = []
        return st

    @property
    def current_result(self) -> ParseResult | None:
        with self._lock:
            return self._result

    # ---------- alert 循环（后台线程） ----------

    def _alert_loop(self):
        while self._running and self._ses is not None:
            try:
                for a in self._ses.pop_alerts():
                    # 单条告警处理失败不得中断整批，否则 metadata_received_alert 会被丢弃
                    try:
                        if isinstance(a, lt.metadata_received_alert):
                            self._on_metadata_received()
                        elif isinstance(a, lt.file_completed_alert):
                            if self.scheduler.on_file_completed:
                                self.scheduler.on_file_completed(a.index)
                    except Exception:
                        pass
            except Exception:
                pass
            # 元数据超时看门狗
            if self._resolving and time.time() - self._resolve_started > self._metadata_timeout:
                self._resolving = False
                self._emit_error(
                    f"获取元数据超时（>{int(self._metadata_timeout)} 秒）：该资源可能已无做种/无在线 Peer")
                with self._lock:
                    if self._handle is not None:
                        try:
                            self._handle.pause()
                        except Exception:
                            pass
            time.sleep(0.15)

    def _on_metadata_received(self):
        with self._lock:
            handle = self._handle
            self._resolving = False
        if handle is None:
            return
        try:
            handle.pause()  # 拿到元数据即停，不继续下载
            ti = handle.torrent_file()
            result = self._result_from_torrent_info(ti, str(handle.info_hash()))
        except Exception as e:
            self._emit_error(f"元数据处理失败：{e}")
            return
        with self._lock:
            self._result = result
        self._emit_metadata(result)

    def _result_from_torrent_info(self, ti, info_hash: str) -> ParseResult:
        fs = ti.files()
        pl = ti.piece_length()
        root = ti.name()
        files, offset = [], 0
        multi = fs.num_files() > 1
        for i in range(fs.num_files()):
            size = fs.file_size(i)
            fp = fs.file_path(i).replace("\\", "/")
            inner = fp if not multi else fp.split("/", 1)[-1] if "/" in fp else fp
            # safe_rel_path 兜底防御：即使 libtorrent 未净化恶意种子路径也不会逃出缓存目录
            # 单文件种子 libtorrent 存为 save_path/root，不能再套一层（否则预览 404）
            path = safe_rel_path(root, inner) if multi else safe_rel_path(root)
            start = offset // pl
            end = (offset + size - 1) // pl if size > 0 else start
            files.append(TorrentFile(i, path, size, offset, start, end))
            offset += size
        trackers = [e.url for e in ti.trackers()]
        return ParseResult(
            info_hash=info_hash,
            name=root,
            total_size=sum(f.size for f in files if not f.is_pad),
            piece_size=pl,
            num_pieces=ti.num_pieces(),
            files=files,
            trackers=trackers,
            comment=ti.comment(),
            created_by=ti.creator(),
            source="magnet",
        )

    # ---------- 回调发射 ----------

    def _emit_metadata(self, result: ParseResult):
        result.cache_dir = self.cache_dir
        if self.on_metadata:
            try:
                self.on_metadata(result)
            except Exception:
                pass

    def _emit_error(self, msg: str):
        self._resolving = False
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass
