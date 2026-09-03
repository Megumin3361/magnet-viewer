# 下载管理模块 · 功能树（团队规划汇总）

> 来源：magnet-audit 团队四成员并行规划（t1 任务模型 / t2 后端集成 / t3 UI / t4 验收）
> 分篇：《plan/t1_download_task_model.md》《plan/t2_libtorrent_persistence.md》《plan/t3_download_ui_tree.md》《plan/t4_acceptance_plan.md》
> 状态：**规划完成，待用户确认决策点后进入开发**（本轮未写业务代码）

---

## 〇、模块定位

在现有「解析 → 文件树 → 单文件边下边播预览」之上增加**多任务下载管理**：把磁力链/种子添加为持久化下载任务，支持队列调度、暂停/恢复、进度展示、重启续传，并与预览/画廊共用 libtorrent 句柄复用已下载分块。

**架构红线**（所有方案一致）：`SessionManager` 单句柄（`core/fetcher.py:57-63`）必须改造为任务注册表；`resolve/status/start_preview/connect_peer` 等 6 组接口契约保持不变，7 套旧测试全绿作为兼容断言。

**实施状态：MVP 已完成并通过验收（t1/t2/t3/t4/t5/t6/t7）**。验收证据：
- `python download_mgr_test.py` → **OK 68 项 / FAIL 0，退出码 0**（MVP 7 项 + 增强 2 项 + 边界 5 条全过）；
- `python regression_run.py` → **7 套旧测试全绿**（兼容契约未破）；
- 验收细节：`plan/t4_acceptance_plan.md` 矩阵逐项勾选见下；实断言实现位于 `download_mgr_test.py` §2~§10。

## 一、功能树总览

| 分支 | 子功能（◆MVP ◇增强 ○远期） |
|---|---|
| **A 任务模型/状态机** | A1☑◆种子级任务 + 文件级勾选（文件级勾选为◇）；A2☑◆`DownloadTask` 模型（进度派生不落盘，taskstore 序列化）；A3☑◆8 状态状态机（QUEUED→META_FETCH→VALIDATE→DOWNLOADING⇄PAUSED→COMPLETED→STOPPED/SEEDING，终态 FAILED/DELETED，fetcher STATE_*）；A4☑◆per-task 元数据看门狗（90s 从全局单计时改按任务，验收 §10-B2）；A5◇预览抢占=最高优先级（同种子合并优先图书馆、异种子提 torrent_priority）；A6◇同种子任务与预览复用分块（"预览转下载"零额外下载——转正路径已实现 D10，验收 §2 回调一致性） |
| **B 后端/持久化** | B1☑◆`_torrents: dict[ih→Task]` 注册表增量改造（`_handle` 降级为当前任务别名）；B2☑◆任务列表 JSON（`<cache>/.tasks.json` 原子写 + 损坏容错，`core/taskstore.py` 纯函数）；B3☑◆fastresume（暂停/退出/60s 周期脏标记，启动注入，**损坏静默降级全新加入**——验收 §10-B4）；B4☑◆目录隔离 `downloads/<ih>/` + `.preview/<ih>/`（复用 cache_guard，清理只碰 .preview）；B5☑◆会话配置可配（连接数/并发 active_downloads=3 defaultValue；带宽为◇ 会话级 rate_limit，验收 §9）；B6◇dht_state 存取加速重启引导；B7◇元数据缓存（`.metadata/<ih>.torrent` 免 DHT 直启） |
| **C UI** | C1☑◆三页签（TAB_DOWNLOADS=2）+ QSplitter 任务列表/详情；C2☑◆4 个添加入口（顶栏「添加下载」按钮/文件树右键/预览页「转下载」/拖拽）；C3☑◆QTreeView 四列（名称/大小/进度/速度·ETA）+ delegate 进度条 + emoji 状态色 + 右键菜单（暂停/恢复/删除/优先级/打开目录/打开预览）；C4☑◆添加确认 QDialog（save_subdir/优先级/做种）；C5☑◆失败 tooltip+双击弹窗（错误可见，沿用 audit P1-4 教训）；C6☑◆StatusPanel 总下载速度；C7☑◆设置新增（默认并发数/下载目录/完成后做种开关）；C8◇完成通知（托盘）、未完成文件双击提示不切预览、过滤排序；C9◇磁盘满预检 |
| **D 验收/边界** | D1☑◆验收矩阵 10 项（自动化判定复用本机做种闭环 + 磁盘字节快照断言暂停/恢复——`download_mgr_test.py` 68 项 OK）；D2☑◆边界 9 条（重复 hash/磁盘不足/同名冲突/看门狗按任务/退出未完成持久化/resume 损坏/缓存被清——验收 §10 全过；磁盘不足与两任务同文件为◇）；D3☑◆新增 `download_mgr_test.py`（退出码 0/1/2 约定）；D4☑◆回归契约 6 组 + 7 套旧测试全绿（`regression_run.py` 一键回归） |

## 二、待用户确认的决策点（合并去重，全部带推荐）

| # | 决策点 | 推荐 | 理由 |
|---|--------|------|------|
| D1 | 任务粒度 | **种子级**（文件级勾选后续增强） | 优先图书馆在句柄级、元数据按种子获取；文件勾选只是优先级向量合并，无需拆任务 |
| D2 | 默认并发下载数 | **3** | 现有 active_downloads=1 仅为预览保速；下载场景放宽到 3，可配置 |
| D3 | 任务完成后行为 | **默认自动停止**（可配置做种） | 本工具定位"只收不做种"（README 已声明）；做种超出定位 |
| D4 | 下载目录 | **允许任意位置**（默认 cache_dir/downloads） | cache_guard 只管清理入口（防误删），落盘另有 safe_rel_path 防穿越，两者无冲突 |
| D5 | 带宽/连接限制 | **增强期做**（会话级 download_rate_limit） | 无需架构改动，纯配置项 |
| D6 | 任务列表持久化载体 | **JSON 存 cache_dir**（`.tasks.json`） | 否决 QSettings：注册表膨胀 + 测试污染（gui_feature 已刻意隔离） |
| D7 | 目录布局命名 | **`downloads/<ih>/` 与 `.preview/<ih>/`** | 现状平铺 cache_dir 无法多任务共存；语义清晰、隔离预览缓存与用户数据 |
| D8 | 清理语义 | **「立即/退出清理」只清 .preview/，不动 downloads/** | downloads 属用户下载数据；完成文件默认保留 |
| D9 | 删除任务时 | **删任务断句柄+选项删文件**；删文件经 cache_guard 只删受管缓存内本任务文件 | 防误删用户数据（对齐 audit P1-2 守卫） |
| D10 | 预览页「转下载」 | **纳入 MVP**（零额外下载直接转正为任务） | 与下载任务共用句柄，实现成本低、体验闭环完整 |

## 三、建议实施顺序（MVP 依赖序）

1. ✅ **去单句柄改造**：`_torrents` 注册表 + `_handle` 别名 + per-task 看门狗（7 套旧测试保持全绿）
2. ✅ **任务持久化**：`.tasks.json` + fastresume 三时机 + 启动恢复流程（`core/taskstore.py` 纯函数）
3. ✅ **目录隔离**：downloads/`<ih>` 与 .preview/`<ih>`（local_torrent/single_file 落盘断言已随布局更新，接口未变）
4. ✅ **任务操作**：添加（4 入口）/列表/暂停/恢复/删除/去重
5. ✅ **UI**：三页签 + 任务列表（C1/C3）+ 确认流（C4）+ 错误可见（C5）+ StatusPanel 速度 + 设置三项
6. ✅ **`download_mgr_test.py`**：验收矩阵 MVP 7 项 + 增强 2 项 + 边界 5 条（68 项 OK / 0 FAIL，退出码 0）+ 7 套旧测试回归（`regression_run.py` 全绿）
7. ◻ **增强**：并发队列（已按 active_downloads=3 默认落地，进一步调度策略）/断线重连/磁盘不足恢复/限速（会话级 rate_limit 已验收，UI 配置入口待做）/托盘通知/文件级勾选/元数据缓存
8. ◻ **远期**：做种、速度曲线、RSS、归档

## 三·五、验收结果记录（2026-09-03，t7）

| 验收段 | 断言数 | 结果 |
|---|---|---|
| §2 添加磁力链 → 下载中（MVP1/2） | 12 | ✅ |
| §3 暂停：5s 磁盘字节快照不变（MVP3） | 5 | ✅ |
| §4 恢复：字节续增、进度单调不回退（MVP4） | 5 | ✅ |
| §5 删除任务：handle 移除+目录释放+重添无残留（MVP5） | 8 | ✅ |
| §6 退出重启续传：.tasks.json+fastresume 读回、上传增量证不重下（MVP6） | 9 | ✅ |
| §7 完成：落盘字节=声明值、state=COMPLETED（MVP7） | 4 | ✅ |
| §8 多任务并发：2 种子同时下载均 100%（增强1） | 4 | ✅ |
| §9 带宽限制：download_rate ≤ 设定值 ±20%（增强2） | 4 | ✅ |
| §10 边界：重复 hash / per-task 看门狗 / 防穿越 / resume 损坏重建 / 缓存被清 | 15 | ✅ |
| 合计 | **68** | **0 FAIL，退出码 0** |

回归：`python regression_run.py`（smoke / local_magnet / local_torrent / single_file / gui_feature / moov / qt）——**全部通过**。

验收中定位并修复一处缺陷：`core/fetcher.py::_restore_task` 对损坏 fastresume 原实现会抛错并把任务标记 FAILED，违反 B3「损坏静默降级全新加入、绝不阻断启动」语义（模块 docstring 已声明）。已改为损坏时丢弃 resume data 走全新加入路径（契约不变）。

## 四、风险提示

- 目录布局变更会触碰 local_torrent/single_file 的落盘断言（接口不变、断言更新）；
- alert 归属校验从 `handle==self._handle` 改为按 info_hash 查 map，需要防"已移除任务迟到告警"；
- 预览与下载的带宽竞争：MVP 以"预览最高优先级"保证边下边播体验不回归。