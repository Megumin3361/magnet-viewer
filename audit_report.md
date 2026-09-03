# magnet-viewer 团队全面审查报告（magnet-audit）

> 审查形式：AgentTeams 四成员并行审查（架构 / 安全 / UI/UX / 测试与工程实践）
> 审查基线：磁盘当前代码 + git（HEAD=`91433ea`）+ REVIEW.md（2026-09-03 轮审查与修复记录）
> 重要背景：工作区存在**未提交的半成品改动**——`core/fetcher.py`（M）与 `core/logutil.py`（??）是 P1-2 日志改造的中间状态，这是本报告 P0 的直接来源。
> 方法：全源码通读 + git 基线核对 + 多项实证（libtorrent file_path 语义、v2 hash 分支、代理直连分支、路径穿越边界、NameError 复现）+ smoke_test 复跑全绿。

---

## 〇、修复进度（2026-09 先行修复轮，已按本报告执行并全量回归）

| 项 | 状态 | 说明 |
|----|------|------|
| P0-1 fetcher `log_exception` 未导入 | ✅ 已修 | 补 import；fetcher 全部 14 处 except 分级接入 logutil（log_exception/log_warning） |
| P1-2 日志改造收尾 | ✅ 已修 | scheduler 6 处、stream_server（availability/demand）接入；main_window 缓存/清理路径接入 |
| P1-1 流服务鉴权 | ✅ 已修 | 每会话随机 token（URL 参数 `?t=`）+ Host 白名单校验 + `Cache-Control:no-store`/`X-Content-Type-Options:nosniff`；smoke [2b2] 断言无 token/伪造 Host → 403 |
| P1-2 缓存清理守卫 | ✅ 已修 | 新增 `core/cache_guard.py`：拒绝盘符根/用户数据目录 + 受管标记文件 `.magnet_viewer_cache`；fetcher/main_window/settings_dialog 三处接入；保存/清理/退出三入口校验 |
| P1-3 播放按钮状态机 | ✅ 已修 | 三态管理（等待/停止禁用），删除死代码 `btn_pause_text` |
| P1-4 播放器错误可见性 | ✅ 已修 | 错误显示到标题区（红色 ⚠）+ 重试耗尽醒目提示 |
| P1-5 画廊完成自动显示 | ✅ 已修 | 当前浏览行下载完成后立即重载大图 |
| P1-6 解析完成自动下载首图 | ✅ 已修 | `set_result` 用 blockSignals 定位首图，浏览动作才触发下载 |
| P1-7 代理空主机静默裸连 | ✅ 已修 | 保存时校验 type+host 组合，缺失弹警告并阻止保存 |
| P1-8 流服务回调异常静默降级 | ✅ 已修 | 回调异常视为「可用性未知」→ 503 + 日志，绝不降级为整文件可用 |
| P1-9 stop 未撤 auto_managed | ✅ 已修 | stop 时 unset auto_managed（防队列自动续传） |
| P1-10 连续解析 handle 归属竞态 | ✅ 已修 | alert 处理按 `a.handle == self._handle` 过滤旧会话告警；.torrent 后台解析带代次（gen）自弃 |
| P1-11 本地 .torrent 解析卡 UI 线程 | ✅ 已修 | 解析移入后台线程（daemon），带代次防旧任务覆盖新会话 |
| P1-12 代理切直连 tracker 残留 | ✅ 已修 | 直连分支显式重置 `proxy_tracker_connections=False`，smoke [3g] 断言 |
| P1-13 覆盖缺口（畸形输入） | ✅ 部分 | smoke 新增 [2b1] bencode 防御单测（深度炸弹/超长整数/超长长度字段）；seek/并发等仍待补 |
| P1-14 测试假绿 | ✅ 已修 | moov/qt 测试依赖缺失时返回退出码 2=SKIP 显式跳过；imageio-ffmpeg 已写入 requirements.txt 注释 |
| P2-1 status 重复 O(分片数) 扫描 | ✅ 已修（第一步） | `buffer` 与 `contiguous` 同源，一次扫描复用（省一半）；增量扫描/位图未做 |
| P2-4 libtorrent 暴露面 | ✅ 部分 | UPnP/NAT-PMP 默认关闭；6881 被占时回退随机端口（不再崩溃） |
| P2-6 .torrent/解析 DoS 上限 | ✅ 已修 | 文件 ≤32MB、bencode 深度 ≤64、整数/长度字段 ≤32 位 |
| P2-21 gui_feature 污染注册表 | ✅ 已修 | 备份→测试→恢复；实测 `RECENT_UNCHANGED=True` |
| P1-3 shutdown 加锁+join（REVIEW 遗留） | ✅ 已修 | 带锁清理 handle/_ses + `thread.join(timeout=2)` |
| P2-12 重试竞态 | ✅ 已修 | `_retry_stream` 加 `_pending_video` 守卫；开播成功归零计数 |
| P2-6 hint 文案截断 | ✅ 已修 | 独立模板变量只替换超时数字 |

**未做**（见正文清单，需后续排期）：P2-2 缓存上限/LRU、P2-3 symlink/junction realpath 复核（需人工）、P2-5 并发限流、P2-7 缓存隔离(info_hash)、饼状 20s 挂起体验、Ctrl+滚轮平移、coverage/CI/依赖锁定/打包/mypy、seek_to_byte 等其余测试缺口。

---

## 一、总体结论

核心机制（边下边播调度、分块级流服务、moov 尾部优先）实现质量高、设计正确；REVIEW.md 记载的 P0-1/P0-2/P1-1/P3-1/P3-2 修复经实证**验证正确、无回归**（安全成员实证 P3-1 边界 8 组全通过；架构成员实证 91433ea 单文件 path 与 v2 hash 无新问题）。

但存在 **1 个 P0（半成品日志改造会炸掉告警线程）**、约 14 项 P1。问题集中在三处：①未完成的改造（P0）；②可观测性（错误被吞/被覆盖/不提示）；③边界入口（会话竞态、异常降级路径、测试覆盖盲区）。

---

## 一、P0 —— 必须立即处理

| # | 问题 | 位置 | 说明 / 证据 | 修复建议 |
|---|------|------|------------|---------|
| P0-1 | **日志改造半成品：未 import 的 `log_exception` 在异常路径抛 NameError** | `core/fetcher.py:289,291,316`（import 区无 logutil） | 实证 `hasattr(fetcher,"log_exception")==False`。289 行在 alert 处理的内层 except 中调用 → NameError 冒泡到外层 except → **该批次其余 alerts 被整体丢弃**（元数据回调丢失）；291 行在外层 except 中调用 → **告警线程直接死亡**（看门狗停摆）；316 行在 metadata_received 处理中调用 → 元数据静默失败。正常路径不触发，故 8 套测试全绿掩盖。 | 立即补 `from .logutil import log_exception` 并提交；随后收尾整轮改造（其余 ~15 处 fetcher 裸 except + scheduler 6 处 + stream_server/main_window）。 |

---

## 二、P1 —— 高优先级（两周内）

### 安全（security 成员）
| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| P1-1 | 流服务**无鉴权 + 无 Host 校验**：DNS rebinding / 同机恶意进程可读缓存目录任意文件（含历史残留与用户自设 cache_dir） | `core/stream_server.py:139-146,264` | 每会话随机 token 注入 URL + Host 严格校验（127.0.0.1/localhost）+ `Cache-Control:no-store`、`X-Content-Type-Options:nosniff` |
| P1-2 | **缓存清理无守卫**：cache_dir 用户可配，`_rmtree_quiet` 直接删目录内容，指向 `C:\` 或文档目录并点「立即清理/退出清理」即灾难 | `ui/settings_dialog.py:105-141`、`ui/main_window.py:162-166,337-338` | 拒绝盘符根/用户目录；要求目录名含项目标识或首次建立标记文件 |

### UI/UX（ux 成员）
| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| P1-3 | 播放按钮文案状态机错乱：stop/set_waiting 不重置 "暂停"、无媒体源点击仍改文案 | `ui/preview_player.py:45,93-98,106-107` | 播放/等待/停止三态显式管理；无源时禁用按钮 |
| P1-4 | 播放器错误提示**用户不可见**：被 700ms 定时器覆盖；4 次重试失败只写底部小字 | `preview_player.py:126-129`、`main_window.py:286-292` | 错误进入状态栏/弹层，重试耗尽给明确提示 |
| P1-5 | 画廊"下载完成自动显示"承诺不兑现：`_load_thumb` 不重渲染当前大图 | `ui/gallery.py` | 大图加载完成后刷新 |
| P1-6 | 解析完成即**自动下载首图**（setResult→setCurrentRow(0)→file_requested→start_preview 抢占下载配额） | `ui/gallery.py` + `ui/main_window.py` | 仅激活行浏览时按需下载，或显式提示 |
| P1-7 | 代理选 SOCKS5/HTTP 但**主机留空 → 静默回落直连**，仍提示"设置已保存"，隐私勾选形同虚设 | `core/config.py:96-100`、`ui/main_window.py:156-160` | 保存时校验 host+type 组合，缺失明确警告 |

### 架构（architect 成员）
| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| P1-8 | 流服务回调（avail_cb/pieces_cb）异常 → **静默降级为"整文件可用"**，把稀疏零数据喂给播放器（与历史缺陷同型，实证） | `core/stream_server.py` `_availability` | 回调异常按"不可用"处理（416/503）+ 记日志，禁止降级为全量 |
| P1-9 | `scheduler.stop()` 只 pause 不撤 `auto_managed`，libtorrent 队列管理可能自动续传（推断） | `core/scheduler.py:160-176` | stop 时 unset auto_managed + 清 deadlines |
| P1-10 | 连续解析的 **handle 归属竞态**：metadata_received_alert 无 request-id 匹配，旧会话 alert 可污染新会话（推理） | `core/fetcher.py` alert 循环 | alert 携带会话代次/句柄校验 |
| P1-11 | 本地 .torrent 解析**同步跑在 UI 线程** + 双解析链路（纯 Python bencode 与 libtorrent 各解析一遍、路径构造不共享——3 次缺陷的根源模式） | `main.py`/`ui/main_window.py`、`core/parser.py`+`core/fetcher.py` | 解析移入后台线程；统一路径构造入口 |
| P1-12 | 代理从启用切回直连时 **proxy_tracker_connections 残留走旧代理**（实证） | `core/config.py:89-110` | 直连分支显式重置 tracker 连接设置 |

### 测试（qa 成员）
| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| P1-13 | 覆盖缺口：seek_to_byte、播放失败重试、画廊切换、畸形输入、shutdown 竞态、并发请求**零测试** | 各测试脚本 | 按入口路径矩阵补齐；补确定性单元测试 |
| P1-14 | `qt_stream_open_test` **非应用同款**（不 play()、不经过 VideoPreviewWidget）+ ffmpeg 缺失时 `return 0` **静默假绿** + imageio-ffmpeg 未声明进依赖 | `qt_stream_open_test.py`、`moov_stream_test.py` | 提高断言强度（进 PlayingState）；"跳过 ≠ 通过"改为显式 SKIP 状态；测试依赖写入 requirements |

---

## 三、P2 —— 中优先级（一个月内）

**安全**：P2-3 symlink/junction 可绕过词法校验（建议 realpath 复查 + 人工复核 libtorrent 落盘行为）；P2-4 libtorrent 暴露面全开（`0.0.0.0:6881` + DHT/LSD/UPnP/NAT-PMP，300 连接可被远程占满 → 预览 DoS；建议关 UPnP/NAT-PMP、随机高位端口、限入站）；P2-5 ThreadingHTTPServer 无并发上限（线程风暴，建议信号量限流+事件等待）；P2-6 .torrent 解析无大小/深度上限（建议限 32MB+深度）；P2-7 缓存默认不清且路径可预测（%TEMP%\magnet_viewer_cache 固定名，同机可预置同名文件伪造内容；建议默认退出清理+按 info_hash 隔离目录）。

**架构**：P2-8 `begin()` 异常留下半启动态（无回滚）；P2-9 6881 端口被占时崩溃无保护（改随机端口+重试）；P2-10 UI 破 core 封装（直写 `_metadata_timeout`、直访 `scheduler`）；P2-11 REVIEW P2-1 O(分片数) 扫描未优化（status() 每次 700ms 双重复计算 + 每 HTTP 请求全量扫描，大文件单核饱和——先消重复，再增量扫描/位图）。

**UI/UX**：P2-12 重试竞态（`_retry_stream` 无 `_pending_video` 守卫，开播门槛与 `_refresh_status` 不一致可绕过 1MB）；P2-13 等待索引阶段"缓冲 0.0%"误导；P2-14 拖动到未下载区 20s 挂起、停止后重播归零无提示；P2-15 Ctrl+滚轮缩放与滚动冲突、放大后不可平移；P2-16 双击 .pad/文本等不可预览文件静默；P2-17 保存设置后 hint 指引文案被 `split("。")[0]` 截断丢失；P2-18 日志系统承诺三处脱节（REVIEW.md 承诺"写缓存目录+设置面板可关" vs logutil 固定写 %TEMP% 且 config.DEFAULTS 无 `logging_enabled`（按 docstring 调用会 KeyError）vs 设置面板无开关、全项目零调用点、无查看入口）；P2-19 "立即清理缓存"清的是启动时旧目录；P2-20 设置面板陈旧误导文案（"配置文件勿含空格/非 ASCII"——实际存注册表、无此校验）。

**测试/工程**：P2-21 `gui_feature_test.py` **污染用户真实注册表 QSettings**（push_recent 写入 "Bitseed/MagnetViewer" 且不恢复）；P2-22 测试临时目录泄漏（Temp 残留 30+ 个 mv_*_ 目录、seed session 无 finally 销毁、异常路径不清理）；P2-23 复用固定端口无释放等待（慢机脆弱）；P2-24 依赖下限过宽（`>=` 无锁定，libtorrent 2.0/2.1 API 不兼容）；P2-25 REVIEW 建议未落地项：coverage、run_tests.py 统一入口、CI、打包、mypy、P1-3 shutdown 加锁/join、P2-2 缓存上限与 LRU。

---

## 四、P3 —— 低优先级 / 技术债

弃用 API（libtorrent `add_files`/`create_torrent` DeprecationWarning）；`_is_within` 内部不 normpath（当前靠调用方先规范化才安全，建议移入守卫内部）；代理密码明文落注册表（建议 DPAPI/Credential Manager）；`config.get` 仅捕 TypeError；设置对话框无"恢复默认"；`play_toggled`/`btn_pause_text` 死代码；SVG 无法解码无提示；90s 解析无进度提示；字体无回退；`_on_gallery_file` 吞异常；tracker 不追加 bootstrap 列表；200 响应长度语义（无 Range 请求时 CL=连续前缀，客户端若不做 Range 则看不到逻辑大小）；info_hash 发往 5 个公共 tracker + DHT 的隐私；URL 含种子文件名可指纹；依赖无 hash 锁定。

---

## 五、已核实正确 / 无需改动

- REVIEW.md P0-1/P0-2/P1-1/P3-1/P3-2 修复**实证通过、无回归**（单文件 path、v2 hash 分支、safe_rel_path 双链路、_is_within 大小写/兄弟前缀/盘符/反斜杠/二次编码 8 组边界、P3-2 cache_dir 三重覆盖）。
- moov 尾部优先流式机制本身设计正确（本轮不涉及）。
- logutil.py 模块自身质量好（幂等、线程安全、滚动 1MB×3、绝不因日志抛异常）——问题在"未接线 + 文档超前"。

---

## 六、建议执行顺序

1. **止血（今天）**：P0-1 补 import 并提交；顺手完成 logutil 接线与 8 处高危 except 改造。
2. **安全 P1（本周）**：P1-1 流服务 token+Host 校验；P1-2 缓存清理守卫。
3. **正确性 P1**：P1-8 回调异常禁止降级 → P1-12 代理直连重置 → P1-9 stop 撤 auto_managed → P1-10 会话代次。
4. **UX P1**：P1-3/P1-4 播放器状态与错误可见性 → P1-5/P1-6 画廊 → P1-7 代理校验。
5. **收尾**：P1-13/P1-14 测试补齐（含"跳过≠通过"语义）、P2-1 性能、shutdown 加锁/join、缓存上限、工程基建（coverage/CI/依赖锁定）。