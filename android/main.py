"""Magnet Viewer 安卓端入口（pyside6-android-deploy 要求入口名为 main.py）。

与桌面版共享 core/ 与 ui/；引擎层改为 Pyjnius + jlibtorrent 桥
（桌面 python-libtorrent 在 Android 无 wheel，见 plan/android_adaptation_plan.md）。
应用代码基线 Python 3.11（buildozer/pyjnius 约束）。
"""
import os
import sys

# 在 android_app 目录下运行（pyside6-android-deploy 打包的 CWD）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_APP_DIR, os.path.dirname(_APP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# engine 选择：core.fetcher 经 env 变量路由（默认桌面 libtorrent 引擎）。
# 真机/构建时注入 JLIBTORRENT=1；缺省回退桌面引擎便于开发期自检。
if os.environ.get("JLIBTORRENT") == "1":
    try:
        import jlibtorrent_bridge  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "JLIBTORRENT=1 但 jlibtorrent_bridge 不可用——"
            "APK 需包含 pyjnius + jlibtorrent jars"
        ) from e


def main() -> int:
    """与桌面 main.py 同一初始化序列（主题/样式装配顺序保持一致）。"""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from core.config import AppConfig
    from ui.main_window import MainWindow
    from ui.style import install as install_chevron_style
    from ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("Magnet Viewer")
    app.setApplicationDisplayName("磁力链实时解析查看器")
    # 安卓无 YaHei，交给平台默认字体族（QFont 族名缺失时 Qt 自动回退）
    app.setFont(QFont("", 10))
    # 箭头绘制必须在 apply_theme 之前装（详见 main.py 注释）
    install_chevron_style(app)
    apply_theme(app, str(AppConfig().get("ui_theme") or "light"))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
