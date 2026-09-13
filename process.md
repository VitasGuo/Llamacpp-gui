# 项目目标与进度

## 现状

- **目标**：在本地 Windows 环境运行 LlamaCPP GUI（PyQt6 桌面客户端），用于管理本地 llama.cpp 推理服务器。

- **当前版本**：v1.11.0（2026-09-13，全量代码审查修复）

## 版本历史

| 版本     | 日期         | 说明                                                                                                                                                       |
| ------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
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

- 暂无。已修复注意项见 `traps.md` #1~#12。
- 审查中未纳入本次修复的低优先级项：`last_pid.pid` 语义陈旧（多服务器下仅剩回退用途，可规划废弃）；首次运行向导、日志面板关键字过滤（后续待办）。

## 后续待办

- [ ] 打开应用 → 主控制页脚本列表应显示上述 6 个脚本，任选其一运行

- [ ] 使用聊天 Web UI

- [ ] 如需 MTP（多token预测）加速，可在模型参数中为 Qwen3.8-27B 开启 `--spec-type draft-mtp`

