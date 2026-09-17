"""jlibtorrent（Pyjnius）引擎桥接的构建期占位/自检。

真实实现由 android/build/spec 注入（pyjnius 类在 Android 运行时可得）。
此处仅定义契约，供构建自检确认导入路径存在。
"""
ENGINE_NAME = "jlibtorrent"
