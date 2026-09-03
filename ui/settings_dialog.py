"""设置对话框：代理 / 元数据超时 / 缓存目录 / 退出清理。"""
from __future__ import annotations

import os
import shutil

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout)

from core.config import AppConfig

PROXY_LABELS = [("none", "不使用代理（直连）"),
                ("socks5", "SOCKS5"),
                ("http", "HTTP")]


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, cache_dir: str, on_clear_cache=None,
                 parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.cache_dir = cache_dir
        self._on_clear_cache = on_clear_cache  # 由主窗口注入：停止预览并清缓存
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)

        form = QFormLayout()

        self.proxy_type = QComboBox()
        for value, label in PROXY_LABELS:
            self.proxy_type.addItem(label, value)
        self.proxy_type.setCurrentIndex(
            max(0, [v for v, _ in PROXY_LABELS].index(cfg.get("proxy_type"))
                if cfg.get("proxy_type") in [v for v, _ in PROXY_LABELS] else 0))
        form.addRow("代理类型", self.proxy_type)

        self.proxy_host = QLineEdit(str(cfg.get("proxy_host")))
        self.proxy_host.setPlaceholderText("例如 127.0.0.1")
        form.addRow("代理主机", self.proxy_host)

        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(int(cfg.get("proxy_port")))
        form.addRow("代理端口", self.proxy_port)

        self.proxy_user = QLineEdit(str(cfg.get("proxy_user")))
        form.addRow("账号（可空）", self.proxy_user)
        self.proxy_pass = QLineEdit(str(cfg.get("proxy_pass")))
        self.proxy_pass.setEchoMode(QLineEdit.Password)
        form.addRow("密码（可空）", self.proxy_pass)

        self.proxy_peer = QCheckBox("Peer 连接也走代理（保护 IP 隐私，推荐勾选）")
        self.proxy_peer.setChecked(bool(cfg.get("proxy_peer")))
        form.addRow("", self.proxy_peer)

        self.timeout = QSpinBox()
        self.timeout.setRange(30, 600)
        self.timeout.setValue(int(cfg.get("metadata_timeout")))
        self.timeout.setSuffix(" 秒")
        form.addRow("磁力链元数据超时", self.timeout)

        row = QHBoxLayout()
        self.cache_edit = QLineEdit(str(cfg.get("cache_dir") or cache_dir))
        self.cache_edit.setPlaceholderText("留空 = 系统临时目录/magnet_viewer_cache（重启生效）")
        btn = QPushButton("浏览…")
        btn.clicked.connect(self._pick_dir)
        row.addWidget(self.cache_edit, 1)
        row.addWidget(btn)
        form.addRow("缓存目录", row)

        self.clear_on_exit = QCheckBox("退出时清理预览缓存")
        self.clear_on_exit.setChecked(bool(cfg.get("clear_cache_on_exit")))
        form.addRow("", self.clear_on_exit)

        note = QLabel("提示：代理与超时保存后立即生效；缓存目录修改需重启程序。"
                      "配置文件中请勿包含空格或非 ASCII 字符。")
        note.setStyleSheet("color:#777; font-size:12px;")
        note.setWordWrap(True)

        self.btn_clear = QPushButton("立即清理缓存")
        self.btn_clear.clicked.connect(self._clear_cache)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_clear)
        row2.addStretch(1)
        layout.addLayout(row2)
        layout.addWidget(buttons)

    # ---------- 内部 ----------

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择缓存目录")
        if d:
            self.cache_edit.setText(d)

    def _clear_cache(self):
        if self._on_clear_cache is not None:
            self._on_clear_cache()
        removed = _rmtree_quiet(self.cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        QMessageBox.information(self, "清理完成",
                                f"缓存已清理{'（' + str(removed) + ' 项）' if removed else ''}")

    def _save(self):
        self.cfg.set("proxy_type", self.proxy_type.currentData())
        self.cfg.set("proxy_host", self.proxy_host.text().strip())
        self.cfg.set("proxy_port", self.proxy_port.value())
        self.cfg.set("proxy_user", self.proxy_user.text())
        self.cfg.set("proxy_pass", self.proxy_pass.text())
        self.cfg.set("proxy_peer", self.proxy_peer.isChecked())
        self.cfg.set("metadata_timeout", self.timeout.value())
        self.cfg.set("cache_dir", self.cache_edit.text().strip())
        self.cfg.set("clear_cache_on_exit", self.clear_on_exit.isChecked())
        self.accept()


def _rmtree_quiet(path: str) -> int:
    """静默清空目录内容，返回删除条数（失败忽略）。"""
    removed = 0
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
                removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
