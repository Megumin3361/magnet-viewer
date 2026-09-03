"""图片画廊：缩略图列表 + 大图浏览（缩放/翻页），图片下载完成后自动载入。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from core.models import ParseResult, TorrentFile, file_disk_path, human_size


class GalleryWidget(QWidget):
    file_requested = Signal(object)  # 用户切换到未下载的图片 → 请求按需下载该文件

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: ParseResult | None = None
        self._files: list[TorrentFile] = []
        self._loaded: set[int] = set()
        self._scale = 1.0
        self._pixmap: QPixmap | None = None
        self._status: dict | None = None

        self.thumb_list = QListWidget(self)
        self.thumb_list.setViewMode(QListWidget.IconMode)
        self.thumb_list.setIconSize(self.thumb_list.iconSize())
        self.thumb_list.setFixedWidth(190)
        self.thumb_list.setWordWrap(True)
        self.thumb_list.currentRowChanged.connect(self._show_index)

        self.viewer = QLabel("（选择左侧图片）")
        self.viewer.setAlignment(Qt.AlignCenter)
        self.viewer.setMinimumSize(300, 300)
        self.viewer.setStyleSheet("background:#181818; color:#aaa;")

        self.btn_prev = QPushButton("← 上一张")
        self.btn_next = QPushButton("下一张 →")
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.info_label = QLabel("")

        nav = QHBoxLayout()
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.info_label, 1)
        nav.addWidget(self.btn_next)

        split = QSplitter(self)
        split.addWidget(self.thumb_list)
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split, 1)
        layout.addLayout(nav)

        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._poll_completed)
        self._timer.start()

    # ---------- 外部接口 ----------

    def set_result(self, result: ParseResult | None):
        self._result = result
        self._files = result.images if result else []
        self._loaded.clear()
        self.thumb_list.clear()
        for f in self._files:
            item = QListWidgetItem(f"⏳ {f.name}（{human_size(f.size)}）")
            item.setData(Qt.UserRole, f)
            self.thumb_list.addItem(item)
        if self._files:
            self.thumb_list.setCurrentRow(0)
        else:
            self.viewer.setText("该资源中没有图片文件")
            self.info_label.setText("")

    def show_file(self, f: TorrentFile):
        """定位到指定图片（用户在文件树双击某张图片时调用）。"""
        for row, item in enumerate(self._files):
            if item.index == f.index:
                self.thumb_list.setCurrentRow(row)
                return

    def update_status(self, status: dict | None):
        self._status = status

    # ---------- 内部 ----------

    def _poll_completed(self):
        """周期检查：图片文件下载完成后自动载入缩略图。"""
        if self._result is None:
            return
        progress = self._status.get("file_progress", []) if self._status else []
        for row, f in enumerate(self._files):
            if row in self._loaded or f.index >= len(progress):
                continue
            if progress[f.index] >= f.size > 0:
                path = file_disk_path(self._result.cache_dir, f)
                if os.path.isfile(path):
                    self._load_thumb(row, path)

    def _load_thumb(self, row: int, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            return
        self._loaded.add(row)
        f = self._files[row]
        item = self.thumb_list.item(row)
        item.setIcon(QIcon(pm.scaled(
            160, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        item.setText(f"{f.name}\n{human_size(f.size)}")

    def _show_index(self, row: int):
        if not (0 <= row < len(self._files)) or self._result is None:
            return
        f = self._files[row]
        path = file_disk_path(self._result.cache_dir, f)
        self._scale = 1.0
        if os.path.isfile(path):
            self._pixmap = QPixmap(path)
            if self._pixmap.isNull():
                self.viewer.setText("图片解码失败")
                self._pixmap = None
        else:
            self._pixmap = None
            # 未下载的图片：请求调度器按需下载该文件
            if row not in self._loaded:
                self.file_requested.emit(f)
        if self._pixmap is None:
            self.viewer.setText("下载中…（完成后自动显示）")
        self._repaint()
        self.info_label.setText(
            f"{row + 1} / {len(self._files)} · {f.name}（{human_size(f.size)}）"
            + (" · Ctrl+滚轮缩放" if self._pixmap is not None else ""))

    def _step(self, delta: int):
        row = self.thumb_list.currentRow() + delta
        if 0 <= row < self.thumb_list.count():
            self.thumb_list.setCurrentRow(row)

    def wheelEvent(self, event):
        if (event.modifiers() & Qt.ControlModifier) and self._pixmap is not None:
            factor = 1.15 if event.angleDelta().y() > 0 else 0.87
            self._scale = max(0.2, min(5.0, self._scale * factor))
            self._repaint()
        else:
            super().wheelEvent(event)

    def _repaint(self):
        if self._pixmap is None:
            return
        base = self.viewer.size()
        scaled = self._pixmap.scaled(
            int(base.width() * self._scale), int(base.height() * self._scale),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.viewer.setPixmap(scaled)
