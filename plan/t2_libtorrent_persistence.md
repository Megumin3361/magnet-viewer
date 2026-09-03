# t2 libtorrent 多任务集成与持久化方案（backend）

现状核实：`SessionManager` 单 handle 架构经代码确认——`core/fetcher.py:57` `_handle`；`:158-170` `_reset_current` remove 旧句柄+gen 换代；`:348/:351` alert 按 `a.handle == self._handle` 归属；`:361-370` 全局超时看门狗；`:74-83` start 配置（enable_dht=True、enable_upnp=False、connections_limit=300、active_downloads=1）；`:188/:239` save_path 平铺 cache_dir；`:183/:425` cache_dir 注入。守卫：`cache_guard.py:48-63` 标记文件、`:66-72` 清理校验。清理入口：`main_window.py:178-187`、`:358-367`。

## 1. 多任务会话架构（增量改造，维持接口契约）

- 【MVP】**任务注册表**：`_torrents: dict[info_hash_hex, Task]`（Task={handle, result, gen, resolving, resolve_started}），`_handle` 降级为"当前任务"别名供预览/状态路径零改动。
- 【MVP】**解析预览与下载共存**：`resolve()` 不再 remove 旧句柄——旧任务在下载则留 map 继续；仅查看清单则 pause+upload_mode 保留。`_reset_current` 拆为 `_detach_current`（保留）与 `_abort_current`（remove），gen 代次防竞态保留。
- 【MVP】**alert 归属**：`_alert_loop` 改按 `a.handle.info_hash()` 查 map，查不到=已移除任务，丢弃；metadata_received_alert 回调携带任务标识。
- 【MVP】**per-task 看门狗**：遍历 map 中 resolving 任务各自判超时（替代全局单点），超时任务独立 pause+on_error，互不影响。
- 【增强】纯 v2/no-btih magnet 用临时 task_id 兜底匹配；【远期】统一会话与临时会话可切。

## 2. 任务持久化

- 【MVP】**任务列表**：JSON 存 cache_dir（如 `.tasks.json`），不用 QSettings——避免注册表膨胀且测试污染（gui_feature_test 已刻意不污染）。字段：info_hash/name/added_at/state/save_subdir/完成标志。写时机：增删、状态切换、closeEvent（已有 hook `main_window.py:358`）。
- 【MVP】**resume data**：`save_resume_data()` + 监听 `save_resume_data_alert`，写 `<cache_dir>/.resume/<ih>.fastresume`（bencode）。时机：暂停/退出批量、周期 60s 节流（仅脏任务）。
- 【MVP】**恢复流程**：启动读 .tasks.json+fastresume → `resume_data` 注入 → pause+`check_files`（libtorrent 按位图跳过已验分块）；解析失败（版本演进）静默降级全新加入，绝不阻断启动。
- 【增强】元数据缓存：磁力链任务获元数据后写 `.metadata/<ih>.torrent`，重启免 DHT 直启；【远期】跨版本迁移。

## 3. 缓存/磁盘联动

- 【MVP】**目录隔离**：下载 `cache_dir/downloads/<ih>/`，预览解析 `cache_dir/.preview/<ih>/`（现状平铺 `fetcher.py:188/:239` 必须隔离）。`ParseResult.cache_dir` 语义不变（smoke_test.py:96 断言不受影响），新增 `save_subdir` 由 SessionManager 注入，main_window 拼路径建 `_path_to_file`（:220-224）。⚠️ local_torrent/single_file 测试的落盘断言需随布局同步更新（接口不变）。
- 【MVP】**复用 cache_guard**：守卫维持根标记；「立即清理/退出清理」只清 `.preview/`，downloads 属用户数据不碰。
- 【增强】**磁盘空间**：`shutil.disk_usage` 周期检查+预留 reserve（默认 500MB 可配），低于阈值暂停新下载并提示。
- 【MVP】**完成任务去留**：`torrent_finished_alert` → completed；默认留在 downloads/（与"退出清理"解耦——现状 `clear_cache_on_exit` 只应清预览）；【远期】完成后移出受管目录/归档。

## 4. 会话级资源管理

- 【MVP】**可配置**：config.py DEFAULTS(:10-20) 增 `connections_limit/active_downloads/download_rate_limit/upload_rate_limit`，沿用 `apply_proxy` 热更新路径（fetcher.py:115-122）。
- 【MVP】**dht_state**：启动/退出 `session.load_state()/save_state()` 存 `<cache_dir>/.session.state`，加速重启 DHT 引导。
- 【MVP】**队列策略**：active_downloads 交 libtorrent 内置队列；预览任务 unset auto_managed+手动 resume（scheduler.py:112 已如此）保证不被队列饿死；普通下载 auto_managed 排队。
- 【MVP】**预览优先级注入**：预览任务标记 previewing，active_downloads=1 时新任务排队。`have_piece/piece_length` 增 info_hash 参数（main_window._pieces_map :285-298 按映射记录的 ih 查对应 handle）。

## 5. 可测试性

- 【MVP】**纯函数新模块**：`core/taskstore.py`（JSON 读写）、`core/resume.py`（fastresume 序列化，用 lt.bencode 无会话依赖），补齐 README.md:112"纯函数测试缺失"。
- 【MVP】**契约保持**：构造/start/resolve/connect_peer/start_preview/status/on_* 全不变——8 套测试依赖点：smoke_test.py:84-93、local_magnet_test.py:82-117、local_torrent_test.py:93-136、single_file_test.py:77-103、live_test.py:50-53、gui_feature_test.py:179。
- 【增强】**多任务集成测试**：复用 local_magnet_test 做种端基建——双磁力链并发解析、超时互不影响、恢复后位图一致。

**开放决策点**：① preview/downloads 目录布局命名（需 ui 与 qa 会签）；② 任务 JSON 与 resume 的同步原子性（先 resume 后 JSON 的 crash 窗口）；③ downloads 是否纳入清理语义需产品确认。