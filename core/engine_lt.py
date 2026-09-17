"""python-libtorrent 2.1.x 引擎实现（core.engine.TorrentEngine 的绑定层）。

调用样式与 core.fetcher.py 现状逐点一致（接线期零行为变化的前提）：

- 会话创建用「settings dict」（2.1.x 的 session_params/dict 配置形态，
  不再走 settings_pack 对象）；
- alert mask 用 ``lt.alert.category_t`` 属性拼整型，未知名跳过（沿用
  fetcher.start 的 try/except 逐名拼接）；
- torrent flags 用 ``lt.torrent_flags.*`` 位运算（set_flags/unset_flags
  风格在引擎层归一为 add/clear 位图）；
- ``resume_data`` / ``has_metadata`` / ``set_priority`` 在 2.1.x 已标
  DeprecationWarning，与 fetcher 今日直接调用完全等价（告警不吞、
  不换新 API）——避免接线期引入任何行为差。
"""
from __future__ import annotations

import warnings

import libtorrent as lt

from .engine import (FLAG_AUTO_MANAGED, FLAG_SEED_MODE, FLAG_UPLOAD_MODE,
                     EngineAlert, EngineHandle, EngineParams, EngineStatus,
                     TorrentEngine)


def _flags_to_bits(flags) -> int:
    """libtorrent flag 位图 → EngineParams.flag_bits（引擎无关编码）。"""
    bits = 0
    if int(flags) & int(lt.torrent_flags.upload_mode):
        bits |= FLAG_UPLOAD_MODE
    if int(flags) & int(lt.torrent_flags.auto_managed):
        bits |= FLAG_AUTO_MANAGED
    if int(flags) & int(lt.torrent_flags.seed_mode):
        bits |= FLAG_SEED_MODE
    return bits


def _bits_to_flags(bits: int):
    """EngineParams.flag_bits → libtorrent flag 位图。"""
    out = 0
    if bits & FLAG_UPLOAD_MODE:
        out |= int(lt.torrent_flags.upload_mode)
    if bits & FLAG_AUTO_MANAGED:
        out |= int(lt.torrent_flags.auto_managed)
    if bits & FLAG_SEED_MODE:
        out |= int(lt.torrent_flags.seed_mode)
    return out


def _ih_from_hash(h) -> str:
    """info_hash 对象 → 归一小写十六进制（无效返回空串，语义同 fetcher 兜底）。"""
    try:
        ih = str(h)
    except Exception:
        return ""
    ih = ih.strip().lower()
    if len(ih) not in (40, 64) or any(c not in "0123456789abcdef" for c in ih):
        return ""
    return ih


class _LtHandle(EngineHandle):
    """torrent_handle 薄包装：全部方法逐点转发（调用形态与 fetcher 一致）。"""

    def __init__(self, h: lt.torrent_handle):
        self._h = h

    # ---------- 状态 ----------

    def status(self) -> EngineStatus:
        s = self._h.status()
        has_meta = self._h.has_metadata()   # 2.1.x Deprecation 告警同 fetcher 现状
        return EngineStatus(
            total_done=int(s.total_done),
            download_payload_rate=int(s.download_payload_rate),
            num_peers=int(s.num_peers),
            num_seeds=int(s.num_seeds),
            state=int(s.state),
            progress=float(s.progress),
            priority=int(getattr(s, "priority", getattr(self._h, "queue_priority", 0)) or 0),
            pieces=list(s.pieces),
            has_metadata=bool(has_meta),
        )

    def pause(self) -> None:
        self._h.pause()

    def resume(self) -> None:
        self._h.resume()

    def set_priority(self, priority: int) -> None:
        self._h.set_priority(int(priority))

    def prioritize_files(self, priorities: list[int]) -> None:
        self._h.prioritize_files([int(p) for p in priorities])

    def save_resume_data(self, flush: bool = True) -> None:
        if flush:
            self._h.save_resume_data(lt.save_resume_flags_t.flush_disk_cache)
        else:
            self._h.save_resume_data()

    def need_save_resume_data(self) -> bool:
        return bool(self._h.need_save_resume_data())

    def file_progress(self) -> list[int]:
        return [int(x) for x in self._h.file_progress()]

    def torrent_metadata(self):
        """bytes info 区段；未就绪返回 None（调用方据此判 has_metadata 等价）。"""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ti = self._h.torrent_file()
        if ti is None:
            return None
        try:
            return bytes(ti.info_section())
        except Exception:
            return None

    def num_files(self) -> int:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ti = self._h.torrent_file()
        if ti is None:
            return 0
        return int(ti.num_files())

    def piece_length(self) -> int:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ti = self._h.torrent_file()
        if ti is None:
            return 0
        try:
            return int(ti.piece_length())
        except Exception:
            return 0

    def set_piece_deadline(self, piece: int, deadline: int = 0) -> None:
        self._h.set_piece_deadline(int(piece), int(deadline))

    def clear_piece_deadlines(self) -> None:
        self._h.clear_piece_deadlines()

    def have_piece(self, piece: int) -> bool:
        return bool(self._h.have_piece(int(piece)))

    def connect_peer(self, ip: str, port: int) -> None:
        self._h.connect_peer((ip, int(port)))

    def get_flags(self) -> int:
        try:
            return _flags_to_bits(self._h.status().flags)
        except Exception:
            return 0

    def set_flags(self, bits: int) -> None:
        """按位加 flags（与 fetcher rec.handle.set_flags(<单个 flag>) 等价）。"""
        for flag in self._split_bits(bits):
            self._h.set_flags(flag)

    def clear_flags(self, bits: int) -> None:
        for flag in self._split_bits(bits):
            self._h.unset_flags(flag)

    @staticmethod
    def _split_bits(bits: int) -> list:
        out = []
        if bits & FLAG_UPLOAD_MODE:
            out.append(lt.torrent_flags.upload_mode)
        if bits & FLAG_AUTO_MANAGED:
            out.append(lt.torrent_flags.auto_managed)
        if bits & FLAG_SEED_MODE:
            out.append(lt.torrent_flags.seed_mode)
        return out

    def info_hash(self) -> str:
        """64 位 v2 时仍归一为小写（与 fetcher._hash_key 前 40/64 判定一致）。"""
        return _ih_from_hash(self._h.info_hash())


class LibtorrentEngine(TorrentEngine):
    """lt.session / lt.add_torrent_params 的语义包装（无策略、无吞异常）。"""

    def __init__(self):
        self._ses: lt.session | None = None

    # ---------- 会话 ----------

    def create_session(self, settings: dict) -> None:
        self._ses = lt.session(dict(settings))

    def apply_session_settings(self, settings: dict) -> None:
        if self._ses is None:
            return
        self._ses.apply_settings(dict(settings))

    def get_session_settings(self) -> dict:
        if self._ses is None:
            return {}
        sp = self._ses.get_settings()
        try:
            return dict(sp)
        except Exception:
            return sp   # 旧版返回 dict 的形态直接透传

    def alert_category_mask(self, names: list[str]) -> int:
        cat = lt.alert.category_t
        mask = 0
        for name in names:
            try:
                mask |= int(getattr(cat, name))
            except Exception:
                pass    # 旧版缺该类别名：跳过，不阻断掩码拼装（start() 语义）
        return mask

    # ---------- 加入/移除 ----------

    def parse_magnet(self, uri: str) -> EngineParams:
        p = lt.parse_magnet_uri(uri)
        return _params_from_atp(p)

    def make_torrent_params(self, torrent_file_path: str) -> EngineParams:
        """本地 .torrent 文件 → EngineParams（读入完整文件字节供重建 ti）。

        与 fetcher._resolve_torrent_file / _add_torrent_file_task 现状等价：
        ``atp.ti = lt.torrent_info(path)``。这里把文件字节随 params 一起带走，
        add_torrent 在实现层重建 ti——文件路径不进入抽象接口语义。
        """
        try:
            with open(torrent_file_path, "rb") as f:
                file_bytes = f.read()
            ti = lt.torrent_info(torrent_file_path)
        except Exception:
            raise
        out = _params_from_atp(_make_atp_with_ti(ti))
        out.torrent_info_bytes = file_bytes
        return out

    def params_from_resume(self, resume_bytes: bytes,
                           torrent_file_path: str | None = None) -> EngineParams | None:
        """fastresume 字节 → params；损坏返回 None（调用方静默全新加入）。

        torrent_file_path 提供时注入 ti（本地 .torrent 恢复路径）。
        """
        try:
            atp = lt.read_resume_data(resume_bytes)
        except Exception:
            return None
        if torrent_file_path is not None:
            try:
                atp.ti = lt.torrent_info(torrent_file_path)
            except Exception:
                return None
        return _params_from_atp(atp)

    def add_torrent(self, params: EngineParams) -> EngineHandle:
        if self._ses is None:
            raise RuntimeError("会话未启动")
        atp = _atp_from_params(params)
        handle = self._ses.add_torrent(atp)
        return _LtHandle(handle)

    def remove_torrent(self, handle: EngineHandle, delete_files: bool = False) -> None:
        if self._ses is None:
            return
        h = handle._h if isinstance(handle, _LtHandle) else handle
        self._ses.remove_torrent(h, 1 if delete_files else 0)

    # ---------- 告警 ----------

    def pop_alerts(self) -> list:
        if self._ses is None:
            return []
        out: list[EngineAlert] = []
        for a in self._ses.pop_alerts():
            ih = ""
            try:
                ih = _ih_from_hash(a.handle.info_hash())
            except Exception:
                pass
            if isinstance(a, lt.metadata_received_alert):
                out.append(EngineAlert(kind="metadata", info_hash=ih))
            elif isinstance(a, lt.file_completed_alert):
                out.append(EngineAlert(kind="file_completed", info_hash=ih,
                                       index=int(a.index)))
            elif isinstance(a, lt.torrent_finished_alert):
                out.append(EngineAlert(kind="torrent_finished", info_hash=ih))
            elif isinstance(a, lt.save_resume_data_alert):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    rd = dict(a.resume_data)
                out.append(EngineAlert(kind="resume", info_hash=ih,
                                       resume_data=rd))
            elif isinstance(a, lt.save_resume_data_failed_alert):
                out.append(EngineAlert(kind="resume_failed", info_hash=ih))
            # 其余类别照现状只进批次判断，不产生 EngineAlert 条目
        return out

    # ---------- 生命周期 ----------

    def shutdown(self) -> None:
        self._ses = None


def _make_atp_with_ti(ti):
    """带 ti 的全新 add_torrent_params（ti 赋值路径共享该入口）。"""
    atp = lt.add_torrent_params()
    atp.ti = ti
    return atp


def _params_from_atp(p) -> EngineParams:
    """add_torrent_params → EngineParams（字段子集：现状实际用到的）。"""
    # libtorrent 2.x：atp.info_hash 是**惰性字段**——磁力链 parse_magnet_uri
    # 即时可用，但 ti 赋值路径（本地种子 / resume 注入）恒为零哈希（句柄
    # info_hash 不受影响，add_torrent 只看 ti）。全零视为「未知」→ 回退 ti。
    ih = _ih_from_hash(p.info_hash)
    if ih == "0" * 40 or ih == "0" * 64:
        ih = ""
    if not ih:
        try:
            ti = getattr(p, "ti", None)
            ih = _ih_from_hash(ti.info_hash()) if ti is not None else ""
        except Exception:
            ih = ""
    try:
        trackers = [str(t) for t in getattr(p, "trackers", [])]
    except Exception:
        trackers = []
    try:
        url = str(getattr(p, "url", "") or "")
    except Exception:
        url = ""
    try:
        save_path = str(p.save_path or "") if hasattr(p, "save_path") else ""
    except Exception:
        save_path = ""
    try:
        bits = _flags_to_bits(getattr(p, "flags", 0))
    except Exception:
        bits = 0
    return EngineParams(info_hash=ih, save_path=save_path, url=url,
                        trackers=trackers, flag_bits=bits)


def _atp_from_params(params: EngineParams):
    """EngineParams → add_torrent_params（字段子集反向）。

    flags 保留 add_torrent_params 的默认值（默认即 fetcher 现状：352440
    位组合），仅在其上叠加 EngineParams 位图。torrent_info_bytes 非空时
    重建 ti（本地种子 / restore 注入路径；ti 赋值后 atp.info_hash 恒为零
    哈希，add_torrent 只看 ti，与现状等价）。
    """
    atp = lt.add_torrent_params()
    if params.torrent_info_bytes:
        atp.ti = lt.torrent_info(bytes(params.torrent_info_bytes))
    if params.save_path:
        atp.save_path = params.save_path
    if params.url:
        atp.url = params.url
    # 引擎层补 info-hash：ti=None 且 url 语义（fetcher _add_magnet_task 现状
    # 把磁力链 URI 塞入 url，value 走 parse 后的 ih 惰性字段不回填——这里显式
    # 转 sha1_hash 保证 add_torrent 不缺 ih）。info-hash 已知且≠全零才写。
    if params.info_hash and params.info_hash not in ("0" * 40, "0" * 64) \
            and len(params.info_hash) == 40:
        try:
            atp.info_hash = lt.sha1_hash(bytes.fromhex(params.info_hash))
        except Exception:
            pass
    if params.trackers:
        atp.trackers = list(params.trackers)
    flags = int(atp.flags) | _bits_to_flags(params.flag_bits)
    atp.flags = lt.torrent_flags._make(flags) if hasattr(lt.torrent_flags, "_make") else flags
    return atp
