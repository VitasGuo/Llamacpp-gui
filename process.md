# 项目目标与进度

## 现状

- **目标**：在本地 Windows 环境运行 LlamaCPP GUI（PyQt6 桌面客户端），用于管理本地 llama.cpp 推理服务器。

- **当前版本**：v1.5.0（2026-09-02，Tailscale 外网接入）

## 版本历史

| 版本     | 日期         | 说明                                                                                                                                                       |
| ------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
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

## 已知问题

- 暂无。已修复注意项见 `traps.md` #1\~#8。

## 后续待办

- [ ] 打开应用 → 主控制页脚本列表应显示上述 6 个脚本，任选其一运行

- [ ] 使用聊天 Web UI

- [ ] 如需 MTP（多token预测）加速，可在模型参数中为 Qwen3.8-27B 开启 `--spec-type draft-mtp`

