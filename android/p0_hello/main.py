"""P0-2 / P0-4 探针应用：验证 PySide6 能否在 Android 上启动，并探测可用 Qt 模块。

设计约束（与 android/main.py 的完整应用区分开）：
- **不 import core/ 与 ui/**——完整应用依赖 python-libtorrent（Android 无 wheel）
  与桌面专有 API，直接打包必崩；本探针只回答决策门的两个问题：
  1. P0-2：PySide6 窗口能否在真机/模拟器显示；
  2. P0-4 前置：QtMultimedia 等模块在 Android 是否可导入（决定边下边播是否可行）。
- 入口必须命名为 main.py（pyside6-android-deploy 的硬性要求）。

用法（CI 构建）：pyside6-android-deploy --name MagnetProbe ...
真机验证：安装 APK → 应显示窗口与模块探测结果；结果同时打印到 logcat（TAG=MagnetProbe）。
"""
import os
import platform
import sys

from PySide6.QtCore import QT_VERSION_STR, __version__ as PYSIDE_VERSION, qVersion
from PySide6.QtWidgets import (QApplication, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

TAG = "MagnetProbe"

# 依次探测：决定后续路线的关键模块（可用性直接写入界面与 logcat）
PROBE_MODULES = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",          # P0-4：边下边播依赖（QMediaPlayer）
    "PySide6.QtMultimediaWidgets",
]


def probe_modules() -> list[tuple[str, bool, str]]:
    """逐个 import 探测；返回 (模块, 是否可用, 备注)。"""
    out = []
    for name in PROBE_MODULES:
        try:
            mod = __import__(name, fromlist=["_"])
            note = getattr(mod, "__file__", "") or ""
            out.append((name, True, os.path.basename(note)))
        except Exception as e:
            out.append((name, False, f"{type(e).__name__}: {e}"))
    return out


def collect_report() -> str:
    lines = [
        f"PySide6 : {PYSIDE_VERSION}",
        f"Qt      : {QT_VERSION_STR}（qVersion {qVersion()}）",
        f"Python  : {sys.version.split()[0]}",
        f"平台     : {sys.platform} / {platform.machine()}",
        f"Android : {os.environ.get('ANDROID_ARGUMENT') or '（非 Android 运行时）'}",
        "",
        "== Qt 模块探测 ==",
    ]
    for name, ok, note in probe_modules():
        lines.append(f"[{'OK ' if ok else 'FAIL'}] {name}  {note}")
    return "\n".join(lines)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MagnetProbe")

    report = collect_report()
    # logcat 可见（真机排查用；Windows/桌面调试时无副作用）
    print(f"[{TAG}] ---- 探测报告开始 ----", flush=True)
    for line in report.splitlines():
        print(f"[{TAG}] {line}", flush=True)
    print(f"[{TAG}] ---- 探测报告结束 ----", flush=True)

    win = QWidget()
    win.setWindowTitle("Magnet Probe（P0-2/P0-4）")
    layout = QVBoxLayout(win)

    title = QLabel("PySide6 Android 探针")
    title.setStyleSheet("font-size:18px; font-weight:600;")
    layout.addWidget(title)

    body = QPlainTextEdit(report)
    body.setReadOnly(True)
    layout.addWidget(body, 1)

    btn = QPushButton("重新探测")
    btn.clicked.connect(lambda: body.setPlainText(collect_report()))
    layout.addWidget(btn)

    win.resize(520, 620)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
