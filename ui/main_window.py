"""主窗口：输入解析 + 文件树 ⇄ 预览（视频/图片）双页签。"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import (QObject, QTimer, QStringListModel, Qt, Signal)
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (QCompleter, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from core.config import AppConfig
from core.fetcher import SessionManager
from core.models import ParseResult, PieceMap, TorrentFile, file_disk_path
from core.stream_server import StreamServer
from ui.file_tree import FileTreeWidget
from ui.preview_pane import PreviewPane
from ui.settings_dialog import SettingsDialog, _rmtree_quiet
from ui.status_panel import StatusPanel

TAB_FILES, TAB_PREVIEW = 0, 1


class _Bridge(QObject):
    """把 core 后台线程的回调转成 Qt 信号（跨线程安全）。"""
    metadata_ready = Signal(object)
    resolve_failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("磁力链实时解析查看器 Magnet Viewer")
        self.resize(1040, 700)

        self.cfg = AppConfig()
        self.cache_dir = str(self.cfg.get("cache_dir") or
                             os.path.join(tempfile.gettempdir(),
                                          "magnet_viewer_cache"))

        # ---- core ----
        self.session = SessionManager(self.cache_dir)
        self.session.start(proxy=self.cfg.proxy(),
                           metadata_timeout=self.cfg.get("metadata_timeout"))
        self.bridge = _Bridge()
        self.session.on_metadata = self.bridge.metadata_ready.emit
        self.session.on_error = self.bridge.resolve_failed.emit

        self._path_to_file: dict[str, TorrentFile] = {}  # 磁盘路径 -> 文件
        self.server = StreamServer(self.cache_dir, pieces_cb=self._pieces_map,
                                   demand_cb=self._demand_range)
        self.server.start()

        self.result: ParseResult | None = None
        self._preview_file: TorrentFile | None = None
        self._pending_video: tuple[TorrentFile, str] | None = None  # 等待首批数据
        self._stream_url: str | None = None   # 当前视频流地址（失败重试用）
        self._stream_attempts = 0

        # ---- UI ----
        self._build_ui()
        self._init_history()
        self.setAcceptDrops(True)  # 拖拽 .torrent 文件 / magnet 文本进窗口直接解析
        self.bridge.metadata_ready.connect(self._on_metadata)
        self.bridge.resolve_failed.connect(self._on_error)
        self.tree.file_activated.connect(self._open_preview)
        self.preview.stop_requested.connect(self._stop_preview)
        self.preview.video.seek_requested.connect(self._on_seek)
        self.preview.video.stream_failed.connect(self._on_stream_failed)
        self.preview.gallery.file_requested.connect(self._on_gallery_file)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(700)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    # ---------- 拖拽与历史 ----------

    def _init_history(self):
        """磁力链/种子路径输入历史：输入框自动补全（最近 15 条，置顶去重）。"""
        self._recent_model = QStringListModel(self.cfg.recent(), self)
        completer = QCompleter(self._recent_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.input.setCompleter(completer)

    def dragEnterEvent(self, event: QDragEnterEvent):
        md = event.mimeData()
        if md.hasUrls() or (md.hasText()
                            and md.text().strip().lower().startswith("magnet:")):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        md = event.mimeData()
        if md.hasUrls():
            for url in md.urls():
                path = url.toLocalFile()
                if path.lower().endswith(".torrent"):
                    self.input.setText(path)
                    self._resolve(path)
                    return
        if md.hasText() and md.text().strip().lower().startswith("magnet:"):
            text = md.text().strip()
            self.input.setText(text)
            self._resolve(text)

    # ---------- UI 构建 ----------

    def _build_ui(self):
        central = QWidget(self)
        root = QVBoxLayout(central)

        # 顶栏
        bar = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("粘贴 magnet:?xt=urn:btih:... 磁力链接，或点击右侧按钮选择 .torrent 文件")
        self.btn_open = QPushButton("打开种子文件…")
        self.btn_resolve = QPushButton("解析")
        self.btn_open.clicked.connect(self._pick_torrent)
        self.btn_resolve.clicked.connect(self._resolve_input)
        self.input.returnPressed.connect(self._resolve_input)
        bar.addWidget(self.input, 1)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_resolve)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setFixedWidth(56)
        self.btn_settings.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_settings)
        root.addLayout(bar)

        self.hint = QLabel(
            "解析只获取文件清单（不下载资源本体）；双击视频/图片文件即可在「预览」页边下边播或浏览。"
            f"磁力链元数据获取超时 {int(self.session.metadata_timeout)} 秒。")
        self.hint.setStyleSheet("color:#777; font-size:12px;")
        root.addWidget(self.hint)

        # 页签
        self.tabs = QTabWidget()
        self.tree = FileTreeWidget()
        self.preview = PreviewPane()
        self.tabs.addTab(self.tree, "文件列表")
        self.tabs.addTab(self.preview, "预览")
        self.tabs.setTabEnabled(TAB_PREVIEW, False)
        root.addWidget(self.tabs, 1)

        # 状态栏
        self.status_panel = StatusPanel()
        root.addWidget(self.status_panel)
        self.setCentralWidget(central)

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self.cache_dir,
                             on_clear_cache=self._clear_cache_now, parent=self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            # 代理与超时立即生效（缓存目录重启生效）
            self.session.apply_proxy(self.cfg.proxy())
            self.session._metadata_timeout = float(self.cfg.get("metadata_timeout"))
            self.hint.setText(self.hint.text().split("。")[0] + "。"
                              f"磁力链元数据获取超时 {int(self.session.metadata_timeout)} 秒。")
            self.status_panel.set_state("设置已保存")

    def _clear_cache_now(self):
        """设置对话框「立即清理缓存」：先停预览再清空目录内容。"""
        self._stop_preview()
        _rmtree_quiet(self.cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    # ---------- 动作 ----------

    def _pick_torrent(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择种子文件", "", "种子文件 (*.torrent)")
        if path:
            self.input.setText(path)
            self._resolve(path)

    def _resolve_input(self):
        text = self.input.text().strip()
        if text:
            self._resolve(text)

    def _resolve(self, source: str):
        self.status_panel.set_state("解析中…（加入 DHT 网络获取元数据）")
        self.preview.reset()
        self.session.stop_preview()
        self._preview_file = None
        self._pending_video = None
        self._stream_url = None
        self._stream_attempts = 0
        self.tabs.setTabEnabled(TAB_PREVIEW, False)
        self._recent_model.setStringList(self.cfg.push_recent(source))
        try:
            self.session.resolve(source)
        except Exception as e:
            self._on_error(str(e))

    def _on_metadata(self, result: ParseResult):
        self.result = result
        # 以主窗口的 cache_dir 为准（不依赖解析侧注入，双重保险）
        self._path_to_file = {
            os.path.normpath(file_disk_path(self.cache_dir, f)): f
            for f in result.files
        }
        self.tree.populate(result)
        self.preview.gallery.set_result(result)
        self.tabs.setTabEnabled(TAB_PREVIEW, True)
        self.tabs.setCurrentIndex(TAB_FILES)
        self.status_panel.set_state(
            f"解析完成：{result.name} · {len(result.view_files)} 个文件 · "
            f"共 {result.total_size / 1024 / 1024:.1f} MB · info_hash={result.info_hash[:16]}…")

    def _on_error(self, msg: str):
        self.status_panel.set_state(f"失败：{msg}")
        QMessageBox.warning(self, "解析失败", msg)

    def _open_preview(self, f: TorrentFile):
        if self.result is None:
            return
        try:
            self.session.start_preview(f)
        except Exception as e:
            QMessageBox.warning(self, "预览失败", str(e))
            return
        self._preview_file = f
        self._pending_video = None
        self._stream_url = None
        self._stream_attempts = 0
        if f.is_video:
            self.preview.show_video(f)
            # 不立即播放：等头部连续数据 + 尾部索引块（moov，MP4/MOV）都就绪后
            # 再 set_stream，避免播放器读到稀疏零数据或探测不到 moov
            self._stream_url = self.server.url_for(f.path)
            self._pending_video = (f, self._stream_url)
            self.preview.video.set_waiting(f.name, f.size)
        else:
            self.preview.show_gallery(f)
        self.tabs.setCurrentIndex(TAB_PREVIEW)

    def _stop_preview(self):
        """用户点击「停止预览」：停下载、停播放、复位界面。"""
        self.session.stop_preview()
        self.preview.reset()
        self._preview_file = None
        self._pending_video = None
        self._stream_url = None
        self._stream_attempts = 0
        self.status_panel.set_state("已停止预览（下载配额已释放）")

    def _on_seek(self, byte_offset: int):
        """播放位置跳转 → 调度器从对应分块重新开始顺序下载。"""
        if self._preview_file is not None and self._preview_file.is_video:
            self.session.scheduler.seek_to_byte(byte_offset)

    def _on_gallery_file(self, f):
        """画廊中切换到未下载的图片 → 按需下载该文件。"""
        if f is None or f == self._preview_file:
            return
        try:
            self.session.start_preview(f)
            self._preview_file = f
        except Exception:
            pass

    def _pieces_map(self, disk_path: str):
        """流服务回调：返回该文件的分块映射（按已下载分块判定可读区间）。

        非预览文件（图片/已完成的文件）返回 None → 按完整静态文件服务。
        """
        f = self._path_to_file.get(os.path.normpath(disk_path))
        if f is None:
            return None
        pl = self.session.piece_length()
        if not pl:
            return None
        return PieceMap(piece_length=pl, offset=f.offset,
                        start_piece=f.start_piece, end_piece=f.end_piece,
                        size=f.size, have=self.session.have_piece)

    def _demand_range(self, disk_path: str, start: int, end_excl: int):
        """流服务回调：播放器要读的字节尚未下载 → 立刻改下载这段。"""
        f = self._path_to_file.get(os.path.normpath(disk_path))
        if f is None or f is not self._preview_file:
            return
        self.session.scheduler.request_range(start, end_excl)

    def _refresh_status(self):
        st = self.session.status()
        self.status_panel.update_status(st)
        self.preview.gallery.update_status(st)
        if self._preview_file is not None and st is not None:
            self.preview.video.update_buffer(st.get("buffer", 0.0),
                                             st.get("download_rate", 0))
        # 视频开播条件：头部连续数据 + 尾部索引块（moov）都已就绪
        if self._pending_video is not None and st is not None:
            f, url = self._pending_video
            contig = st.get("contiguous", 0)
            if contig >= min(1024 * 1024, f.size) and st.get("tail_ready", True):
                self._pending_video = None
                self.preview.video.set_stream(url, f.name, f.size)

    # ---------- 播放失败重试 ----------

    def _on_stream_failed(self):
        """播放器打开媒体失败：多半是索引块/数据仍在下载，稍后重试。"""
        if self._preview_file is None or self._pending_video is not None:
            return  # 已停止或尚未开播
        if self._stream_url is None:
            return
        if self._stream_attempts >= 4:
            self.status_panel.set_state("播放器多次打开失败：数据仍未就绪，继续下载中…")
            return
        self._stream_attempts += 1
        delay = 1500 * self._stream_attempts
        QTimer.singleShot(delay, self._retry_stream)

    def _retry_stream(self):
        if self._preview_file is None or self._stream_url is None:
            return
        st = self.session.status()
        if st is None:
            QTimer.singleShot(1500, self._retry_stream)
            return
        if not st.get("tail_ready", True) or st.get("contiguous", 0) < 1:
            # 数据仍未就绪：静默等待，不算失败次数，避免反复弹错误
            QTimer.singleShot(1500, self._retry_stream)
            return
        self.preview.video.set_stream(self._stream_url,
                                      self._preview_file.name,
                                      self._preview_file.size)

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        try:
            self.session.shutdown()
            self.server.shutdown()
            if self.cfg.get("clear_cache_on_exit"):
                _rmtree_quiet(self.cache_dir)
        finally:
            super().closeEvent(event)
