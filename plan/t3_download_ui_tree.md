# magnet-viewer 下载管理模块 · UI 功能树

> 依据已读源码：ui/main_window.py（双页签/顶栏/状态刷新）、ui/file_tree.py、ui/preview_pane.py、ui/preview_player.py、ui/gallery.py、ui/status_panel.py、ui/settings_dialog.py、core/config.py。优先级：◆MVP ◇增强 ○远期。

## A 信息架构
- A1◆ 新增「下载」页签：`TAB_DOWNLOADS=2`（main_window.py:24 旁加常量），在 :157-158 后 `addTab(DownloadsPane(), "下载")`；布局用 QSplitter=上任务列表（QTreeView，同 file_tree.py:11）+ 下详情区，MVP 可退化为单列表。
- A2◆ 全局「添加下载」入口：①顶栏「解析」右侧加按钮（main_window.py:126-141）；②文件树右键「添加下载」（file_tree.py:77 双击逻辑旁加 contextMenu）；③预览页「停止预览」旁加「转下载」（preview_pane.py:28-31）；④拖拽 magnet/.torrent 复用 dropEvent（main_window.py:99-117）。
- A3◇ 详情区：种子名/info_hash/做种数/文件子列表（勾选取消下载单文件）。

## B 任务列表项
- B1◆ QTreeView+QStandardItemModel 四列：名称/大小/进度/速度·ETA（仿 file_tree.py:14-27 表头风格）；进度条用 QStyledItemDelegate 嵌 QProgressBar（样式同 preview_player.py:39-42）。
- B2◆ 状态：emoji+颜色 ⏳白/⏸灰/✅绿/❌红/🌱做种蓝（沿用 gallery.py:64 emoji、status_panel.py:18 `#555` 色风格）。
- B3◆ 操作=右键菜单（暂停/恢复/删除/优先级↑↓/打开所在目录/打开预览）+ 顶栏工具按钮；◇ 多选批量（ExtendedSelection）、行内 hover 按钮。
- B4◇ 过滤 QComboBox（全部/下载中/已完成/失败/暂停）+ QSortFilterProxyModel 表头排序。
- ○ 限速/计划任务、任务导入导出。

## C 交互细节
- C1◆ 添加确认流：解析完成后点「添加下载」→ 确认 QDialog（仿 settings_dialog.py:87-89 的 Save/Cancel）显示名称/大小/保存目录（默认取设置，可改）。
- C2◇ 完成通知：MVP=行变色+StatusPanel 文案；增强=QSystemTrayIcon 托盘气泡（入口在 closeEvent 前，main_window.py:358）。
- C3◆ 失败原因：行 tooltip + 详情区红字；双击失败行弹 QMessageBox（仿 main_window.py:235）。
- C4◇ 与 file_tree/预览联动：双击未完成文件→状态栏「该文件下载中…」不切预览；完成→走 _open_preview（main_window.py:237-258）；「下载页-打开预览」同路径。

## D 与现有 UI 一致性
- D1◆ StatusPanel 增「总下载速度」label：update_status() 汇总全任务速率（status_panel.py:14/27-31）。
- D2◆ 设置新增三项（settings_dialog.py:60-77 后追加 form.addRow）：默认并发数 QSpinBox、默认下载目录（QLineEdit+浏览…，仿 :66-73 缓存目录行）、完成后继续做种 QCheckBox（仿 :75-77）；键入 config.py:10-20 DEFAULTS。
- D3◆ 控件/文案沿用：QLineEdit/QPushButton/QLabel 集（main_window.py:9-11）、微软雅黑 9（main.py:17）、全角标点祈使句风格（main_window.py:128/145-150）。

## E 空态/异常态
- E1◆ 无任务居中引导文案（仿 gallery.py:34-37 占位风格）。
- E2◆ 重复 info_hash→提示已存在并定位任务（main_window.py:229-231 已有 hash 字段）；畸形种子走 _on_error（:233-235）。
- E3◇ 磁盘满预检（写入前 statvfs）→任务标 ❌+warning；○ 任务失败自动重试策略。

**推荐实施顺序**：A1→B1/B2/B3→C1/C3→D1/D2→E1/E2（MVP 闭环）→A2③④/C2/C4→A3/B4/E3（增强）→○远期。