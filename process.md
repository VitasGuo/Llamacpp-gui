# 项目目标与进度

## 现状

- **目标**：在本地 Windows 环境运行 LlamaCPP GUI（PyQt6 桌面客户端），用于管理本地 llama.cpp 推理服务器。

- **当前版本**：v1.7.1（2026-09-04，后台静默下载修复）

## 版本历史

| 版本     | 日期         | 说明                                                                                                                                                       |
| ------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
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

