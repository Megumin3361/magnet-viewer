# 下载管理模块 · 核心任务模型与生命周期状态机（t1）

现状核实（行号依据）：单句柄 `SessionManager._handle`（`core/fetcher.py:57-63`），新解析 remove 旧 handle（`fetcher.py:158-170`）；`PreviewScheduler` 单文件锁定 + prioritize_files + moov 尾部 + deadline 点播（`core/scheduler.py:103-180`），stop 撤 auto_managed（`scheduler.py:188-207`，audit P1-9）；`active_downloads=1`（`fetcher.py:82`）；元数据看门狗 90s（`fetcher.py:20,361-370`）；分块级可用性（`core/models.py:133-160`）；画廊按需单文件（`ui/gallery.py:16,141`）；缓存守卫标记文件（`core/cache_guard.py:16,48-71`）。

## 1. 任务粒度决策
**【MVP】种子级任务**【增强】文件级勾选。理由：①优先图书馆在句柄级（`scheduler.py:109-114`），文件勾选只是同一句柄优先级向量合并（下载 1~3 / 不选 0），无需拆任务；②元数据获取天然以种子为单位（`fetcher.py:232-251`）；③任务与预览共用句柄才能复用已下载分块，避免双调度打架。

## 2. DownloadTask 字段（★=持久化）
`id★`（info_hash 去重）、`source★`（磁力链/torrent/预览转下载）、`info_hash★ / name★ / total_size`、`files★`（TorrentFile 序列化）、`selected★`（勾选文件集）、`state★ / priority★ / save_path★`、`error / retries★`（错误 UI 可见，吸取 audit P1-4）、`created_at★ / finished_at★`；速度/进度/ETA **不持久化**——派生自 `handle.status()`（`fetcher.py:294-330`），重启后校验磁盘恢复。载体【MVP】QSettings JSON（同 `config.py:70-86` 模式）；【增强】SQLite + resume data（交 backend）。

## 3. 生命周期状态机
`QUEUED → META_FETCH → VALIDATE → DOWNLOADING ⇄ PAUSED → COMPLETED → SEEDING/STOPPED`；终态 `FAILED / DELETED`。

| 迁移 | 条件与清理语义 |
|---|---|
| QU→MF | 轮达执行位（受全局并发约束） |
| MF→VALIDATE | metadata_received_alert 归属校验后（沿用 `fetcher.py:345-349` 反竞态） |
| MF→FAILED | **看门狗按任务独立倒计时**（现为全局单数 `fetcher.py:361-370`）；停 handle，可手动重试 |
| VA→DOWNLOADING | 磁盘预检（总大小×1.1）+ 路径冲突检查通过 |
| DL⇄PAUSED | 用户暂停/恢复；恢复须重设 auto_managed（`scheduler.py:198-201` 教训） |
| DL→COMPLETED | total_done≥total_size 且校验通过 |
| CP→SEEDING | 【远期】开做种；【MVP】维持 upload_mode 不做种（`fetcher.py:189,240`）；做种超时/手动停止→STOPPED |
| →STOPPED | 手动停止：保留文件分块，可恢复 |
| →DELETED | 删任务；选删文件时经 `guard_ok_for_cleanup`（`cache_guard.py:66-71`），只删受管缓存内本任务文件 |

## 4. 队列与调度
- 全局并发【MVP】：沿用 libtorrent 队列，`active_downloads` 1→可配置 N（`fetcher.py:82`），QUEUED 由 auto_managed 驱动；配置入口进设置面板【增强】。
- 优先级【MVP】：priority 映射 torrent_priority；暂停/恢复用 pause + auto_managed 组合防自动续传（`scheduler.py:188-207`）。
- **预览抢占**【MVP】：预览=最高优先级。同种子：预览文件 4 / 任务文件按优先级，**合并优先图书馆**，deadline 只作用于预览文件（原机制不动，`scheduler.py:158-180`）；不同种子：提升预览 handle 的 torrent_priority 抢带宽。
- **同种子协同**【MVP】：任务与预览共用句柄 → `have_piece` 天然共享（`fetcher.py:268-278`）；「预览转下载」已落盘分块零额外下载直接转正。

## 5. 错误与重试
| 错误 | 处理 | 级别 |
|---|---|---|
| 元数据超时/无做种 | FAILED+error 可见；手动重试；自动重试 ≤2 次指数退避 | MVP/增强 |
| 磁盘不足 | VALIDATE 预检拦截 + storage alert → FAILED | MVP |
| 文件冲突（同 info_hash 已存在） | 校验分块后复用续传 | 增强 |
| 校验失败 | libtorrent 丢块重下；持续失败 → FAILED 保留进度 | 增强 |

## 6. 架构冲突与改造
多任务**必然重构 SessionManager**：`_handle` 单值（`fetcher.py:57-63`）→ `dict[info_hash→handle]` + 任务注册表；"解析即换代"（`fetcher.py:158-170`）→ "追加任务"；`PreviewScheduler` 句柄参数化（`scheduler.py:52-57`）。stream_server 保持现状（仍服务当前预览种子），多任务按选中任务定向预览【增强】。