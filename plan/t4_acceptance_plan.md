# 下载管理模块 · 验收标准与边界清单（QA 规划）

依据：8 套测试按数据入口路径铺排（README.md:91）；退出码 0/1/2（README.md:90）；自动化判定复用 `local_magnet_test.py` 本机闭环（做种端 :52，resolve+connect_peer :86-87）。新增验收脚本 `download_mgr_test.py`（≤2 个 seed_mode 做种端 + 1 个多任务 SessionManager，退出码同约定）。

## 一、验收标准矩阵（#MVP / 增强 / 远期）

| 子功能 | 可断言验收点（自动化判定） | 阶段 |
|---|---|---|
| 添加磁力链 | on_metadata 回调且 info_hash 与做种端一致、文件数正确（仿 local_magnet_test.py:104-108） | MVP |
| 元数据→下载中 | start_preview 后 `status()["state"]=="downloading"`（fetcher.py:306）、total_done 单调增长 | MVP |
| 暂停 | 暂停后 5s 内缓存目录磁盘字节快照不变 | MVP |
| 恢复 | 字节续增；已有点不重下（piece 下载序不回退） | MVP |
| 删除任务 | handle 移除、该 hash 目录释放；重添同 hash 无残留续传 | MVP |
| 退出重启续传 | 重建 SessionManager 后任务列表与进度恢复（仿 config.py:70 recent 持久化读回） | MVP |
| 完成 | 全部落盘字节=声明值、state==finished | MVP |
| 多任务并发 | 2 种子同时下载均达 100%（active_downloads 提至 2+，fetcher.py:82） | 增强 |
| 带宽限制 | download_rate ≤ 设定值 ±20% | 增强 |
| 队列/优先级 | 按序调度、永不饿死 | 远期 |

## 二、边界与异常清单

- 重复添加同 info_hash：拒绝并提示（断言 add 两次 handle 数不变）【MVP】
- 磁盘空间不足：任务标记 error，不崩溃【MVP】
- 同名/路径冲突：safe_rel_path（models.py:89）仍防穿越；同名自动改名或挂起（决策 D3）【MVP】
- 元数据超时：现为全局单计时（fetcher.py:361 `_resolve_started`）——多任务须按各自 add_torrent 时刻计 90s，互不拖累【MVP】
- 退出时未完成：进度清单持久化（QSettings JSON，仿 recent config.py:70-86）【MVP】
- 做种端中途下线：停滞不崩、恢复后可续【增强】
- resume data 损坏：静默重建、从头校验，不崩溃【增强】
- 缓存目录被外部清理：缺失分块重下，不 404 崩溃【增强】
- 两任务含同一文件：同 hash 内容共享落盘；异 hash 同名防互相覆盖【增强】

## 三、回归风险评估（单句柄→多任务）

单句柄根因：`_handle` 单值（fetcher.py:57）、`_reset_current` 每次 resolve 移除旧 handle（:158-170）、active_downloads=1（:82）、告警归属 `a.handle==_handle`（:348）。改造=handle→task 映射化，下列契约必须保持（7 套依赖测试 local_magnet/local_torrent/single_file/gui_feature/moov/qt/smoke，README.md:80-87）：

- `resolve(source)` + on_metadata/on_error 回调（fetcher.py:144,48-49）
- `status()→dict` 既有字段（:294-330；UI 轮询 main_window.py:308,343）
- `start_preview(f)`/`stop_preview()`（:255-263）；`connect_peer(ip,port,wait_handle=5.0)` 等待句柄语义（:206-230）
- scheduler `begin(handle,f)`、`request_range` 幂等（scheduler.py:103,123）
- StreamServer pieces_cb/demand_cb 磁盘路径回调（main_window.py:63,285,300）
- 单文件落盘不套前缀（fetcher.py:403）

**兼容断言**：7 套旧测试全绿=契约未破；多任务新测试须保证单任务路径行为=旧行为特例。

## 四、开放决策点（推荐+理由）

- **D1 任务粒度**：种子级（推荐）——去重/落盘/status 均以 info_hash 为键，文件级状态机成本高。
- **D2 默认并发数**：3（推荐）——现 active_downloads=1 仅为预览保速（fetcher.py:82），下载场景应放宽。
- **D3 完成后行为**：默认自动停止（推荐）——本工具只收不做种（fetcher.py:73），做种超出定位；做成设置项。
- **D4 下载目录**：允许任意位置（推荐）——cache_guard 只约束清理入口（cache_guard.py:66），落盘另有 safe_rel_path 防穿越，无冲突；默认仍 cache_dir。
- **D5 带宽限制**：增强期做，会话级 `download_rate_limit` 即可，无需架构改动。
- **D6 任务列表入 QSettings**：存轻量摘要 JSON（仿 recent，config.py:70）；resume data 归 libtorrent 管理。

## 五、分阶段交付

- **MVP**（依赖序）：去单句柄改造 → 添加/列表/删除 → 暂停/恢复 → 重启续传 → 去重 → 7 套回归 + download_mgr_test.py。
- **增强**：并发+队列 → 断线重连 → 磁盘不足恢复 → 限速 → 完成行为设置。
- **远期**：文件级选择、做种、速度曲线、RSS。