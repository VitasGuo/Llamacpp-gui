# 项目目标与进度

## 现状

- **目标**：在本地 Windows 环境运行 LlamaCPP GUI（PyQt6 桌面客户端），用于管理本地 llama.cpp 推理服务器。

- **当前版本**：v1.20.2（2026-09-24，本地模型管理入口 toggle 化：进入后同按钮位变"← 返回"）

## 版本历史

| 版本     | 日期         | 说明                                                                                                                                                       |
| ------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.20.2 | 2026-09-24 | 「本地模型管理」入口按钮 toggle 化：用户反馈"进入视图后还要去左边点返回按钮太折腾"，改为**同一按钮位在视图 3 时变成"← 返回"**——`stack.currentChanged` 信号统一切文字 + tooltip，再点切回追踪视图（索引 0）；删除本地模型视图顶部的"← 返回"按钮（功能被搜索栏按钮收编，UI 更简洁）；`_show_local_models` 改为 toggle 行为（已在视图 3 时点击 → 返回 0；否则进入 3）；新增 `tests/test_local_models_toggle.py` 4 例（199→203 全绿） |
| v1.20.1 | 2026-09-24 | 「本地模型」按钮文字改为「**本地模型管理**」（更贴合实际功能：含查看 + 删除 + 连带清理绑定） |
| v1.20.0 | 2026-09-24 | **新增「本地模型」文件管理**（traps #43）：模型搜索与下载标签页搜索栏右侧新增入口，进栈索引 3 的视图列出模型目录下全部 `.gguf`（文件名/大小/修改时间/视觉投影标记，按名排序），支持勾选批量或单行**永久删除**。删除时：① 确认框列出实际将删文件 + 合计大小、默认按钮"取消"；② 连带删除同目录**配对 mmproj**（`pair_mmproj`）；③ 清理该模型**启动脚本绑定**（`ScriptService.remove_binding_for_model`：`.bat` 移入 `data/scripts_replaced` 备份 + 清 `scripts.json` 条目，匹配覆盖 json 原始条目（含过期条目）/孤儿 .bat/derive_name 兜底三路）；④ **模型正在运行时整体拦下**（删除 worker 内查 `ProcessService.is_running`，避免 Windows 锁文件与"服务在跑但脚本已没"的错位）；⑤ 删除后主窗口按 `local_models_changed` 信号清理已失效的当前选择（显式清空路径框 + 重刷下拉）。新增 `service/model_file_service.py`、`ui/workers/local_model_workers.py`、`model_scanner.list_local_models`；顺带补 #42 第二种触发路径（清空选择后下拉插「（未选择模型）」占位项）；测试新增 25 例（174→199 全绿） |
| v1.19.6 | 2026-09-24 | 修复**重启后模型下拉停在第一项、与下方路径不一致**（traps #42）：`_reload_model_combo` 用 `findData` 精确匹配，而下拉项来自 `scan_gguf_files` 的 `os.path.join`（Windows 反斜线，实际是 `C:/modelscope\MiniCPM5-2B-F16.gguf` 这种混用形式）、配置里存的是正斜线 → 匹配必失败 → 跳过 `setCurrentIndex` → 停在索引 0；新增纯函数 `combo_index_for`（两侧 `normalize_path` + 大小写不敏感），下拉 userData 统一存规范化路径；兜底：当前模型不在模型目录（被删/移走）时插入"（不在模型目录）<文件名>"并选中，保证下拉与路径/表单永不打架；实测真实数据 旧 `-1` → 新 `2`；测试新增 4 例（170→174 全绿） |
| v1.19.5 | 2026-09-24 | 修复**进程秒退时日志丢掉最后几行（恰恰是报错行）**（traps #40）：`ui/workers/log_worker.py` 主循环每轮只读一行、读完即查存活并 break，llama-server 加载失败在 0.3~1s 内退出、缓冲区内未读的错误行全丢，界面只剩"CORS 警告 → 进程已结束"的假日志（用户运行 Ternary-Bonsai 时被此坑住）；新增 `_drain_remaining()`——进程已退出后继续读到 EOF（上限 `LOG_DRAIN_MAX_LINES=500`），且**进程存活时不读**（避免阻塞读卡住线程）；实测同一 .bat 由"只到 CORS 警告"变为完整 19 行（含 `invalid ggml type 142` + `exiting due to model loading error`）；新增 `tests/test_log_worker_drain.py` 3 例（桩进程服务）；167→170 全绿 |
| v1.19.4 | 2026-09-24 | 修复下载队列"进度"列数字乱码（traps #2 **复发**，用户描述"百分比数字好像是用中文写的"）：`ui/model_tab.py` 的 QProgressBar 一直没关条内建文本，`%p%` 在本环境渲染成乱码字形；新增模块级 `_make_progress_cell`（条 `setTextVisible(False)` + 右侧 QLabel 显示阿拉伯数字 `NN%`，列宽 160px 容纳）与 `_set_progress_cell`，`_add_queue_row`/`_update_queue_row` 改走这对函数；新增 offscreen 回归测试 `tests/test_download_progress_cell.py` 5 例（条内文本关闭 / 文本仅 ASCII 数字 / 数值同步 / 容错 None）；162→167 全绿 |
| v1.19.3 | 2026-09-24 | 启动脚本区去掉模型"浏览..."按钮（模型只从"路径配置"里模型目录的递归扫描下拉中选）：模型来源已由 `Settings.model_dir` 规定，浏览任意路径属重复入口，还让"选模型即生成脚本"的主流程多一条旁路；删除 `_select_model_file`（其设置 model_path/save/自动绑视觉/`_on_model_changed` 的链路已被 `_on_model_combo_selected` 完全覆盖）+ 相应按钮；保留"外挂视觉模型 选择..."（`find_mmproj` 只在模型同目录找 mmproj，异目录时是唯一兜底）与路径配置里的 llama-server.exe 选择；162 全绿 |
| v1.19.2 | 2026-09-24 | 脚本区按钮合并：**「一键生成」+「删除该模型脚本」→「重置参数」**。根因：懒人流（选模型即自动生成参数）下两个按钮都只服务"回到默认状态"——一键生成只剩重置作用、删除脚本是它的前置清理，实际是同一诉求的两半（且"请先一键生成或填写脚本内容"等提示已成过时文案）。新按钮一次性完成：清掉该模型已保存的脚本参数 → 按 `auto_generate_config` 重新生成一版默认脚本并落盘（带确认框，不影响模型文件）；`ScriptService.reset_script(model_path, content)` 复用**现有绑定名**重建（无绑定才用 `derive_name`）——改名会让运行中清单/`pids.json` 记录错位；布局：`保存｜重置参数` 同行 + `查看生成的脚本`，模型路径行只留"浏览..."；清除死代码 `ScriptService.delete_script` / `_remove_config_entry`（合并后无调用方）；测试新增 3 例（162 全绿） |
| v1.19.1 | 2026-09-24 | 「运行控制」顶部按钮语义对齐多模型：大"结束"改**"全部结束"**——不再只结束当前选中脚本，而是逐个结束清单里所有运行中的模型（每个模型本就有各自的行内"结束"按钮）；目标为 运行中清单 ∪ `pids.json` 运行时记录（纯函数 `stop_all_targets`，去重保序，覆盖跨会话恢复、尚未进清单的服务），旧实例仍走全局 PID 回退；任一模型在跑该按钮即可点（`_sync_control_panel`）；删除清单上方多余的"正在运行:"标题（清单本身即答案，"无模型运行"占位符保留）；未跟踪的外部实例仍归下方"清理全部llama进程"（带确认）；测试新增 6 例（159 全绿） |
| v1.19.0 | 2026-09-24 | **脚本绑定唯一化（修复"跑 MiniCPM5 运行中却显示 gemma-4"）**：`scripts.json` 同模型多条目 + 部分条目 model_path 与 .bat 的 `-m` 脱节，`get_script_for_model` 取"第一条命中"导致错位（traps #38）。三端收口：**读取端** `get_script_for_model` 同路径候选按"脚本名与模型文件名的 token 契合度 > 置顶 > 最新保存"择优（新增纯函数 `name_model_score` / `binding_rank`）；**写入端** `_upsert_config_entry` 同 model_path 视为同一条（改名时清旧 .bat 防孤儿）；**启动迁移** 新增 `migrate_bindings()`——以 .bat 的 `-m` 为事实源校正 model_path、同模型只留最优条目（淘汰 .bat 备份到 `data/scripts_replaced` 不删除）、清掉 .bat 已不存在的幽灵条目、幂等；`process_service.remap_runtime` 同步 pids.json 键；实测本机 18→15 条（校正 2、清理 3、二次运行全 0）；测试新增 12 例（153 全绿） |
| v1.18.0 | 2026-09-24 | 主控制页运行状态**去重**：删除系统负载 CompactMonitor 的"状态"显示与"运行时长"（两处与运行控制冗余）；运行控制新增**运行中模型清单**（支持多模型同时运行）——每行 模型名｜各自运行时长｜"结束"按钮（单点结束任一模型，走 `stop_by_pid(name)`），1s QTimer 逐行刷新时长（`format_uptime` 纯函数）；旧版本 last_pid 恢复实例显示"旧实例"行、走全局 PID 停止；141 全绿 |
| v1.17.3 | 2026-09-24 | 切换搜索来源按输入分流：**空输入**时只更新占位提示、完全不打扰当前视图（追踪界面保持正常不重新加载）；**有输入**时保留输入内容、清空旧来源结果表并回追踪视图等重搜；142 全绿 |
| v1.17.2 | 2026-09-24 | 修复切换搜索来源的两个体验问题：不再清空搜索框已有输入（用户可能想在另一来源搜同一关键词，仅更新占位提示）；切换来源后回到默认的更新追踪视图而非空的搜索结果页（重新搜索才显示新来源结果）；142 全绿 |
| v1.17.1 | 2026-09-24 | 修复搜索后无法回到追踪视图：搜索框新增 **× 清空按钮**（搜索栏搜索框与搜索按钮之间），点击清空关键词并切回默认的更新追踪视图——搜索后返回追踪的唯一显式入口；142 全绿 |
| v1.17.0 | 2026-09-23 | **模型更新追踪并入"模型搜索与下载"标签页**（v1.13.1 独立标签页撤销合并）：单一视图栈——未搜索时默认显示追踪列表，输入关键词自动切换搜索结果，"查看文件"进文件列表；追踪表格每行新增"查看文件"按钮复用现有文件浏览+下载队列（追踪模型有新版可直达下载）；`ModelWatchTab` 改可嵌入视图 `ModelWatchView`（新增 `view_model_requested` 信号、`notify_added` 轻量插入不丢高亮）；关键坑：文件列表下载来源用独立 `_filelist_source`（追踪查看强制 ModelScope，防 combo 停在 HF 镜像时下载 URL 错）、"返回"按 `_filelist_back_index` 记忆目标（追踪页/结果页）；主窗口仅保留一个标签，切回该标签刷新追踪；顺手删 README 遗留的"版本检查"功能行（v1.16.0 已删实现）；142 全绿 |
| v1.16.5 | 2026-09-23 | 修复搜索页手动追踪的模型在追踪页消失（traps #36，数据丢失）：追踪页 merge/check 后台 worker 持 UI 内存旧快照**整体覆盖写盘**，把 add_manual_model 刚写入磁盘的条目冲掉——`_save_watchlist` 改**合并语义**（磁盘条目按 model_id 保留、ignored 剔除防"移除"复活），worker 一律磁盘重读不信任 UI 快照，remove_model 调整为先写 ignored 再删（防合并捞回）；实测确认用户磁盘数据已被旧 bug 冲掉（manual 条目丢失，需重新点一次"追踪"）；142 全绿 |
| v1.16.4 | 2026-09-23 | 修复聊天页地址固定显示旧端口（traps #35）：前端"手动保存过→永远用手动值"把自动检测永久压死（用户忘了保存过 8086 旧地址，切换服务永不跟随）——init 优先级反转为**自动检测优先**（有运行中服务始终跟随当前服务地址，手动值仅无服务时兜底）；设置弹窗 API 地址旁新增"自动检测"按钮（拉 bridge/info 即时填充+测连接，无服务时明确提示）；llamaUrlManual 降级为纯历史记录；139 全绿 |
| v1.16.3 | 2026-09-23 | 修复聊天页取不到正在运行模型的 API 地址（traps #34）：`_current_llm_url` 两层来源（就绪 URL/pids.json）都只查"当前选中脚本"，切到其他模型调参时返回空——改为"选中名优先、回退任意运行中记录"（纯函数 `_pick_llm_url`，7 例回归测试）；伴生修复：host 解析不再在桥服务 HTTP 线程同步跑 `get_tailscale_ipv4()` 探测子进程（最坏阻塞 ~6s，R12 后台化漏改点），改读手动指定/探测缓存；另整合 fork 上游 merge（转义字符/主线程卡死两提交，冲突取本地版）并移植 GBK 回退解码（cmd 中文报错不再变问号）；139 全绿 |
| v1.16.2 | 2026-09-23 | ctx 基础挡位补充 **200K (204800)** 细分挡（不作为默认值，仅手选；1M 挡 v1.16.1 已有）；默认值规则不变（上限<128K 取最大、≥128K 默认 128K）；132 全绿 |
| v1.16.1 | 2026-09-23 | **ctx-size 挡位化（解决 128K 约束矛盾）**：新增 `read_gguf_context_length` 解析 GGUF 元数据 `*.context_length`（只扫文件头 KV 区、命中即停，遇超大 tokenizer 数组防御性放弃）；`ctx_options_for` 统一生成挡位与默认值——模型上限 <128K 默认取模型最大值、≥128K 默认 128K（更高挡手选/手填），读不到元数据回退文件大小分级；表单 ctx 控件改**可编辑下拉**（挡位点选 8K~1M + 任意值手填，NoInsert + IntValidator），挡位随选中模型动态刷新；解决 v1.16.0 遗留的"ctx≥128000 硬性约束 vs auto_generate 按文件大小分级"矛盾（traps #33：可编辑下拉必须读 currentText 而非 currentData）；实测本机模型全部正确解析（MiniCPM5→128K、Qwen3.8-27B→256K 上限默认 128K）；测试新增 9 例（132 全绿） |
| v1.16.0 | 2026-09-23 | **全量审查修复 16 项 + misc**（R1-R16）：表单切模型残留参数污染（set_preset 整体重置）；chat handlers 4 处读改写包 CONV_LOCK；chat.js 非流式多角色回复错位（记 msgIdx）+ 轮询 isSending 守卫；清理旧版本排除运行中进程 exe 目录；下载队列移除按 UserRole 现查行号；追踪按钮 lambda 默认参数防循环变量捕获；scheduler not-found 自愈 rebuild_task_index；parse_bat_params 引号值 + 短 flag 词边界；恢复服务停止后 CompactMonitor 状态同步；**五处 GUI 线程阻塞挪后台**（Tailscale 探测 60s TTL 缓存 / 清理全部 llama 进程 / 模型目录 merge / 旧版本清理规划 / 图片迁移）；misc：托盘重启 Popen 补 CREATE_NO_WINDOW、get_script_for_model 大小写不敏感、Settings.load 读侧路径归一、dest_path 归一、表单原文视图统一刷新、下拉校验失败回滚、print→logger；**死代码清理约 450 行**（MonitorTab/TpsChart/HistoryChart、NewScriptDialog、CheckUpdateWorker/CheckAppUpdateWorker、history_worker、update_workers→compare_semver 迁 utils/semver.py、validator.sanitize_filename），移除 PyQt6-Charts 依赖；CompactMonitor.update_tps 补 t/s 历史落盘；traps 新增 #24-#32；测试新增 6 例（123 全绿） |
| v1.15.0 | 2026-09-23 | **脚本彻底绑定模型**（移除独立脚本名/清单）：选中/下载模型即自动生成基础参数进表单，微调保存即绑定到该模型；`ScriptEntry.derive_name(model_path)` 自动命名（文件名+路径短hash，跨目录同名不冲突），`script_service.get_script_for_model` 归一化路径查找；删除脚本列表/置顶/新建按钮与控制面板"检查更新"按钮；表单常用参数=通用/模型/量化 直接显示 + **模型别名自动=模型文件名（只读）**，高级参数（并发批处理/MTP/MOE）收进主控制页底部可折叠"高级参数"区（默认收起）；移除独立"性能监控"标签页（监控并入主控制页压缩版）；修复托盘重启误报"已在运行"（重启实例跳过单实例锁）+ 重启慢（桥 shutdown 改后台线程、monitor join 2s→0.2s）；左栏新增 Tailscale 默认 IP 手动指定（Settings.tailscale_ip 覆盖自动检测、失效/留空回退）；聊天 LLM 地址 0.0.0.0 时自动解析为 Tailscale IP；移除脚本表单里残留的 `--mmap` 开关（traps #15 复发修复）；测试新增 11 例（累计 127） |
| v1.14.0 | 2026-09-22 | 脚本编辑器改为**表单式**（填空/下拉/勾选，不再直接编辑 .bat 代码，可展开看生成原文）；新增**一键生成脚本**：选模型 → `auto_generate_config` 按文件大小自动算 ctx/量化/gpu 层/host、命名 alias、自动挂 mmproj，微调即存；`parse_bat_params`/`parse_model_path_from_bat` 反向解析 .bat 回填表单；运行/保存/端口改写统一走表单事实源；测试新增 4 例（累计 116） |
| v1.13.1 | 2026-09-22 | 模型更新追踪改为**主窗口独立标签页**（ui/model_watch_tab.py），不再嵌在模型下载页；修复"移除没效果"：新增 `WATCHLIST_IGNORED_FILE` 持久化 ignored 集合，本地模型移除后不再被 merge 自动加回，手动追踪解除忽略；切到该 tab 自动 refresh（并入新下载模型 + 后台检查）；测试新增 2 例（累计 112） |
| v1.13.0 | 2026-09-22 | 新增模型更新追踪（模型搜索与下载页"模型更新追踪"视图）：本地已装 ModelScope 模型系列自动加入关注 + 搜索结果可手动追踪；逐模型查 ModelScope `LastUpdatedTime` 与基线对比判更新，有更新行整行标黄；打开视图自动后台检查 + 手动"检查更新"；持久化 data/model_watchlist.json；新增 service/watchlist_service.py、ui/workers/watch_worker.py；测试新增 5 例（累计 110） |
| v1.12.0 | 2026-09-22 | 新增"清理旧版本"功能（版本管理页）：保留当前使用版本 + 每变体系列（cuda-13.3/13.4/...）最新 2 个，其余旧版本目录与已解压的下载缓存 zip 一并清理；service 层 `plan_version_cleanup`/`delete_version_dir`/`plan_zip_cleanup`/`delete_zip`，UI 确认框列出待删项与释放空间、预览删除、单个失败不中断；实测本机 8 版本→保留 4、zip 清 8，释放约 1.9GB；测试新增 4 例（累计 105） |
| v1.11.0 | 2026-09-13 | 全量代码审查（三路并行）一次性修复 16 项：运行前自动保存/端口改写保存漏传 pinned 静默取消置顶；_toggle_pin 用磁盘内容避免回退未保存修改；closeEvent StatusPoller 缺 wait；plan_auto_download 完整 release 无待下载时不再降级下载旧版；list_installed 按 build 号排序（字符串跨位数错乱）；start_script 单引号路径被全局 replace 破坏；chat call_llm 空 choices IndexError；chat 并发读写对话加 CONV_LOCK（RLock，写串行化 + scheduler 两段锁，traps #20）；_on_release_selected 提前 return 未恢复 blockSignals；模型搜索无过期保护（旧 worker 迟到覆盖）；本地已有完整 zip 跳过 SHA256 校验；DownloadQueue._load 结构校验；download_service worker 竞态（旧 finished 弹掉新 worker 致 GC 崩溃）；测试新增 3 例（累计 101） |
| v1.10.3 | 2026-09-13 | 版本切换完成弹窗改为双按钮："立即重启"（重启 GUI 加载新版本，走 restart_requested 信号由主窗口执行）/ "稍后重启"（默认，继续当前会话）；替代原来只能点 OK 的信息框 |
| v1.10.2 | 2026-09-13 | 脚本列表体验优化：置顶从右键改为**每行内置 📌 按钮**（橙色=已置顶/灰色=未置顶，点击切换，右键保留为补充）；列宽合理化——脚本名自动拉伸占满剩余空间、状态按内容、置顶列固定 34px |
| v1.10.1 | 2026-09-13 | 修复自动下载失效（traps #18）：GitHub release 资产逐步上传，最新版可能只有 cudart → `_release_from_api` 丢弃无主资产快照、`fetch_releases` 缓存命中校验完整性强制重抓、`plan_auto_download` 支持列表自动落到第一个可下载版本（实测 b10934 计划 [('b10934','cuda-13.3')]）；修复下载进度条百分比错乱（traps #19）：HTTP total 覆盖失真元数据、完成后校准 file_size、progress clamp 0-100；测试新增 8 例（累计 98） |
| v1.10.0 | 2026-09-13 | 托盘菜单新增"重启"（sys.executable+argv 重启，main() 单实例锁短暂重试兜底）；脚本列表支持**置顶**：新增"置顶"列（📌）+ 右键菜单置顶/取消置顶，新建/新保存脚本默认置顶，排序置顶在前，pinned 字段持久化到 scripts.json；脚本面板垂直空间加大（stretch 2）；测试新增 5 例（累计 90） |
| v1.9.2 | 2026-09-13 | 主控制页嵌入压缩版系统监控（CompactMonitor）：CPU/RAM/GPU + t/s + 运行时长，与日志输出并排（stretch 3:1），加载模型时边看日志边看负载免切标签；与 MonitorTab 同数据源，5 处状态/信号双发（启动/停止/聚焦/tps）；新增 _GpuRow 紧凑卡（温度并入显存文本）；测试新增 7 例（累计 85） |
| v1.9.1 | 2026-09-13 | 新建脚本流程联动优化：脚本名称并入参数对话框并**自动命名**（默认取所选模型文件名去扩展名，可改，去掉手输弹窗）；模型下拉改显示文件名（完整路径存 userData）；保存新脚本默认名同样预填；测试适配 v1.9.0 路径规范 + 新增自动命名 3 用例（累计 78） |
| v1.9.0 | 2026-09-13 | 路径分隔符统一规范：新增 `utils/path_utils.py`（normalize_path，统一正斜线 `/`）；config 路径 setter、script_builder、find_mmproj、replace_bat_dir 写入点收口；一次性迁移存量 17 个脚本 + scripts.json + app_config.json（traps #16）；顺带修复 MiniCPM5 脚本文件错位——名字含空格时 bat 文件名是 sanitize 后的下划线版，此前修了空格版孤儿、真文件未同步（traps #17） |
| v1.8.1 | 2026-09-13 | 新增 MiniCPM5-2B-F16 启动脚本（端口 8080、ctx 200000）；修复 b10883 移除 `--mmap` 导致的脚本秒退（traps #15）：批量清理 7 个脚本的 `--mmap`，同步 .bat 与 scripts.json；修正 MiniCPM5 脚本的相对 cd 路径与错误的 model_path |
| v1.8.0 | 2026-09-06 | 全部脚本统一端口 8080（单模型运行，不冲突）；ctx-size 最低 128000（大上下文可用）；脚本名移除端口后缀，bat 文件同步重命名 |
| v1.7.1 | 2026-09-04 | 修复后台静默下载链路完全不触发（traps #14）：`_on_local_info` 补 `_local_info_done` 置位；`_refresh_installed` 回填 `_installed_dirs`（防已装版本重复规划）；`_start_next_auto` 提前置 `_install_silent`；CUDA 通道按大版本系列匹配最新资产（本地 cuda-13 → 装资产 cuda-13.3）；新增 `llamacpp_auto_download` 配置（默认开），启动即后台下载当前通道+推荐变体，完成不弹窗、进"已安装版本"一键切换 |
| v1.7.0 | 2026-09-04 | 新增"版本管理"标签页：本地 build 号检测（`llama-server --version` 双格式解析）+ NVML 驱动检测与变体推荐（cuda-13.x 需驱动≥580、cuda-12.x 需≥528，按前缀系列逐级降级）；GitHub releases 列表（1h 缓存）对比标记"可更新"；断点续传下载安装（直连/自定义镜像前缀/手动导入 zip，SHA256 校验，zip-slip 防穿越）；CUDA 变体可选配套 cudart 运行库（约 380MB，系统已有 CUDA 时自动跳过）；版本化目录安装 + 一键切换（自动批量替换 .bat 中 `cd /d` 路径，运行中服务不受影响）；新增测试 35 例（累计 55） |
| v1.6.0 | 2026-09-03 | 全量代码审查一次性修复 18 项：新建脚本 QComboBox 崩溃（traps #9）；GUI 线程 tasklist 回归（#11）；/metrics host 感知（#10）；下载续传换目录损坏（#12）；退出时 LogWorker 崩溃风险；聊天页 XSS 消毒 + 桥服务同源校验 + 静态文件防穿越加固；模型搜索移后台线程；运行前自动保存；多服务器 URL 串扰；端口占用自动顺延；定时任务采样参数与退避；配置类型防御；taskkill 结果校验；单实例锁；补冒烟测试 tests/（20 用例） |
| v1.5.0 | 2026-09-02 | Tailscale 外网接入：`--host` 改为下拉（仅本机/所有接口/Tailscale 专用）；新增 `service/tailscale.py` 多路径检测 IP；pids.json 记录 host，聊天回退地址 host 感知；服务就绪后显示外网访问 URL + 复制按钮（traps #8） |
| v1.4.3 | 2026-09-02 | 聊天界面 API 地址免手填：桥服务新增 `GET /bridge/info`，GUI 注入当前模型地址 provider，前端 `init` 自动填充（未手动保存时），并记录 traps #7                                                        |
| v1.4.2 | 2026-09-02 | 修复 #5 改后台轮询引入的启动顺序 bug：StatusPoller 创建晚于 \_restore\_service\_state 导致 GUI 启动即崩溃；已提前创建并实际启动验证（traps #6）                                                   |
| v1.4.1 | 2026-09-02 | 修复无控制台启动时频繁弹 cmd 窗口与界面卡顿：所有子进程统一加 CREATE\_NO\_WINDOW；状态轮询从 GUI 线程移到后台 StatusPoller QThread（traps #5）                                                     |
| v1.4.0 | 2026-09-02 | 修复所有启动脚本无法运行的根因：cmd /c 列表传参导致引号被剥离、&& 被拆命令，改为整串命令 + /d /s /c + call + 双引号包路径（traps #4）                                                                   |
| v1.3.1 | 2026-09-02 | 扫描本地模型目录全部 14 个完整 gguf 主模型各生成一个脚本：上下文按文件尺寸分级（65536/32768/16384）、视觉模型自动绑定 mmproj、>12G 模型自动 CPU offload                                                    |
| v1.3.0 | 2026-09-02 | 基于用户硬件配置与本地模型目录现有模型，用应用自身 ScriptService 预生成 6 个启动脚本并登记 scripts.json                                                                                      |
| v1.2.1 | 2026-09-02 | 修复性能监控页 QProgressBar 内建百分比文本乱码（统一 setTextVisible(False)，百分比由旁侧 QLabel 显示）                                                                                |
| v1.2.0 | 2026-09-02 | 启用开机自启动 + 创建桌面快捷方式；新增本地模型目录扫描（model\_scanner）+ 下拉快速选择模型（model\_dir 配置）                                                                                   |
| v1.1.0 | 2026-09-02 | 新增系统托盘（关闭最小化到托盘、托盘菜单显示/退出）+ 开机自启动开关（注册表 HKCU Run），设置对话框与托盘菜单均可控                                                                                          |
| v1.0.0 | 2026-09-02 | 首次克隆并配置运行环境，GUI 启动验证通过                                                                                                                                   |

## 当前任务

- [x] v1.20.0 新增「本地模型」文件管理（2026-09-24）：
  - 起因：用户要删掉不合适的本地模型只能自己去文件夹手删；手删还会留下指向已删文件的死绑定
  - `service/model_scanner.py`：新增 `list_local_models(dir)`（path 正斜线 / name / size / mtime / is_mmproj，按名排序，单文件 stat 失败不影响其余）
  - `service/model_file_service.py`（新）：`pair_mmproj(path, models)`（在已扫描结果里配同目录 mmproj）+ `delete_model_files(paths)`（逐项容错、幂等、被占用时点明"可能正在运行"）
  - `service/script_service.py`：新增 `remove_binding_for_model(model_path, names)`（`.bat` → `data/scripts_replaced` 备份 + 清 json 条目；匹配三路来源：json 原始条目含过期条目 / `load_scripts()` 含孤儿 .bat / `derive_name` 兜底）
  - `ui/workers/local_model_workers.py`（新）：`LocalModelScanWorker`（扫描）+ `LocalModelDeleteWorker`（**先查 `is_running` 拦下运行中的模型**——该调用内部跑 tasklist 必须留在 worker；再删文件、逐个清绑定）
  - `ui/model_tab.py`：搜索栏右侧"本地模型"入口 → 栈索引 3 视图（复选框列 30px / 文件名 / 大小 / 修改时间 / 操作，底部合计 + 删除选中项 + 刷新）；`_confirm_local_delete` 独立方法（对齐 update_tab 的确认框模式，默认"取消"）；删除后整表重扫；新增信号 `local_models_changed(list)`
  - `ui/app.py`：`_on_local_models_deleted` —— 当前模型/视觉模型正是被删文件时清空配置 + **显式** `setText("")` + `settings.save()`，然后重刷下拉（未删到当前模型时不重载表单，避免抹掉未保存微调）；顺带补"（未选择模型）"占位项（traps #42 第二种触发路径）
  - `tests/`：新增 `test_local_model_files.py`(13) / `test_script_binding_removal.py`(6) / `test_local_model_view.py`(6)
  - 验证：199 例全绿；offscreen 端到端冒烟（临时目录 3 文件 → 扫描 3 行/合计正确 → 删主模型连带 mmproj、无关文件保留、信号带被删路径）；桩注入验证运行中拦截（文件未动 + 提示脚本名）；桩对象复用真实 `_reload_model_combo` 验证三种下拉定位（命中 / 未选择占位 / 不在目录占位）

- [x] v1.19.6 修复模型下拉与路径显示不一致（2026-09-24）：
  - 用户报告：重启后"启动脚本"模型下拉变成第一项，下面只读路径却不变
  - 根因（traps #42）：`_reload_model_combo` 用 `findData`（精确字符串匹配）定位当前项；下拉项来自 `scan_gguf_files` 的 `os.path.join`（Windows 反斜线，且因 root 是正斜线而呈 `C:/modelscope\X.gguf` 混用形式），配置里存的是正斜线 → 必失败 → `idx=-1` → 跳过 `setCurrentIndex` → 停在索引 0
  - `ui/app.py`：新增纯函数 `combo_index_for(paths, target)`（两侧 `normalize_path` + `lower()`，Windows 大小写不敏感）；`_reload_model_combo` 改走它、userData 统一存规范化路径；兜底"当前模型不在模型目录"时插入"（不在模型目录）<文件名>"并选中（下拉与路径/表单永不打架）
  - `tests/test_script_model_binding.py`：新增 `TestComboIndexFor` 4 例（反斜线扫描 vs 正斜线配置 / 大小写不敏感 / 未命中 -1 / 空目标）
  - 真实数据实测：旧实现 `index = -1`（复现 bug）→ 新实现 `index = 2`，正确命中 `Qwen3.8-27B-UD-IQ2_XXS.gguf`
  - 验证：174 例全绿；`py_compile` 通过

- [x] v1.19.5 修复进程秒退时日志丢失错误行（2026-09-24）：
  - 起因：用户运行 `Ternary-Bonsai-2-27B-PQ2_0.gguf` 失败，界面日志只有 CORS 警告 + "进程已结束"，无任何报错
  - 根因（traps #40）：`ui/workers/log_worker.py` 主循环每轮只读一行、读完即查存活并 `break`；llama-server 加载失败在 0.3~1s 内退出，缓冲区未读的错误行全丢
  - `ui/workers/log_worker.py`：新增模块常量 `LOG_DRAIN_MAX_LINES = 500` 与 `_drain_remaining()`（进程已退出才读，读到 EOF 为止），在 emit "进程已结束" 之前调用
  - `tests/test_log_worker_drain.py`（新）：3 例桩测试——残余行不丢且顺序为 `…错误行 → 进程已结束`、进程存活时一次都不读
  - 实测：同一 .bat 现在完整输出 19 行，结尾为 `invalid ggml type 142 … / exiting due to model loading error`
  - 顺带排查结论（traps #41）：该模型是 PrismML 三值量化（PQ2_0，张量类型 142 超出 mainline 类型表 0~42），本机 b11139/b11149 均拒载；须用厂商 fork `PrismML-Eng/llama.cpp` 的二进制（模型卡明确 "stock llama.cpp will not run these files"）
  - 验证：170 例全绿；`py_compile` 通过

- [x] v1.19.4 下载队列"进度"列数字乱码修复（2026-09-24）：
  - 根因：traps #2 复发——`ui/model_tab.py` 队列进度条的条内建文本（`%p%`）在本环境渲染为乱码字形，看着像中文；用户报"百分比的数字好像是用中文写的"
  - `ui/model_tab.py`：新增模块级 `_make_progress_cell(value)`（进度条 `setTextVisible(False)` + 右侧固定宽 38px 的 QLabel 显示 `NN%`）与 `_set_progress_cell(cell, value)`；`_add_queue_row` / `_update_queue_row` 改走这对函数（原先直接放裸 QProgressBar）
  - `tests/test_download_progress_cell.py`（新）：5 例 offscreen 测试——条内文本必须关闭、QLabel 文本仅含 ASCII 数字与 `%`、条值与文本同步、`None` 容错（含"必须持有 cell 引用，否则容器被 GC 连带删掉子控件"的坑）
  - traps #2 状态行补记复发与二次教训（"每新增一个 QProgressBar 都要同时决定百分比文本由谁显示"）
  - 验证：167 例全绿；`py_compile` 通过；顺手用 AST 扫全项目类体确认无重复方法定义（traps #32 隐患）= NONE

- [x] v1.19.3 去掉启动脚本区的模型"浏览..."按钮（2026-09-24）：
  - `ui/app.py`：删除模型路径行下的 `浏览...` 按钮与 `_select_model_file`；模型只从"路径配置"模型目录（`Settings.model_dir`）递归扫描的下拉中选（`_reload_model_combo` + `_on_model_combo_selected` 已覆盖设置/保存/自动绑视觉/`_on_model_changed` 全链路）
  - 决策：保留"外挂视觉模型 选择..."——`find_mmproj` 只在模型同目录查找 mmproj，视觉文件放在别处时它是唯一入口；路径配置里的 llama-server.exe 选择同理保留
  - 验证：162 例全绿；`py_compile` 通过

- [x] v1.19.2 「一键生成」+「删除该模型脚本」合并为「重置参数」（2026-09-24）：
  - `service/script_service.py`：新增 `reset_script(model_path, content)`（复用现有绑定名覆盖重建，无绑定用 `derive_name`；一个模型仍只一条绑定）；删除死代码 `delete_script` / `_remove_config_entry`（已无调用方）
  - `ui/app.py`：删除 `_generate_script` / `_delete_script`，新增 `_reset_script`（确认框 → 重置表单为默认参数 → `build_bat_content` 落盘 → 刷新原文视图/日志/按钮状态）；按钮布局改 `保存｜重置参数` 同行 + `查看生成的脚本`，模型路径行只留"浏览..."；运行前空表单提示改为"参数表单为空，请先勾选参数"（去掉过时的"请先一键生成…"）
  - `tests/test_script_model_binding.py`：新增 `TestResetScript` 3 例（沿用绑定名替换内容 / 无绑定用规范名 / 重复重置不增条目）
  - **顺手修复测试临时目录泄漏（traps #39）**：5 个测试文件的 `tempfile.mkdtemp()` 统一加 `self.addCleanup(shutil.rmtree, ...)`（`test_script_builder` 4 处 / `test_script_model_binding` 4 / `test_script_pin` / `test_download_entry` / `test_llamacpp_update` 2）——此前 `test_ctx_tier_by_size` 每次运行真写 1.5G+8G 假模型且不回收，本机累计 442 个 `tmp*` 目录约 489GB 把 C 盘写满（仅剩 8.1GB，跑测试报 `OSError: [Errno 28]`）；清理历史垃圾释放 478.8GB（盘恢复到 638GB 可用）
  - 验证：162 例全绿；跑完测试 `%TEMP%` 残留测试目录 = 0；`py_compile` 通过

- [x] v1.19.1 「运行控制」顶部按钮改"全部结束"（2026-09-24）：
  - `ui/app.py`：新增纯函数 `stop_all_targets(run_rows, runtime, legacy_running)`（清单 ∪ pids.json 运行时记录，去重保序；旧实例单独标识）；`_stop_script` 改为逐个结束全部目标并对每个失败容错，日志列出实际结束的模型名；`_sync_control_panel` 中 `stop_btn` 改为"任一模型在跑即可点"；删除 `run_list_title`（"正在运行:"）；按钮文案/工具提示更新（未跟踪实例指向下方"清理全部llama进程"）
  - `tests/test_run_control.py`：新增 6 例（清单/记录并集去重、旧实例识别、空目标、空串过滤）
  - 验证：159 例全绿；`py_compile` 通过

- [x] v1.19.0 脚本绑定唯一化（2026-09-24）：
  - `service/script_service.py`：新增纯函数 `name_model_score`（脚本名 token 命中模型文件名比例）与 `binding_rank`（契合度>置顶>最新保存）；`get_script_for_model` 改为候选择优（兜底 `derive_name`）；`_upsert_config_entry` 按归一化 model_path 判重（同模型即同一条，改名时 `_remove_bat` 清旧 .bat）；新增 `migrate_bindings()`（以 .bat 的 `-m` 校正 model_path / 同模型只留最优 / 幽灵条目剔除 / 备份到 `data/scripts_replaced` / 幂等）
  - `service/process_service.py`：新增 `remap_runtime(mapping)`，脚本名变更后同步 pids.json 键
  - `config/__init__.py`：新增 `REPLACED_SCRIPTS_DIR = "data/scripts_replaced"`
  - `ui/app.py`：`__init__` 插入 `_migrate_script_bindings()`（`_init_tray` 之后、`_load_saved_paths` 之前），清理结果写界面日志面板 + `info()` 落 `data/logs/app.log` 留痕
  - `tests/test_script_model_binding.py`：新增 `TestNameModelScore`(4) / `TestMigrateBindings`(5) / `TestBindingUniqueness`(3)
  - 验证：153 例全绿；真实数据副本干跑 18→15 条、备份 3 份 .bat、二次运行全 0（幂等）；关键文件 `py_compile` 通过
  - **实机执行（2026-09-24 01:13 启动）**：`data/scripts.json` 18→15 条、校正 2、清理 3、`data/scripts_replaced/` 落 3 份 .bat、`pids.json` 清空；复核 15 条全部无重复 model_path / 无缺失 .bat，逐条 `get_script_for_model` 均命中自身；再次调用迁移全 0（幂等）

- [x] v1.18.0 主控制页运行状态去重 + 运行中模型清单（2026-09-24）：
  - `ui/monitor_tab.py`：CompactMonitor 删除"状态"显示与"运行时长"（`_status_label`/`_uptime_label`/`_running`/`on_server_*`），只保留 CPU/RAM/GPU/t-s 纯负载；新增 `format_uptime` 纯函数（秒 → HH:MM:SS，非法/负值回退 --）
  - `ui/app.py`：运行控制新增**运行中模型清单**——每行 模型名｜运行时长｜"结束"按钮，1s `_uptime_timer` 逐行刷新；6 处钩子改走 `_on_model_started/_on_model_stopped/_on_all_models_stopped`；`_stop_single_model` 按脚本名 `stop_by_pid` 单点结束任一模型、旧实例（LEGACY_NAME="旧实例"）走全局 PID 回退
  - `tests/test_compact_monitor.py`：移除 2 例旧状态测试、新增 `format_uptime` 测试（净 -1）
  - 验证：141 例全绿；离屏冒烟通过（多模型增删/时长刷新/占位符切换）

- [x] v1.16.1 ctx 挡位化 + GGUF 元数据上限（2026-09-23）：
  - `service/script_builder.py`：`read_gguf_context_length`（GGUF 头 KV 扫描，命中 `*.context_length` 即停）+ `ctx_options_for`（挡位 + 默认值单一事实源）；`auto_generate_config` 的 ctx 改走元数据（上限<128K 默认取最大值、≥128K 默认 128K、读不到回退文件大小分级）
  - `ui/script_form_widget.py`：ctx_size 控件改可编辑下拉（挡位 "8K (8192)" 点选 + 任意值手填，NoInsert + QIntValidator）；`_update_ctx_tiers` 随模型刷新挡位；`get_config`/`set_preset`/`_reset_to_defaults` 适配可编辑下拉（traps #33）
  - `ui/app.py`：`_on_model_changed`/`_generate_script` 里 `set_model_path` 提前到 `set_preset` 之前（挡位默认值先就绪）
  - 验证：132 例全绿；本机真实模型实测（MiniCPM5-2B→上限 128K 默认 128K；Qwen3.8-27B 全系→上限 256K 默认 128K）；导入冒烟通过

- [x] v1.16.0 全量审查修复 + 后台化 + 死代码清理（2026-09-23）：16 项审查修复（R1-R16）+ misc + 文档同步，123 例全绿；详见版本历史表 v1.16.0 条目与 traps.md #24-#32
  - R1 `script_form_widget.set_preset`：切入新模型先整体重置表单，杜绝残留参数污染（traps #24）
  - R2 `chat/handlers.py`：rename/delete/clear/set-title 4 处读改写包 CONV_LOCK（traps #25）
  - R3/R8 `chat.js`：@多角色非流式回复按 msgIdx 定位（traps #26）+ pollConversation isSending 守卫防覆盖发送中消息
  - R4 `plan_version_cleanup`：排除运行中进程 exe 所在目录（traps #27）
  - R5 下载队列移除按钮 UserRole 现查行号；R6 追踪按钮 lambda 默认参数（traps #28）
  - R7 scheduler not-found 分支 rebuild_task_index 自愈（traps #29）
  - R10 `parse_bat_params` 引号值 + 短 flag 词边界（traps #30）；R11 update_cache 原子写
  - R9 恢复服务停止后 `_apply_script_statuses` 清 runtime + `on_server_stopped`；`_stop_script` 补同步
  - R12 Tailscale 探测 60s TTL 缓存 + TailscaleProbeWorker 后台刷新（traps #31）
  - R13 清理全部 llama 进程 KillAllLlamaWorker 后台 + `_on_cleanup_all_done` 回传重置
  - R14 旧版本清理规划 CleanupPlanWorker 两阶段；R15 merge_local_models 后台（含删 model_watch_tab 重复 refresh，traps #32）；R16 图片迁移后台
  - misc：托盘重启 Popen 补 NO_WINDOW、`_script_name_for` 大小写不敏感复用绑定、Settings.load 读侧归一、dest_path 归一、表单原文视图三来源统一刷新、模型下拉校验失败回滚
  - 死代码：删 MonitorTab/TpsChart/HistoryChart（monitor_tab 仅存 CompactMonitor）、NewScriptDialog、CheckUpdate/CheckAppUpdateWorker、history_worker、update_workers（compare_semver 迁 `utils/semver.py`）、validator.sanitize_filename、monitor_service.load_history/_downsample；requirements 移除 PyQt6-Charts

- [x] v1.14.0 脚本一键生成 + 表单式编辑器（2026-09-22）：
  - `service/script_builder.py`：`auto_generate_config`（选模型自动算 alias/ctx 分级/KV 量化/gpu 层/host/挂 mmproj）+ `parse_bat_params`/`parse_model_path_from_bat`（反向解析 .bat 回填表单）；修解析时 `\b` 在 `^` 续行后不匹配的问题
  - `ui/script_form_widget.py`（新）：表单式脚本编辑器，按 CATEGORIES 渲染填空/下拉/勾选 + 脚本名 + 模型只读；set_preset/get_config/set_name/clear_form
  - `ui/app.py`：脚本区右侧换表单 + "查看生成的脚本"折叠原文；按钮行加"一键生成"；`_on_script_selected` 反解回填、`_save_script`/`_generate_script`/`_new_script` 改表单、`_run_script`/端口改写/外网地址全部以表单为事实源；选模型/加载时同步表单模型显示
  - 测试：新增 `TestAutoGenerateAndParse` 4 例（auto 基础字段/ctx 分级/build→parse roundtrip/开关存在）；116 全绿

- [x] v1.13.1 追踪独立标签页 + 移除生效（2026-09-22）：
  - `ui/model_watch_tab.py`（新）：主窗口独立"模型更新追踪"标签页，从 model_tab 抽出；首次不预跑网络，`recall()` 由主窗口切 tab 触发（合并新本地模型 + 空闲则检查）
  - `ui/app.py`：注册 `model_watch_tab` 标签；`currentChanged` 切到该页调 `recall()`
  - `ui/model_tab.py`：移出追踪视图，保留搜索结果"追踪"按钮（`_watch_model` 改全局读写，`_do_search` 刷新关注列表使按钮状态一致）
  - `service/watchlist_service.py`：修复"移除没效果"——新增 `WATCHLIST_IGNORED_FILE` 持久化 ignored 集合，`remove_model` 记 ignore，`merge_local_models` 跳过 ignored，`add_manual_model` 解除 ignore
  - 测试：新增 `TestIgnoreRemove` 2 例；112 全绿

- [x] v1.12.0 清理旧版本功能（2026-09-22）：
  - `service/llamacpp_update_service.py`：`plan_version_cleanup`（保留当前 + 每变体系列最新 2 个，返回待删）、`delete_version_dir`（删目录返释放字节）、`plan_zip_cleanup`（清已装版本对应的下载缓存 zip，cudart/未装保守保留）、`delete_zip`
  - `ui/update_tab.py`："已安装版本"组新增"清理旧版本"按钮；确认框列出待删版本+zip 及释放空间，默认取消防误触；预览删除、单个失败不中断其余、失败项提示"可能被运行中服务占用"
  - 规则说明：当前使用版本恒保留，每个变体系列（cuda-13.3/13.4/cpu...）各保留最新 2 个供回退，超出清理
  - 实测：本机 8 版本 → 保留 4（b11093 当前 + b11065 + b10936/b10934），删 4 + zip 8，释放约 1.9GB
  - 测试：新增 `TestCleanup` 4 例（规则保留/阈值内全留/zip 归属/删除返大小）；105 全绿

- [x] v1.11.0 全量代码审查修复（2026-09-13）：三路并行审查（service+chat / ui / model+utils+config），修复 16 处明显 bug 并补 3 例测试（101 例全绿）；已知低优先级残留：`_stop_script`/`_cleanup_all_processes` 在 GUI 线程同步 taskkill（用户主动一次性操作，卡顿 <1s 可接受）；chat handler 读在锁外的极小覆盖窗口（traps #20）

- [x] v1.10.2 置顶按钮 + 列宽优化（2026-09-13）：
  - 脚本列表每行内置 📌 按钮（QToolButton，橙色=已置顶/灰色=未置顶，点击切换置顶），替代"文本列 + 右键"的隐蔽入口；右键菜单保留为补充
  - 列宽：脚本名 Stretch 自动拉伸占满、状态 ResizeToContents、置顶列 Fixed 34px
  - 验证：ui.app 导入正常、98 例全绿

- [x] v1.10.1 自动下载 / 进度条修复（2026-09-13）：
  - traps #18：GitHub release 资产逐步上传 → 最新版可能"只有 cudart"无主包
    - `llamacpp_update_service._release_from_api`：无主包资产 release 丢弃
    - `fetch_releases`：缓存命中时校验最新 release 完整性，不完整强制重抓（自愈）
    - `plan_auto_download`：支持传整个 releases 列表，跳过不完整快照取第一个可下载版本（单 dict 兼容）；`update_tab._maybe_auto_download` 改传列表
    - 清理存量不完整缓存；实测 GitHub 最新 b10934（完整），计划 `[('b10934','cuda-13.3')]`
  - traps #19：ModelScope 元数据 file_size 失真（MiniCPM5 744MB vs 实际 5GB）
    - `download_service._on_progress`：total>0 时用 HTTP 完整大小覆盖 file_size
    - `_on_finished`：完成后以实际大小校准 file_size（收敛 100%）
    - `download_entry.progress`：clamp 0-100
  - 测试：plan_auto_download 列表用例 5 + progress clamp 4；98 例全绿

- [x] v1.10.0 托盘重启 + 脚本置顶（2026-09-13）：
  - 托盘菜单新增"重启"：`_restart_app` 用 `sys.executable + sys.argv` 重启（打包后为 exe），先 Popen 再 close；`main()` 单实例锁 tryLock 失败时 10 次×150ms 短暂重试，等旧实例释放锁
  - 脚本置顶：`ScriptEntry` 新增 `pinned` 字段（to_dict/from_dict 持久化）；`ScriptService._upsert_config_entry` 保存 pinned；`ui/app.py` 脚本列表改三列（脚本/状态/置顶📌）、右键菜单置顶/取消置顶、新建/新保存默认置顶、`_refresh_script_list` 稳定排序置顶在前
  - 脚本面板垂直空间加大：`control_layout` 中脚本面板 stretch=2
  - 测试：新增 `tests/test_script_pin.py` 5 例（默认不置顶、roundtrip、缺字段回退、save/load 保留、存量 json 兼容）；90 例全绿

- [x] v1.9.2 主控制页嵌入压缩监控（2026-09-13）：
  - `ui/monitor_tab.py`：新增 `_GpuRow`（两行紧凑 GPU 卡）+ `CompactMonitor`（CPU/RAM/GPU + t/s + 运行时长 + 状态），复用 `_bar_style`/`_fmt_bytes`，与 MonitorTab 同连 `metrics_updated`
  - `ui/app.py`：创建 `monitor_compact`；主控制页日志与压缩监控**并排**（stretch 3:1）；启动/停止/聚焦/tps 5 处调用与信号双发（`on_server_started`/`on_server_stopped`/`set_focus_script`/`tps_signal`）
  - 完整功能仍在"性能监控"标签（采样频率/历史/图表），压缩版仅展示
  - 测试：新增 `tests/test_compact_monitor.py` 7 例（CPU/RAM/tps 推送/日志回退/聚焦过滤/GPU 增删与占位符恢复/启停状态）；85 例全绿

- [x] v1.9.1 新建脚本自动命名联动（2026-09-13）：
  - `ui/dialogs/new_script_dialog.py`：新增"脚本名称"输入行（预填 default_name）+ `get_name()`
  - `ui/app.py`：`_new_script` 去掉 QInputDialog 手输弹窗，默认名取所选模型文件名（去扩展名）传入对话框，可在参数对话框内改；空名校验
  - `ui/app.py`：模型下拉改显示文件名（完整路径存 userData），`findData` 精确匹配当前模型；`_save_script` 空名分支预填模型名
  - `service/llamacpp_update_service.py`：`switch_version` 写 settings.llamacpp_path 前显式 normalize（不依赖 setter）
  - 测试：适配 v1.9.0 路径规范（find_mmproj / build_bat_content / replace_bat_dir / switch_version 断言改正斜线），新增自动命名 3 用例；78 例全绿

- [x] v1.9.0 路径分隔符统一规范（2026-09-13）：
  - 新增 `utils/path_utils.py`：`normalize_path()`（`\` → `/`，幂等），确立正斜线 `/` 为唯一规范形式（跨平台 / JSON 免转义 / Qt 原生 / cmd 与 llama-server 兼容）
  - `config/config.py`：6 个路径 setter（llamacpp_path / model_path / model_dir / visual_model_path / download_path / llamacpp_install_root）统一过 normalize_path
  - `service/script_builder.py`：`build_bat_content` 对 exe_dir / model_path / visual_model_path 入口统一；`find_mmproj` 返回前规范化
  - `service/llamacpp_update_service.py`：`replace_bat_dir` 的 new_dir 统一正斜线（原 normpath 产反斜线）
  - 一次性迁移存量：17 个 .bat + scripts.json + app_config.json 全部路径分隔符改为 `/`
  - 顺带修复 traps #17：MiniCPM5 脚本名含空格 → 登记 bat 实际是 sanitize 后的 `Mini_CPM5-2B.bat`（此前只改了空格版孤儿），已同步内容并删除孤儿
  - 验证：登记脚本 17 个全部通过（sanitize 文件名 bat/json 一致、无 --mmap、无残留反斜线、无孤儿文件）；app_config.json 路径统一；全模块导入冒烟通过

- [x] v1.8.1 MiniCPM5 脚本 + 移除 `--mmap`（2026-09-13）：
  - 新增 `Mini CPM5-2B` 启动脚本：模型 `C:/modelscope/MiniCPM5-2B-F16.gguf`（纯文本，无 mmproj），端口 8080、ctx 200000、gpu-layers 99
  - traps #15：b10883 已移除 `--mmap`（改 `--load-mode`），批量清理 7 个脚本（Qwen3.8-27B / gemma-heretic / GLM-4.7-Flash / gemma-4-26B / IQ3 / IQ2 / MiniCPM5）的 `--mmap`，.bat 与 scripts.json 同步
  - 修正 MiniCPM5 条目：相对 cd 路径改绝对路径；model_path 由 Qwen3.8-27B-UD-IQ2_S 改为 MiniCPM5-2B-F16
  - 验证：17 个脚本全部通过（无 --mmap、cd 绝对路径、bat/json 一致、model_path 有效、port 8080、ctx≥128000）

- [x] v1.7.1 后台静默下载（2026-09-04）：
  - `service/llamacpp_update_service.py`：`plan_auto_download()` 按当前通道（CUDA 大版本系列匹配）+ 推荐变体规划，去重、跳过已装、通道无更新时只装推荐
  - `ui/update_tab.py`：`_maybe_auto_download()`（本地信息 + releases 双就绪后触发，`_auto_handled_tag` 防重复）、`_start_next_auto()`（队列顺序执行）；静默完成/失败不弹窗，文案"后台下载完成: tag（已就绪，可切换）"；traps #14 三处标志修复
  - `ui/workers/update_manager_workers.py`：InstallWorker 增加 `dll_sources` 参数，`_ensure_cuda_dlls()` 新目录缺 cudart/cublas 时从现有目录按大版本复制
  - `config/config.py`：新增 `llamacpp_auto_download`（默认开），"下载设置"组提供开关
  - 验证：75 例单测全绿（新增 TestPlanAutoDownload / TestCudartPlan 等 20 例）；冒烟测试覆盖开关关闭不触发、单任务（通道=推荐去重）、静默完成不弹窗、双通道（cuda-12.4 本地 + 驱动支持 13 → 两任务顺序入队），跑完即删

- [x] v1.7.0 llama.cpp 版本管理（2026-09-04）：
  - `service/llamacpp_update_service.py`：releases 解析（资产名正则 + cudart 识别，1h 缓存复用 update_cache.json）、`llama-server --version` 双格式 build 号解析、NVML 驱动检测与 CUDA 系列推荐、zip 安装（防穿越 + 元数据 + 原子改名）、`switch_version`（Settings + ScriptService 联动批量替换 .bat 路径，兼容正/反斜杠历史写法）
  - `ui/workers/update_manager_workers.py`：LocalInfoWorker / ReleasesWorker / InstallWorker（断点续传 + 取消保留半成品 + SHA256 + 多文件聚合进度；"暂停"= 取消后重建 worker 自动续传）
  - `ui/update_tab.py`：本机状态 / 可用版本（变体下拉按 release 实际资产生成，推荐项标注）/ 下载进度 / 已安装版本（行内"切换"按钮）/ 下载设置（镜像前缀 + 安装根目录）
  - `ui/app.py`：第 4 个标签页接入 + `version_switched` 信号刷新主控制页路径
  - `config/config.py`：新增 `gh_mirror_prefix`、`llamacpp_install_root`
  - 验证：55 例单测全绿；offscreen 冒烟（含真实本地检测 b10453、驱动 616.56 → 推荐 CUDA 13.3）；E2E 真实下载（直连与 gh-proxy.com 镜像均通过，SHA256 匹配、安装识别正常）；实测 ghfast.top 超时不可用（traps #13）
  - 待用户重启 GUI 交互验证：旧实例（v1.6.0）运行中占用单实例锁，需退出托盘后重启

- [x] 克隆 `https://github.com/kkblank/Llamacpp-gui` 到本地

- [x] 创建 Python 3.12 虚拟环境 `.venv`

- [x] 通过清华镜像安装依赖（pyqt6, psutil, nvidia-ml-py, PyQt6-Charts, Pillow）

- [x] 验证全部模块导入正常

- [x] 启动 GUI 验证运行正常，data/ 运行时目录自动生成

- [x] 新增 `service/autostart_service.py`：HKCU Run 注册表项写入/删除/查询

- [x] `ui/app.py`：系统托盘初始化、关闭隐藏到托盘、托盘菜单（显示/退出/自启动开关）

- [x] `ui/dialogs/settings_dialog.py`：设置对话框增加"开机自动启动"复选框

- [x] 启用开机自启动（注册表 Run 项已写入）

- [x] 创建桌面快捷方式 `LlamaCPP GUI.lnk`（指向 .venv pythonw + main.py）

- [x] `config/config.py`：新增 `model_dir` 配置

- [x] 新增 `service/model_scanner.py`：递归扫描目录 .gguf

- [x] `ui/app.py`：模型目录选择 + 下拉快速选择模型（保留手动浏览兜底）

- [x] 基于本地模型目录现有模型，预生成 6 个启动脚本（`data/scripts/*.bat` + `scripts.json`）

- [x] 扩展为**全部 14 个完整 gguf 主模型**各一个脚本：上下文按文件尺寸分级（<2.5G→65536，<6G→32768，否则→16384），>12G 模型 `gpu-layers 48` + `--mmap` CPU offload，含 mmproj 的目录自动绑定视觉模型
  - 端口 8080\~8093 依次分配；视觉模型：gemma-12b-heretic、Ministral-14B/3B、gemma-4-12B/26B-A4B/E2B/E4B
  - **v1.8.0 统一为端口 8080**（单模型运行），ctx-size 最低 128000

- [x] 修复所有启动脚本无法运行的根因：`process_service.start_script()` 由列表传参改为整串命令 + `cmd /d /s /c` + `call` + 双引号包路径；实测 llama-server 正常启动（traps #4，v1.4.0）

- [x] 修复控制台弹窗与界面卡顿：7 处子进程统一加 `CREATE_NO_WINDOW`；新增 `ui/workers/status_worker.py`（StatusPoller QThread）把 tasklist 状态轮询移出 GUI 线程，信号回传渲染（traps #5，v1.4.1）

- [x] 修复 #5 引入的启动顺序 bug：StatusPoller 创建提前到 \_init\_ui/\_restore\_service\_state 之前，实际启动 GUI 验证运行正常（traps #6，v1.4.2）；清理系统里残留的旧 GUI 实例后重新启动

- [x] 聊天界面 API 地址自动获取：`chat/handlers.py` 新增 `GET /bridge/info`；`chat/server.py` 的 `start_bridge(llm_url_provider=...)` 注入模型地址 provider；`ui/app.py` 新增 `_current_llm_url()` 传入；前端 `chat.js` `init` 未手动保存时自动填充（traps #7，v1.4.3）

- [x] Tailscale 外网接入（v1.5.0）：
  - `service/tailscale.py`：多路径检测本机 Tailscale IPv4（PATH 命令 → 已知安装路径 → psutil 网卡枚举 100.64.0.0/10 兜底），未检测到返回空串

  - `service/script_builder.py`：`--host` 由固定值改为下拉选项（仅本机 127.0.0.1 / 所有接口 0.0.0.0 / Tailscale 专用哨兵 `__tailscale__`），新增 `extract_host()` 解析脚本监听地址

  - `ui/dialogs/new_script_dialog.py`：`--host` 渲染为下拉框，哨兵值在打开时解析为实际 Tailscale IP（未检测到回退 0.0.0.0 保证可启动）

  - `service/process_service.py` + `ui/workers/log_worker.py`：运行时状态 pids.json 增加 `host` 字段记录监听地址

  - `ui/app.py`：`_current_llm_url()` 回退地址 host 感知（Tailscale IP 时用该 IP，保证本地浏览器可达）；服务就绪后若以 0.0.0.0/Tailscale IP 监听则显示外网访问 URL + 复制按钮，停止时隐藏（traps #8）

- [x] 全量代码审查 + 一次性修复（v1.6.0，2026-09-03）：
  - **功能缺陷**：新建脚本对话框 QComboBox.text() 崩溃（traps #9）；恢复运行中服务时 GUI 线程跑 tasklist 回归（#11）；`/metrics` 按实际 host 请求（#10）；下载暂停后换目录续传文件损坏（#12）；退出程序时 LogWorker 未停止的崩溃风险（closeEvent 通知+短暂等待+保引用）；remove_download 弹出运行中 QThread 引用；多服务器 `_server_url/_ts_url` 全局单值串扰（改按脚本归属）；"编辑器内容≠磁盘内容"仍执行旧 .bat（运行前自动保存）
  - **安全加固**：聊天页 marked.parse 无消毒 XSS（chat.js 新增 sanitizeHtml）；桥服务 CORS `*` 全开（改 Origin 同源校验，跨站 403）；静态文件 startswith 防穿越改 commonpath 严格判定
  - **中优先级**：模型搜索/文件列表移后台 QThread（新增 `ui/workers/search_worker.py`，GUI 不再冻结 15s）；端口预检按实际 host + 冲突自动顺延下一个可用端口；定时任务应用角色采样参数（call_llm 支持 sampling）；无 llm_url 时任务退避推进 next_run_time；Settings.load 类型校验；stop_by_pid 校验进程真死而非 taskkill 返回码；单实例锁（QLockFile，data/app.lock）
  - **UX 快赢**：脚本列表双击=运行；下载完成托盘通知；"服务就绪自动打开聊天页"设置开关（默认关）；设置对话框 `--host` 与新建对话框统一为三选项下拉（未知历史值追加为选项不静默改写）；监控页运行中状态色与主控制页统一（橙）；logger 在 pythonw 下只留文件日志
  - **测试**：新增 `tests/`（script_builder 纯函数 / 新建对话框回归 / semver 与防穿越，20 用例，offscreen 可跑）；全部通过 + 全模块导入冒烟通过

## 已知问题

- 暂无。已修复注意项见 `traps.md` #1~#33。
- 审查中未纳入本次修复的低优先级项：`last_pid.pid` 语义陈旧（多服务器下仅剩回退用途，可规划废弃）；首次运行向导、日志面板关键字过滤（后续待办）。

## 后续待办

- [ ] 打开应用 → 主控制页脚本列表应显示上述 6 个脚本，任选其一运行

- [ ] 使用聊天 Web UI

- [ ] 如需 MTP（多token预测）加速，可在模型参数中为 Qwen3.8-27B 开启 `--spec-type draft-mtp`

