# 磁力链实时解析查看器 (Magnet Viewer)

输入磁力链接或 .torrent 文件，**不下载资源本体**即可实时查看完整的文件清单（名称、大小、目录结构、做种健康度）；对视频文件支持**边下边播**，对图片文件支持**即点即看**（内嵌画廊）。

## 功能

- **实时解析**：磁力链通过 DHT + BEP-9 `ut_metadata` 从在线 Peer 获取元数据（仅几十 KB）；.torrent 文件本地 bencode 直接解码。全程不下载资源本体。
- **文件树视图**：目录层级、单文件大小、占比，双击媒体文件直接预览。
- **视频边下边播**：libtorrent 单文件锁定 + 分块顺序下载 + 索引块（moov）优先 → 本地 HTTP 流服务（仅监听 127.0.0.1，支持 Range）→ 内嵌 QMediaPlayer 播放，实时显示缓冲进度。播放器请求未就绪区间时自动「点播」调度器补拉并等待（moov 探测与任意拖动均可正常工作）。
- **图片画廊**：图片文件按需下载到临时缓存，完成后自动载入缩略图，支持 Ctrl+滚轮缩放、翻页；画廊内切换未下载图片会自动切换下载目标。
- **设置**（右上角「设置」按钮）：SOCKS5/HTTP 代理（含账号密码、Peer 连接走代理以保护 IP）、元数据获取超时、缓存目录、退出时清理缓存、立即清理缓存。代理与超时保存后立即生效（libtorrent `apply_settings` 热更新），缓存目录修改重启生效。设置持久化于 QSettings（Windows 注册表 `HKEY_CURRENT_USER\Software\Bitseed\MagnetViewer`）。
- **拖放与输入历史**：可直接把 `.torrent` 文件或磁力链文本拖入窗口（落点即解析）；输入框带自动补全，保留最近 15 条解析记录（置顶去重，持久化到 QSettings）。
- 预览可随时取消（自动释放下载配额）。

## 运行

### 一键启动（Windows）

双击 `start.bat`：首次运行自动创建虚拟环境并安装依赖，然后启动程序。

### 手动运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

依赖：Python 3.10+（已在 3.13 验证）、`libtorrent`、`PySide6`。

## 使用步骤

1. 粘贴磁力链（如 `magnet:?xt=urn:btih:...`）或点「打开种子文件…」，点「解析」；也可直接把 `.torrent` 文件或磁力链文本**拖进窗口**。输入框会按最近 15 条历史自动补全。
2. 在「文件列表」页查看文件树（目录层级 / 大小 / 占比）。
3. 双击视频文件 → 「预览」页内嵌播放器开始边下边播；双击图片 → 画廊浏览。
4. 预览页交互：
   - **拖动播放进度条**：播放与下载位置同步跳转（调度器从对应分块重新预约）；
   - **画廊切换图片**：未下载的图片会自动按需下载，完成后显示；
   - **停止预览**：随时停止下载与播放，释放该文件的优先级。
5. 状态栏实时显示做种数、连接数、速度与预览缓冲进度。

## 项目结构

```
magnet-viewer/
├── main.py               # 入口
├── core/
│   ├── parser.py         # bencode 编解码 + .torrent / magnet 解析
│   ├── fetcher.py        # libtorrent 会话管理（元数据获取、超时看门狗）
│   ├── scheduler.py      # 预览调度：单文件锁定 + 顺序分块 + 索引块优先
│   ├── stream_server.py  # 本地 HTTP 流服务（127.0.0.1 + Range）
│   ├── models.py         # 数据模型
│   └── config.py         # QSettings 持久化 + 代理/历史映射
├── ui/
│   ├── main_window.py    # 主窗口与线程桥接（含拖放与输入历史）
│   ├── file_tree.py      # 文件树
│   ├── preview_pane.py   # 预览容器（播放器/画廊切换）
│   ├── preview_player.py # 内嵌视频播放器
│   ├── gallery.py        # 图片画廊
│   ├── status_panel.py   # 状态面板
│   └── settings_dialog.py# 设置对话框（代理 / 超时 / 缓存）
├── smoke_test.py         # 无 GUI 冒烟测试（python smoke_test.py）
├── local_magnet_test.py  # 本机闭环验证：做种端 + 磁力链解析 + 边下边播（无需外网）
├── moov_stream_test.py   # moov 尾部优先端到端验证（ffprobe/ffmpeg 实际探测，无 GUI）
├── qt_stream_open_test.py# QMediaPlayer（FFmpeg 后端）offscreen 实测开播
├── gui_feature_test.py   # GUI 交互校验：拖放 / 输入历史 / 文件树展开（offscreen）
├── live_test.py          # 真实 DHT 磁力链验证（需能访问 BT 网络）
├── requirements.txt
└── start.bat             # 一键启动
```

## 验证状态

| 验证项 | 结果 |
|--------|------|
| `smoke_test.py`：解析 / **本地种子注入 cache_dir** / **路径穿越防护** / Range 流服务 / 前缀钳制 / **中文·特殊字符文件名往返** / 分块级可用性 / 尾部索引窗口 / **点播+等待** / **代理配置映射** / **会话启动参数** / 模块导入 | 通过 |
| `local_magnet_test.py`：磁力链 → 元数据 → 单文件顺序下载 | 通过（元数据 1.0s、info_hash 一致、900 KB 缓冲至 100%、磁盘字节数一致） |
| `moov_stream_test.py`（ffprobe/ffmpeg 实测） | 通过：A 仅头部→打不开（复现 moov not found）；B 头+尾+**按需补拉**→可探测；C 全量→可探测 |
| `qt_stream_open_test.py`（QMediaPlayer FFmpeg 后端 offscreen 实测） | 通过：A 仅头部→`FormatError`（即用户遇到的 moov atom not found）；B 头+尾+按需补拉→`LoadedMedia` 成功开播；C 全量→成功 |
| GUI 无头启动 | 通过（主窗口构造、会话与流服务启动、退出码 0） |
| `gui_feature_test.py`（offscreen 实测 24 项） | 通过：主窗口实例化 / 拖放接受·拒绝 / 输入历史（置顶去重、上限 15、持久化读回）/ 文件树（嵌套目录三级展开、无折叠、无 `.pad`、叶子数与可见文件数一致）/ **磁盘路径映射键为绝对路径且可命中** |
| `live_test.py`：公网 DHT | **沙箱内不可用** —— 该环境仅允许 HTTP(S) 走代理，BT/UDP 出站被屏蔽（`dht_nodes` 恒为 0）。请在正常 BT 网络下执行 `python live_test.py` 复核。 |

## 已修复问题

1. **`moov atom not found`（用户实测复现）**。根因：MP4 的 moov 在尾部，播放器探测发起后缀 Range 请求时该区间尚未下载，旧流服务直接回 **416**，FFmpeg 把 416 当致命错误。修复：流服务改为「**点播 + 等待**」——收到未就绪区间的请求时，先通过 `demand_cb` 通知调度器 `request_range()` 立即补拉这些分块，并挂起请求等待数据到达（默认最长 20 秒），超时才退化为 416/503。
2. **文件树"看不到文件"**。根因：`expandToDepth(0)` 只展开第 0 层，多文件种子常见的 `根目录/子目录/文件` 三级结构中二级目录保持折叠。修复：文件数 ≤3000 时 `expandAll()`，超大种子展开到第 2 层防卡顿。
3. **本地 .torrent 预览退化为「按完整静态文件服务」（静默功能失效）**。根因：`parse_torrent_file()` 从不设置 `ParseResult.cache_dir`（磁力链路径由 `_result_from_torrent_info` 注入，本地种子路径漏了），主窗口据此建立的「磁盘路径 → 文件」映射键退化为**相对路径**，而流服务回调传入的是绝对路径 → `_pieces_map()` 与 `_demand_range()` 全部查不到 → 分块可用性判定与按需补拉整体失效，预览把未下载的稀疏零数据直接喂给播放器（正是第 1 条修复的 moov 问题会原样复发）。修复：解析侧注入 `cache_dir`，主窗口改以自身 `cache_dir` 为准建表（双重保险）。**5 套测试原本都覆盖不到这条路径**——它们要么走磁力链、要么绕过主窗口直连流服务。
4. **本地流服务目录穿越（安全修复）**。两处缺陷：
   - 恶意种子可声明 `path: ["..","..","Windows","win.ini"]` 或绝对路径，旧代码在 `file_disk_path()` 中原样拼接，路径会逃出缓存目录。修复：新增 `core.models.safe_rel_path()`，逐级丢弃 `.`/`..`/空段、剥离盘符与根前缀、把分隔符与 Windows 非法字符替换为下划线；`parser` 与 `fetcher` 两条路径构造链路均已接入。
   - 流服务的越界校验用 `fp.startswith(root)`，会把**同前缀兄弟目录**误判为合法（`C:\...\cacheT` 与 `C:\...\cacheT_evil`）。实测证明旧校验对 `/../cacheT_evil/secret.mp4` 返回 True 并放行。修复：改用 `os.path.commonpath()` + `normcase` 的 `_is_within()`。

已验证环境：Python 3.13.12 + libtorrent 2.1.1 + PySide6 6.11.2（Windows）。

> 注意：libtorrent 2.1.x 已移除 `settings_pack`，改用 `lt.session(dict)` 配置（本项目已适配，2.0.x 同样兼容）。磁力链元数据依赖 `metadata_received_alert`，本项目显式设置了 `alert_mask` 订阅必要告警类别，并做到「单条告警处理异常不中断整批处理」。

## 已知限制（如实说明）

- **冷门资源**：磁力链必须存在在线 Peer 才能拿到元数据；0 做种资源会超时（默认 90 秒）。这是协议本质限制。
- **安全边界**：程序只对本地 127.0.0.1 提供服务，且所有请求路径受 `safe_rel_path()` + `_is_within()` 双重约束，不会读取或写出缓存目录之外的文件。但它仍是常规的 BT 客户端，元数据与分块来自不可信的 Peer——解析结果只用于展示，请勿据此直接打开或执行下载到的文件。
- **流式格式**：MKV 与 faststart MP4 体验最佳；moov 在尾部的普通 MP4 会先补拉尾部索引块（约 4 MB，开播慢几秒），索引就绪后即可边下边播；AVI/WMV 依赖系统解码器，可能无法播放。
- **边下边播会下载被预览的那个文件的分块**（不是整个资源）；「查看文件清单」仍然零下载。
- IP 暴露为所有 BT 客户端共性；可在「设置」中配置 SOCKS5/HTTP 代理（含账号密码、Peer 连接走代理）以隐藏真实 IP。代理仅在你主动配置时启用，不配置即直连。

## 免责声明

本工具与 qBittorrent 等客户端同为中性 P2P 工具。请勿用于获取受版权保护的资源，由此产生的法律责任由使用者自行承担。
