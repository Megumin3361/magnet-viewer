# 安卓端适配 · 可行性结论与分阶段实施计划

> 生成：2026-09-10 · 基于对 magnet-viewer 当前架构（桌面 Windows）的实读 + 官方文档与社区实践检索
> 结论先行：**「功能 100% 平移」不可达，但 90% 功能可达——代价是核心引擎替换 + 三项功能重做**

---

## 一、可行性侦察结论（已实证/已检索，非猜测）

### 1.1 技术栈逐层判定

| 层 | 桌面现状 | Android 可行性 | 判定 |
|---|---|---|---|
| Python + PySide6 UI | PySide6 6.11 桌面 | **可行但有硬约束**：官方 `pyside6-android-deploy` 存在且活跃（6.8+ 支持好，arm64-v8a 一级支持；armeabi-v7a/x86 需自行交叉编译 wheel）；仅 **Linux/macOS 主机构建**，Windows 主机不可行；Python 必须 3.10/3.11 | ✅ 路线存在 |
| **libtorrent Python 绑定** | pip 装 2.1.1 | **PyPI 无 Android wheel**；需 NDK 交叉编译（boost + openssl + libtorrent + swig 绑定，社区屡试屡败的经典坑），或放弃 | ❌ **最大风险点** |
| 本地 HTTP 流服务 | `http.server` + 127.0.0.1 | Android 允许 app 内 localhost TCP，但 QMediaPlayer 访问 127.0.0.1 需 `usesCleartextTraffic` 配置；可行 | ✅ |
| QMediaPlayer 边下边播 | FFmpeg 后端 | **Qt 官方文档确认 FFmpeg 是 Android 默认后端**；但实测坑：HTTPS 流需手动配 OpenSSL stub、部分 MP4/流报 Invalid data（社区普遍反映 Android 上 QMediaPlayer 兼容性劣于桌面），且**本应用核心玩法——Range 流服务 + 分段缓冲进度条——依赖 QMediaPlayer 的 Range 请求行为**，Android 后端是否同样发渐进 Range 请求未经验证 | ⚠️ 需真机验证 |
| QSettings 注册表 | Windows 注册表 | QSettings 在 Android 自动落到 app 私有目录的 ini，代码零改动 | ✅ |
| 主题/布局 | 桌面窗口 1040×700 | **必须重做**：手机屏是竖屏触摸，文件树/三页签/双击交互在手机上不可用 | ❌ UI 层大改 |
| 下载管理/任务持久化 | `.tasks.json` + fastresume | 纯 Python + libtorrent 语义，随引擎层走 | ✅（跟随引擎） |

### 1.2 libtorrent 引擎的三条候选路线

| 路线 | 内容 | 成本 | 风险 |
|---|---|---|---|
| **A. NDK 交叉编译 python-libtorrent** | NDK r27c 工具链编 boost/openssl/libtorrent/swig 绑定 → `.so` 塞进 APK | **极高**（社区 10 年反复踩坑，无官方支持；libtorrent 官方讨论区确认 py3.13 绑定构建已是问题） | 高：构建成功率、长期维护 |
| **B. 换 jlibtorrent（FrostWire）** | 成熟的 Java/SWIG 绑定，**官方发布 Android 四架构 natives**（Maven: `com.frostwire:jlibtorrent-android-arm64`），FrostWire for Android 生产在用 | 中：core 引擎层用 **Pyjnius** 从 Python 调 Java | 中：API 语义与 python 绑定略有差异（但同 libtorrent 内核），需适配层 |
| **C. 桌面/安卓双端架构** | 桌面版保留现状；安卓端 core 用 jlibtorrent（Pyjnius）+ PySide6 Android | 中高 | 中 |

**推荐 B/C（同一件事）：安卓端用 jlibtorrent via Pyjnius。** 理由：FrostWire 团队持续维护（2.0.12.9，2025 年仍活跃发版）、Android natives 官方预编译、生产验证（FrostWire/LibreTorrent），避免把项目拖入 NDK 编译泥潭。桌面版 core 不动。

### 1.3 「功能完全」的真实边界（必须先对齐预期）

| 功能 | Android 命运 |
|---|---|
| 解析/文件树/画廊/下载管理/暂停恢复/续传/限速/做种开关/日志 | ✅ 可保真 |
| **边下边播**（核心卖点） | ⚠️ 引擎换 jlibtorrent 后调度/流服务逻辑可移植，但 **QMediaPlayer Android 后端的 Range/渐进请求行为必须真机验证**——这是「功能完全」命题里唯一无法纸面保证的点；若不达标，备选是 Android 原生 MediaPlayer/ExoPlayer 桥接（UI 层再抽象一层） |
| 拖放输入 | 手机无拖放 → 改「文件选择器 + 剪贴板粘贴」（语义等价） |
| 双击交互 | 触摸屏 → 单击/长按菜单重映射 |
| 窗口布局 | 竖屏重排（文件树→列表、三页签→底部导航/抽屉） |

**结论：功能语义 100% 保留可达；交互形态必然变化；边下边播的流畅度需真机实证后才能承诺。**

---

## 二、分阶段实施计划（每阶段有可验证出口）

### Phase 0 — 真机/模拟器可行性试点（1 个里程碑，决定整个方向）

> 目标：用最小代价验证两大生死问题，失败则止损、成功则全速。

- [ ] P0-1 Linux 构建环境（WSL2 或 CI）：SDK/NDK r27c + Python 3.11 venv + `pyside6-android-deploy` 依赖
- [ ] P0-2 **Hello-Android-Qt**：空 PySide6 窗口 APK，装上模拟器能显示
- [ ] P0-3 **jlibtorrent 探针 APK**：Pyjnius 桥 jlibtorrent-android-arm64，跑「会话启动 + `lt.session` 等价配置 + 磁力链元数据获取 + 种子生成/解析」四项探针（对齐此前桌面依赖探针的方法论）
- [ ] P0-4 **QMediaPlayer Range 行为验证**：本地 HTTP 流服务 + 分块位图，真机播放一个边下边播种子，验证渐进 Range 请求与缓冲条行为是否与桌面一致
- [ ] **决策门**：P0-2/3/4 全绿 → 继续 Phase 1；任一红 → 写结论报告，改推「桌面完善 + 远期重评」

### Phase 1 — core 引擎抽象层（桌面先重构，风险最低）

> 原则：**先在桌面把引擎隔离出来，再接第二实现**——安卓失败也不伤桌面。

- [ ] P1-1 `core/engine.py` 抽象基类：把 fetcher 对 libtorrent 的调用面（session/start/add_torrent/apply_settings/pause/resume/set_priority/prioritize_files/save_resume/read_resume/piece ops/alerts）收敛为接口
- [ ] P1-2 `core/engine_lt.py`：现有 python-libtorrent 实现搬入（桌面默认，行为零变化，10 套回归全绿）
- [ ] P1-3 `core/engine_jlibtorrent.py`：Pyjnius 实现，逐 API 对拍（同一磁力链/种子在两引擎下 info_hash、piece 位图、resume 字节语义一致性单测）
- [ ] P1-4 纯 Python 单测铺两引擎跑（对拍矩阵）

### Phase 2 — UI 适配层

- [ ] P2-1 响应式布局：检测 `QtAndroid`/屏幕尺寸，竖屏用 `BottomNavigation`/抽屉替代三页签；文件树→可展开列表（触摸行高）
- [ ] P2-2 交互重映射：双击→单击进入/长按菜单；拖放→系统文件选择器 + 剪贴板磁力链
- [ ] P2-3 主题沿用 `ui/theme.py`（暗色 QSS 跨平台）+ 触摸滚动/安全区适配
- [ ] P2-4 流服务：`127.0.0.1` 照旧 + manifest `usesCleartextTraffic="true"`（仅 localhost 语义说明写入文档）

### Phase 3 — 打包与 CI

- [ ] P3-1 `pyside6-android-deploy`/buildozer.spec 入库（含 jlibtorrent natives、Pyjnius 依赖清单）
- [ ] P3-2 CI 增加 android job（ubuntu runner，arm64-v8a debug APK 产物 + artifact）
- [ ] P3-3 版本策略：desktop 与 android 共用 core，tag 双产物

### Phase 4 — 验收

- [ ] P4-1 桌面 10 套回归全绿（引擎抽象零回归）+ 安卓探针矩阵全绿
- [ ] P4-2 真机验收清单：解析→清单→点图→点视频（边下边播+拖动+缓冲条）→下载管理全流程→退出续传
- [ ] P4-3 文档：README「运行（Android）」节 + 已知限制

---

## 三、需要你现在拍板的事

1. **接受「交互形态改变、边下边播待真机验证」这个边界吗？**（「功能完全」在此定义下才可达）
2. **构建主机**：你有 Linux/macOS 环境或愿意用 WSL2/CI 吗？（Windows 主机无法出包）
3. **引擎路线**：认可 jlibtorrent-via-Pyjnius 吗？（替代方案是 NDK 自编 python-libtorrent，成本高一个量级）
4. **预算形态**：Phase 0 只做试点验证（1 个里程碑）；要不要先只做 Phase 0 再决定是否继续？
