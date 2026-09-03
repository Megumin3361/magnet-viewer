"""底部状态面板：会话状态 / 做种 / 速度 / 缓冲。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from core.models import human_size


class StatusPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = QLabel("空闲 —— 粘贴磁力链或打开 .torrent 文件开始解析")
        self.peers = QLabel("做种 -  连接 -")
        self.speed = QLabel("速度 0 B/s")
        self.buffer = QLabel("")

        for lbl in (self.peers, self.speed, self.buffer):
            lbl.setStyleSheet("color:#555;")

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(self.state, 1)
        row.addWidget(self.buffer)
        row.addWidget(self.speed)
        row.addWidget(self.peers)

    def update_status(self, status: dict | None):
        if status is None:
            return
        self.peers.setText(f"做种 {status['num_seeds']}  连接 {status['num_peers']}")
        self.speed.setText(f"速度 {human_size(status['download_rate'])}/s")
        if status.get("preview_file") is not None:
            self.buffer.setText(f"预览缓冲 {status['buffer'] * 100:.1f}%")
        else:
            self.buffer.setText("")

    def set_state(self, text: str):
        self.state.setText(text)
