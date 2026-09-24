# 踩坑记录

## #1 绘制生成的图标不能直接传给 QIcon 接口

**现象**：启动 GUI 报 `TypeError: setWindowIcon(self, icon: QIcon): argument 1 has unexpected type 'QPixmap'`

**根因**：`ui/app.py` 新增的 `_create_app_icon()` 用 QPainter 绘制后返回 `pixmap`（QPixmap），
而 `QSystemTrayIcon.setIcon` / `QWidget.setWindowIcon` 都要求 `QIcon` 类型。

**解决方案**：`ui/app.py` 的 `_create_app_icon()` 末尾改为 `return QIcon(pixmap)`，并在
QtGui 导入中补上 `QIcon`（见 [ui/app.py 的 \_create\_app\_icon](ui/app.py)）。

**教训**：PyQt6 中绘制类（QPixmap）与图标类（QIcon）不通用；凡是 `*Icon*` 接口的入参
都要用 `QIcon` 包装，不能直接丢 QPixmap。

***

**当前状态**：#1 已修复（v1.1.0）。

***

## #2 QProgressBar 内建百分比文本渲染乱码

**现象**：性能监控页各进度条（CPU/RAM/GPU 利用率/显存/温度）中部跳动的百分比数字
显示为乱码；而进度条旁的 QLabel 数字、中文标签均正常。

**根因**：`ui/monitor_tab.py` 用到 `QProgressBar`，其自带的 value 文本（默认格式
`%p%`）在条中部渲染时异常（该环境下的字体/渲染路径问题）。而百分比本已由进度条
右侧/旁的 `QLabel`（util\_label / mem\_label 等）正常显示，条上内建文本属于冗余。

**解决方案**：`ui/monitor_tab.py` 对 5 个进度条（GpuCard 的 util\_bar/mem\_bar/temp\_bar，
系统资源的 `_cpu_bar/_ram_bar`）统一调用 `setTextVisible(False)` 关闭内建文本，
百分比继续由两侧正常 QLabel 显示——既消除乱码，又避免同一数值重复显示。

**教训**：进度条百分比若已用独立 QLabel 展示，应 `setTextVisible(False)` 关掉条内
内建文本，勿依赖其自渲染文本。

***

**当前状态**：#2 已修复（v1.2.1）。**2026-09-24 复发（v1.19.4 修复）**：
`ui/model_tab.py` 下载队列"进度"列的 QProgressBar 一直没关内建文本——条上数字
渲染成乱码，用户描述为"百分比的数字好像是用中文写的，看不懂具体数字"。
按同一方案修复：新增模块级 `_make_progress_cell(value)` / `_set_progress_cell(cell, value)`
（条 `setTextVisible(False)` + 右侧 QLabel 显示 `NN%`），`_add_queue_row` /
`_update_queue_row` 改走这对函数；并加 offscreen 回归测试
`tests/test_download_progress_cell.py`（断言条内文本关闭、QLabel 文本只含 ASCII 数字）。

**教训补充**：**每新增一个 QProgressBar，都要同时决定"百分比文本由谁显示"**——
漏一个就复发一次（本次是 `model_tab` 漏了，`monitor_tab` / `update_tab` 当初都已按
约定处理）。判据：条上若有自带文本需求，一律 `setTextVisible(False)` + 旁侧 QLabel。

***

## #3 按"前缀 mmproj-"识别视觉投影漏掉非标准命名

**现象**：批量生成脚本时，`gemma-4-12b-heretic-abliterated-GGUF` 目录下的
`gemma-4-12b-heretic-mmproj-f16.gguf` 未被识别为视觉投影，被误当成主模型，
多生成一个脚本，且该模型未绑定 mmproj。

**根因**：一次性生成脚本用 `basename.lower().startswith("mmproj-")` 判断投影文件，
而该模型 mmproj 文件叫 `...-mmproj-f16.gguf`（mmproj 在中间，不在前缀）。
前缀匹配规则不通用。

**解决方案**：识别逻辑改为 `"mmproj" in basename.lower()`（子串包含），主模型筛选
同步排除含 mmproj 的文件；重跑后 14 个脚本全部正确、视觉模型完整绑定。

**教训**：对文件"类型"的判定不要信任固定前缀，不同模型家族的命名不一定统一；
用"是否包含关键子串"更稳。此坑只涉及一次性生成逻辑，未进入 src，属开发期踩坑。

***

## #4 cmd /c 后紧跟以引号开头的参数，&& 被拆命令导致 .bat 无法执行

**现象**：点击任意启动脚本，日志出现
`'"C:\...\data\scripts\XXX.bat"' 不是内部或外部命令，也不是可运行的程序或批处理文件`，
进程秒退（PID 每次不同，均立即结束）。

**根因**：`service/process_service.py` 的 `start_script()` 原本用
`["cmd", "/c", f'chcp 65001 >nul && "{bat_path}"']`（列表传参）。Windows 对
`cmd /c` 后紧跟以引号开头的参数有特殊剥离规则，`&&` 又会把命令拆成两段，
最终 bat 路径被单条命令解析，找不到文件。

**解决方案**：改为整串命令 + `cmd /d /s /c` + `call`，并保证 bat 路径用双引号包裹：

```python
cmdline = f'cmd /d /s /c "chcp 65001>nul && call \'{bat_path}\'"'.replace("'", '"')
process = subprocess.Popen(cmdline, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000, ...)
```

注意：cmd 不把单引号当引号，必须用双引号包路径，故用 `.replace("'", '"')` 统一。
已实测 llama-server 正常启动并输出日志（[process\_service.py](service/process_service.py#L26-L39)）。

**教训**：`cmd /c` 传参在 Python 里最稳的写法是「单字符串 + `/d /s /c` + `call` +
双引号包路径」，不要用列表里内嵌引号的写法。本坑影响所有脚本的启动，属核心路径。

***

**当前状态**：#4 已修复（v1.4.0）。

***

## #5 无控制台启动时子进程弹黑色 cmd 窗口 + 状态轮询卡 GUI

**现象**：GUI 运行期间频繁闪现黑色命令行窗口，界面严重卡顿、无法操作。
配合自动启动（pythonw 无控制台）时尤其明显。

**根因**（两层）：

1. `service/process_service.py` 里所有 `subprocess.run(["tasklist"/"taskkill"])`
   未指定 `creationflags=CREATE_NO_WINDOW`。GUI 以 pythonw（无控制台）运行时，
   每个子进程都会弹出黑色 cmd 窗口。
2. `ui/app.py` 的 `_refresh_script_statuses()` 在 **GUI 线程** 每 2s 同步执行
   `tasklist`（实测单次约 0.6s），阻塞主线程事件循环 → 界面卡顿。

**解决方案**：

1. `process_service.py` 顶部加 `NO_WINDOW = 0x08000000`，给 7 处 `subprocess.run/Popen`
   全部补 `creationflags=NO_WINDOW`，杜绝任何控制台窗口。
2. 新建 `ui/workers/status_worker.py`（`StatusPoller` QThread）：后台线程做 tasklist
   存活探测，通过 `status_refreshed` 信号回传结果；GUI 线程只负责渲染。`app.py`
   用 `StatusPoller` 替换原 `_status_timer`，`_refresh_script_statuses` 改为只调
   `request_poll()` 请求后台探测。

**教训**：Windows GUI 程序（尤其 pythonw/pyinstaller -w）里 spawn 子进程必须
统一带 `CREATE_NO_WINDOW`；任何会周期性执行且耗时（>几十 ms）的后台探测
（tasklist/IO/网络）都不能放 GUI 线程，应移到 QThread/后台线程 + 信号回传。
本坑影响所有脚本的启动与整个界面的流畅度，属核心路径。

***

**当前状态**：#5 已修复（v1.4.1）。

***

## #6 MainWindow\.__init__ 里 \_status\_poller 创建晚于 \_restore\_service\_state，启动即崩溃

**现象**：改为后台轮询后，GUI 启动立即崩溃退出（pythonw 无任何提示，表现为"点了没反应/
程序秒退"）。用控制台 python 跑 main.py 得到：

```
File "ui/app.py", line 415, in _refresh_script_statuses
    self._status_poller.request_poll()
AttributeError: 'MainWindow' object has no attribute '_status_poller'
```

**根因**：`MainWindow.__init__` 中 `_restore_service_state()`（会调用
`_refresh_script_statuses()` → `self._status_poller.request_poll()`）排在
`self._status_poller = StatusPoller(...)` 创建**之前**执行，属性尚不存在。

**解决方案**：把 `StatusPoller` 的创建/连接/start 提前到 `_init_ui()`、
`_restore_service_state()` 之前（见 [ui/app.py](ui/app.py#L66-L72)），
并在注释中说明该顺序约束。

**教训**：把周期性/异步组件从 QTimer 改为后台线程时，必须检查 __init__ 内所有
**同步调用点**是否在组件创建之后；这类"创建顺序" bug 在 pythonw（无窗口）下
表现为静默崩溃，极易误判为"没生效"或"还是老样子"。验证不能只靠语法检查，
必须实际启动 GUI。本坑影响 GUI 启动，属核心路径。

***

**当前状态**：#6 已修复（v1.4.2）。

***

## #7 旧 GUI 实例残留占用桥端口，改动桥服务后测试假 404 / 新代码不生效

**现象**：改完 `chat/handlers.py` 新增 `GET /bridge/info` 后，写单测起
`start_bridge()` 去请求 `/bridge/info` 一直返回 404 `{"error":"not found"}`，
而 `/settings`、`/conversations` 等旧接口正常；甚至往 `do_GET` 顶部加 debug
print 都不打印，仿佛新代码没加载。`start_bridge()` 却报告"绑定成功，端口 18765"。

**根因**：机器上残留着旧版本 GUI 的 `pythonw` 进程（可能多个），其桥服务
仍监听 `data/chat/bridge_port.txt` 里记录的 18765。Python `HTTPServer` 默认
`allow_reuse_address=1`（Windows 的 SO\_REUSEADDR 允许重叠绑定），新起的测试
服务器 bind 18765 "成功"并返回该端口，但实际 TCP 连接被 OS 交给**旧进程**的
socket —— 旧代码没有 `/bridge/info`，走 `_serve_static` 返回 404；debug print
也打在无人接收的 socket 上，自然看不到。

**解决方案**：验证桥服务改动前先确认没有旧 GUI/pythonw 实例占用桥端口
（`Get-NetTCPConnection -LocalPort <port> -State Listen` / `Get-Process pythonw`）；
测试时临时改名 `data/chat/bridge_port.txt` 让新服务绑定**全新端口**，验证完再恢复。
真实使用场景中：改了桥服务代码后，**必须完全退出（含托盘）所有旧实例再重启**，
否则旧桥继续用内存里的旧代码。

**教训**：调试"本地端口服务"时，`bind 成功` ≠ `请求到达我的服务`。Windows 的
SO\_REUSEADDR 会让新进程对同一端口"假成功"重叠绑定，请求仍由先绑定者接收。
排查顺序：先看该端口被哪个进程监听、有无残留 GUI，再怀疑自己的代码。
（项目记忆里"测试新版本前先清掉旧 GUI 实例"即指此坑。）

***

## #8 检测 Tailscale IP 时命令不在 PATH，需多路径兜底

**现象**：本机已安装并登录 Tailscale（`tailscale status` 正常），但 GUI（尤其
`pythonw` 无控制台 / PyInstaller 打包场景）里直接 `subprocess.run(["tailscale", "ip", "-4"])`
返回空，检测不到 IP，导致"Tailscale 专用"监听选项拿不到地址。

**根因**：`tailscale.exe` 默认装在 `C:\Program Files\Tailscale\`，该目录**不一定在
GUI 进程的 PATH** 里（GUI 由资源管理器/快捷方式启动时 PATH 与命令行不同）；
`tailscale ip -4` 输出是本机 Tailscale IP（CGNAT 100.64.0.0/10 段，形如 `100.x.x.x`），若不加校验还可能
误收其他地址。

**解决方案**：`service/tailscale.py` 按三级顺序探测并做精确段校验：

1. PATH 中的 `tailscale` 命令；2. 已知安装路径 `C:\Program Files\Tailscale\tailscale.exe`；
2. `psutil.net_if_addrs()` 枚举网卡匹配 100.64.0.0/10 段的 IPv4 兜底。
   每级结果都用 `is_tailscale_ip()` 校验落在 CGNAT 段内才采用；全部失败返回空串，
   对话框侧回退 `0.0.0.0` 保证脚本仍能启动。

**教训**：GUI/打包程序里"外部可执行文件是否在 PATH"不可假设，要有已知路径 +
系统 API（psutil 网卡枚举）多级兜底；对检测结果做协议/网段校验能避免误收。
（Tailscale 的 CGNAT 段 100.64.0.0/10 是判断"这是 Tailscale 地址"的可靠依据。）

***

## #9 新建脚本对话框对 QComboBox 调 .text()，勾选 --host 必崩（v1.5.0 回归）

**现象**：打开"新建启动脚本"对话框，通用参数组默认整组勾选，点"确定"立即崩溃：
`AttributeError: 'QComboBox' object has no attribute 'text'`。v1.5.0 之后新建脚本功能一次都没成功过。

**根因**：`ui/dialogs/new_script_dialog.py` 的 `get_config()` 对所有值控件统一调 `.text().strip()`，
但 v1.5.0 把 `--host` 的值控件从 QLineEdit 改成了 QComboBox（`_build_choice_widget`），
QComboBox 根本没有 `text()` 方法。当时验证 Tailscale 时是直接手改 14 个 .bat 的 `--host`，
绕过了新建对话框，所以没暴露。

**解决方案**：`get_config()` 区分控件类型——`sw["choices"]` 存在时用 `currentData()`
取实际值（如 Tailscale IP），其余仍走 `.text()`。并补回归测试
`tests/test_new_script_dialog.py`（offscreen 平台跑 QApplication，勾选全部参数断言不崩溃）。
（v1.6.0 修复）

**教训**：把"控件类型换掉"类改动合入时，至少要把受影响的交互路径双击一遍——
本坑有最基本的新建脚本冒烟测试即可当场拦下。同型隐患：设置对话框 host 字段
也一并改成同一套下拉（避免两处语义不一致）。

***

## #10 监控 /metrics 固定请求 127.0.0.1，Tailscale 监听时精确 t/s 恒失效

**现象**：全部脚本 `--host <Tailscale IP>` 后，性能监控页 t/s 只能靠日志正则回退，
`/metrics` 精确计数（含 idle 期归零）从不生效，日志每 60s 一条
`/metrics 抓取失败 [...] 127.0.0.1:808x: ...连接拒绝`。

**根因**：`service/monitor_service.py` `_fetch_server_tps()` 硬编码
`http://127.0.0.1:{port}/metrics`。llama-server 绑定 Tailscale IP（100.x.x.x）时
**不监听回环地址**，127.0.0.1 连接必然被拒。而 pids.json 里明明已记录了 host 字段却没用上。

**解决方案**：`_poll_servers_once` 取 runtime entry 的 `host` 传入
`_fetch_server_tps(name, port, host)`；`0.0.0.0`/空值回退 127.0.0.1（绑定所有接口时回环可达）。
本机请求绑定在 Tailscale IP 的服务用该 IP 即可（它是本机网卡地址）。
（v1.6.0 修复）

**教训**：任何"服务地址"都不能假设 127.0.0.1 可达——`--host` 可配置后，
所有客户端（metrics、聊天回退地址、端口探测）都要跟着实际 host 走。
排查这类问题的最快方法：看 app.log 里被节流的 `/metrics 抓取失败` 条目。

***

## #11 恢复运行中服务时 GUI 线程又跑起 tasklist（traps #5 的回归变体）

**现象**：GUI 重启、pids.json 里恢复仍在运行的服务（此脚本没有活动 LogWorker）后，
界面每 2 秒规律性卡顿一下——与 v1.4.1 修过的问题一模一样。

**根因**：`ui/app.py` `_sync_control_panel()` 在选中脚本没有活动 LogWorker 时回退调用
`process_service.is_running(name)` → `_pid_alive()` → **同步 tasklist（约 0.6s）跑在 GUI 线程**。
讽刺的是 StatusPoller 的信号参数里就带着 `alive_set`，`_apply_script_statuses` 却只用了它刷列表、
没存下来给面板用。

**解决方案**：`_apply_script_statuses` 把每轮 `runtime + alive_set` 缓存到
`self._last_runtime / self._last_alive`；`_sync_control_panel` 只查缓存 + LogWorker 列表，
彻底不再同步调 tasklist。停止/结束时同步 pop 缓存条目，状态立即反映（不等下一轮轮询）。
（v1.6.0 修复）

**教训**："GUI 线程不做阻塞操作"要守住**所有**调用路径，不只最初出问题的那一条；
后台轮询结果一旦拿到就应缓存供任意后续读取，而不是让旁路再查一遍。

***

## #12 下载暂停后更换目录再恢复，产出缺头的损坏 GGUF

**现象**：下载 A 模型到目录 D1 → 暂停 → 设置里把下载目录改成 D2 → 恢复下载
"成功完成"，但加载 GGUF 报格式错误（文件头缺失/大小不符），且很难联想到是下载问题。

**根因**：`service/download_service.py` `start_download()` 恢复 paused 任务时直接沿用
`entry.downloaded`（D1 里旧文件的字节数），但 `dest_path` 已指向 D2。D2 里没有本地文件
或大小不同 → `download_file` 以 `Range: bytes=N-` + 追加模式（"ab"）向 D2 的
**空文件追加后半段** → 得到只有尾部的损坏文件。

**解决方案**：恢复时续传基线以**本地实际文件大小**为准重新计算
（新目录无文件 → 从 0 开始；已有完整文件 → 直接标记完成），不再信任 entry.downloaded。
（v1.6.0 修复）

**教训**：持久化的"进度值"只有和"当前环境实际状态"重新对账后才能用——
凡是外部条件（路径/目录）可变，恢复逻辑都要重验本地事实，续传尤其如此。

***

## #13 llama.cpp release 资产命名随上游演进，变体识别不能写死枚举

**现象**：版本管理功能初版按 2026-08 的资产清单写死变体枚举（cuda-12.4 / cuda-13.1 /
hip-radeon）。真实验证最新 release（b10793，2026-09-03）时发现：CUDA 13 系列已从
`cuda-13.1` 变成 `cuda-13.3`，AMD 包从 `hip-radeon` 改为 `rocm-10.0`，还新增了
`openvino-2026.3.1`——死认 `cuda-13.1` 的推荐逻辑在最新版上直接失效（驱动 ≥580 的
机器被回退推荐 cuda-12.4）。

**根因**：llama.cpp 的 release 资产命名不是稳定契约，变体后缀（CUDA 版本号、后端名）
随上游构建矩阵演进；用精确字符串匹配/枚举做分类与推荐，版本一变就静默失效。

**解决方案**：`service/llamacpp_update_service.py` 的 `recommend_variant()` 改为按
`cuda-` 前缀系列处理：`sorted(..., reverse=True)` 降序尝试所有 CUDA 变体，按主版本号
（13+ 需驱动 ≥580，12.x 需 ≥528）校验驱动门槛，不满足逐级降级；展示名由
`variant_label()` 按前缀规则生成（`cuda-*` / `openvino-*` / `rocm-*`），未知新变体
自动回退显示原始 key。cudart 配套包按 `cudart-{variant}` 动态匹配，同样不写死。
（v1.7.0，测试覆盖 cuda-13.3 / 旧驱动降级 / 无 CUDA 资产回退）

另记：公共镜像稳定性参差——实测 ghfast.top SSL 握手超时不可用，gh-proxy.com 可用；
GitHub 直连时通时超时（10060）。UI 镜像前缀 placeholder 建议填实测可用的
`https://gh-proxy.com/`，下载失败先换镜像再怀疑代码。

**当前状态**：#13 已规避（v1.7.0 按前缀系列匹配）。

***

## #14 自动下载状态标志从未置位，静默链路静默失效（无任何报错）

**现象**：版本管理页后台静默下载功能完全不触发——本地检测和 release 列表都正常
返回，`_maybe_auto_download()` 却总是提前返回；冒烟测试断言
`auto start wrong: []` / `two-channel plan wrong: []`，且无任何异常或日志。

**根因**：`ui/update_tab.py` `_on_local_info()` 声明了前置条件标志
`self._local_info_done`（`__init__` 初始化为 False），但回调里**从未置 True**；
`_maybe_auto_download()` 第 304 行 `if not self._local_info_done: return`
永久短路。配套问题两处：① `_refresh_installed()` 未回填
`self._installed_dirs`（installed\_keys 过滤恒空集 → 已装版本会被重复规划，
`_copy_sources()` 也只剩当前目录）；② `_install_silent` 只在 `_start_install()`
内部赋值，外部替换该方法（测试 mock / 未来重构）时标志丢失，完成回调走错
弹窗分支。典型的"状态机标志声明了但忘置位"，且失败模式是静默跳过——
没有异常、没有日志，只有端到端断言能抓到。

**解决方案**：`ui/update_tab.py` 三处修复（v1.7.1）——
① `_on_local_info()` 回调开头置 `self._local_info_done = True`；
② `_refresh_installed()` 收集 `self._installed_dirs = [item["dir"] for item in items]`；
③ `_start_next_auto()` 调 `_start_install(..., silent=True)` **之前**先置
`self._install_silent = True`（`_start_install` 内部同名赋值保留，幂等）。
**教训**：跨回调的就绪标志（done/handled 类）必须在其数据源回调里显式置位，
且这类"条件永远不满足"的 bug 只能靠链路级冒烟测试暴露——单测纯函数全绿
不代表 UI 编排层正确。

***

## #15 llama.cpp 新版移除 `--mmap`（改用 `--load-mode`），旧脚本秒退

**现象**：新建 MiniCPM5 脚本启动即失败，日志：
`error: invalid argument: --mmap` / `进程已结束`（退出码 1，发生在模型加载前）。

**根因**：llama.cpp 在较新版本（本机 b10883）把 `--mmap / --no-mmap` 移除，
mmap 改为默认行为，由 `--load-mode auto|mmap|mlock|mmap+mlock` 控制。
旧 build（如 b10819）仍接受 `--mmap`（标记 DEPRECATED），所以切版本后
存量含 `--mmap` 的脚本全部失效。参数解析失败发生在加载模型之前，表现为"秒退"。

**解决方案**：b10883 环境下去掉所有脚本中的 `--mmap`（新默认 load-mode=auto 即
mmap 优先，行为等价）。一键批量处理：`re.sub(r"--mmap\s*\^?\r?\n", "", content)`，
同步写回 `.bat` 与 `scripts.json`；共清理 7 个存量脚本（Qwen3.8-27B、
gemma-4-12b-heretic-abliterated、GLM-4.7-Flash、gemma-4-26B-A4B、
qwen3.8-27B-IQ3、Qwen3.8-27B-IQ2、MiniCPM5）。

**教训**：llama.cpp 命令行参数随上游演进不稳定（见 #13 同类教训）。切换安装版本
（v1.7.0 版本管理）后，旧脚本里的已弃用/已移除参数会导致静默秒退；
排查"进程秒退"应先核对当前 `llama-server --help` 是否还支持脚本里每个参数，
不能假设参数一直有效。脚本生成侧应避免写入非稳定参数。

***

## #16 路径分隔符混用：Qt 给 `/`、os.path 给 `\`，脚本与 GUI 显示不统一

**现象**：GUI 模型选择框显示 `C:/modelscope/...`（正斜线），而 `.bat` 里
`cd /d "C:\Users\..."`（反斜线）、`-m "C:/modelscope\models\..."`（混合）、
`--mmproj "C:/modelscope\models\...\mmproj-...gguf"`（混合）；scripts.json 的
model_path 字段也是混合写法。同一套路径三种写法并存。

**根因**：路径从未在"写入点"统一，四个来源混用——

1. `QFileDialog` 返回正斜线（Qt 原生），经 Settings 原样存入 app_config.json；
2. Python `os.path.join / dirname / normpath` 在 Windows 返回反斜线
   （`find_mmproj` 拼出 `C:/modelscope\models\...` 混合；版本切换
   `replace_bat_dir` 的 new_dir 是反斜线）；
3. 历史一次性生成脚本用 os.path.join 拼接正斜线根路径 + 子目录 → 混合；
4. 手工编辑的 .bat 反斜线。

Windows 文件 API 对 `/` 和 `\` 都接受，llama-server 也能加载混合路径，
所以功能一直正常——但观感混乱、字符串比较脆弱（埋雷）。

**解决方案**：确立**正斜线 `/` 为唯一规范形式**（跨平台、JSON 免转义、Qt 原生、
cmd/llama-server 兼容），在"写入点"统一收口（v1.9.0）：

- 新增 `utils/path_utils.py` 的 `normalize_path()`（`\` → `/`，幂等）；
- `config/config.py` 所有路径 setter（llamacpp_path / model_path / model_dir /
  visual_model_path / download_path / llamacpp_install_root）统一规范化；
- `script_builder.build_bat_content` 对 exe_dir / model_path / visual_model_path
  入口统一；`find_mmproj` 返回前规范化；
- `llamacpp_update_service.replace_bat_dir` 的 new_dir 统一正斜线
  （原本 normpath 产反斜线）；
- 一次性迁移脚本批量把存量 17 个 .bat + scripts.json + app_config.json 里
  所有路径分隔符改为 `/`。

**教训**：Windows 路径处理必须有一个全局统一的分隔符规范，在 Qt 返回值、
os.path 产物、脚本/配置写入点三处收口；不要依赖"Windows 两种都兼容"
来放任混用。凡是新增写入路径的位置（配置 setter、脚本生成、日志）都要过
normalize_path()。

***

## #17 脚本名含空格时 name ≠ bat 文件名（sanitize 转下划线），修错文件而不自知

**现象**：用户新建 `Mini CPM5-2B` 脚本（名字带空格）后启动报
`invalid argument: --mmap`。修复时修改了 `data/scripts/Mini CPM5-2B.bat`
（空格版），验证脚本也按 `{name}.bat` 读取——全部"通过"，但 GUI 里启动
MiniCPM5 **依然报错**。

**根因**：`ScriptEntry.sanitize_filename()` 把空格转下划线
（`Mini CPM5-2B` → `Mini_CPM5-2B`），`ScriptService.get_script_path` /
`save_to_file` 实际读写的文件是 **`Mini_CPM5-2B.bat`（下划线版）**。
空格版 .bat 是孤儿（GUI 列表会把它作为孤儿再显示一条）。于是：
- 修复改了空格版孤儿 → 真正被加载的下划线版仍是旧内容（带 `--mmap`）；
- 验证脚本按 name 拼路径 → 检查的是孤儿文件 → 误报"一致"。

**解决方案**（v1.9.0 一并修复）：
- 同步下划线版 bat 内容为 scripts.json 的正确 content，删除空格版孤儿；
- 涉及脚本文件的读写与验证**必须经 `ScriptEntry.sanitize_filename(name)`**
  得到真实文件名，不能直接 `{name}.bat`。

**教训**：scripts.json 的 name 是显示名，bat 文件名是 sanitize 后的安全名，
二者在名字含空格/特殊字符时不一致。任何"按脚本名定位文件"的逻辑（读写、
删除、一致性验证）都必须先过 sanitize_filename；否则会出现"改了但没生效"
的静默错位，且验证还会帮你确认它"没问题"。

***

## #18 GitHub release 资产逐步上传：最新版本"只有 cudart"时自动下载静默失效

**现象**：版本管理页显示有新版本（如 b10933），后台自动下载却从不触发；
点开该版本的"构建变体"下拉是空的，无法下载，而下一个版本（b10932）正常。

**根因**：llama.cpp 发布 release 时资产是**逐步上传**的（先建 release，再
逐个传 zip）。缓存/API 在资产传完前抓取，会得到"只有 cudart 运行库、
没有主包"的不完整快照。`plan_auto_download` 里
`variants = [v for v in assets if not v.startswith("cudart-")]` 恒为空
→ 静默 `return []`；且 `fetch_releases` 的 1h 缓存 TTL 内直接返回旧快照，
GitHub 直连超时又回退旧缓存，导致长期"有新版本却不下载"。

**解决方案**（v1.10.1）：
1. `_release_from_api`：无主包资产（只有 cudart）的 release 直接丢弃；
2. `fetch_releases`：TTL 内命中缓存时检查最新 release 是否有主资产，
   不完整 → 视为过期强制重抓（自愈）；
3. `plan_auto_download` 增强：支持传整个 releases 列表，自动跳过不完整
   快照，取**第一个可下载的最新推荐版本**（单 dict 传参保持兼容）。

**教训**：外部 API 的"最新条目"可能是发布中不完整状态（GitHub release
资产、云存储上传、CDN 回源同理）。凡是基于"最新"做决策（自动下载、
默认选择），都要校验数据完整性并向后回退，不能信任"最新 = 最全"；
缓存命中时也要校验内容而非只按 TTL。

***

## #19 ModelScope 文件大小元数据失真，下载进度百分比错乱

**现象**：下载进度条百分比离谱（下载到一半就显示 100%，或显示 677% 这类
超范围值）。实测 MiniCPM5-2B-F16.gguf 元数据 `file_size` 744MB、实际文件
5GB（相差约 7 倍）；Qwen3.8-27B-UD-IQ4_XS 元数据 1.4GB、实际 14GB。

**根因**：`DownloadEntry.progress` 按 `downloaded / file_size` 计算百分比，
而 `file_size` 来自 ModelScope 文件列表 API 的元数据（部分文件与实际大小
不符）。下载中 `_on_progress` 只在 HTTP `total > 0`（有 Content-Length）时
覆盖 `file_size`；无 Content-Length 时停留在错误元数据上 → 百分比超 100
（QProgressBar 内部文本直接显示超范围值）。

**解决方案**（v1.10.1）：
1. `_on_progress`：`total > 0` 时直接用 HTTP 完整大小覆盖 `file_size`
   （HTTP 值比元数据可靠）；
2. `_on_finished`：完成后以实际文件大小校准 `file_size`（`max`），保证
   收敛到 100%；
3. `DownloadEntry.progress`：百分比 clamp 到 0~100（兜底任何失真输入）。

**教训**：外部元数据（文件大小、版本号等）不能作为进度/比较的可靠基数，
必须以实际传输内容为准并在边界做 clamp；进度类 UI 永远要防御
"分子 > 分母"的越界输入。

***

## #20 定时任务线程与 HTTP handler 并发读写同一对话 JSON，更新互相覆盖

**现象**：定时任务执行结果或聊天消息偶发丢失（后写者覆盖先写者）；触发条件
是任务调度线程与聊天请求线程几乎同时写同一 conversation 文件。

**根因**：`chat/scheduler.py` 的读-改-写（list_conversations → 改 messages/tasks
→ write_conv_file）与 `chat/handlers.py` 的同类操作无锁并发。两个线程都基于
旧快照修改后原子替换文件，后写者把先写者的修改整个覆盖掉。

**解决方案**（v1.11.0 审查修复）：
- `chat/repository.py` 新增模块级 `CONV_LOCK = threading.RLock()`（可重入，
  scheduler 持锁时内部 write_conv_file 再取锁不死锁），`write_conv_file`
  整体包锁（写串行化）；
- `chat/scheduler.py` 成功路径拆两段锁：网络调用（最长 120s）前锁内读
  conv/task/agent 构造请求，调用后**重新读最新 conv**再写回结果（避免基于
  旧对象覆盖并发更新）；`_record_task_error` 同样包锁。
- 已知残余限制：handler 的"读在锁外"仍存在极小覆盖窗口（写已串行化），
  极端并发下可能丢一次更新，已记录为低概率已知项。

**教训**：多线程 + 文件存储的"读-改-写"必须整体串行化（锁住整个事务），
只在写函数里加锁不够；网络等长耗时操作绝不能放在锁内。且写回前要重读
最新状态，不能信任调用前读到的快照。

***

## #21 表单回填 set_preset 只填值不勾开关，加载已存脚本"似读了但没用上"
**现象**：v1.15.0 脚本绑定模型后，选中已有绑定脚本的模型时表单看起来有值，
但某参数开关仍是灰的，保存/运行后参数丢失（等于没加载成功）。

**根因**：`ui/script_form_widget.py` 的 `set_preset` 原先只对 config 里出现
的 key 填值，但**不动 QCheckBox 勾选态**；而 `get_config` 只收集"被勾选"的
参数。于是"值填进去了、开关没勾"→ 保存时该参数被跳过，表单事实源与磁盘
脚本不一致（旧版靠"表单为事实源"兜底掩盖了，绑定模型后加载成为常态才暴露）。

**解决方案**（v1.15.0）：
- 改 `set_preset`：凡 config 中出现的 key 且值非空（非 None/"" /"off"），
  同时置起对应 `checkboxes[key].setChecked(True)`。这样 parse 回填 与
  auto_generate_config 自动生成 都能如实反映"该参数已启用"。

**教训**：表格/表单组件的"回填"必须同步值和使能状态，二者缺一不可。凡是
收集时过滤的维度（这里是勾选态），回填时就一定要还原它。

***

## #22 托盘"重启"误报"已再运行"：单实例锁重试窗口太短
**现象**：托盘/版本管理页点"重启"，新实例弹"LlamaCPP GUI 已在运行"，重启失败。

**根因**：`_restart_app` 用 `subprocess.Popen` 立即拉起新实例后才 `close()` 旧实例。
新实例 `main()` 抢 `app.lock` 时旧实例尚未退出（停 StatusPoller wait300 +
LogWorker wait300 + bridge shutdown + monitor stop，累计耗时波动），而锁重试窗口仅
10×0.15s=1.5s，不够旧实例释放锁，误判单实例占用。

**解决方案**（v1.15.0，最终）：
- `_restart_app` 给子进程设环境变量 `LLAMACPP_RESTARTING=1` 再 Popen；
- `main()` **对重启实例直接跳过单实例锁**（`if os.environ.get("LLAMACPP_RESTARTING") != "1":`）——
  重启是用户显式意图，不应被锁拦；普通首次启动仍受单实例保护，重复启动走短暂重试后提示。
  （初版方案"重启用 40×0.25s 长重试等旧实例释放锁"在残留/僵尸实例占锁时仍会误拦，
  故改为彻底跳过。）

**教训**："重启=启动新的+退出旧的"流程中，新实例先于旧实例启动，天然存在竞争窗口；
把锁重试拉长只是缓解，最可靠的是**重启路径不走单实例锁**（区分重启与普通启动）。

**二次优化**（同 v1.15.0）——重启"能成但慢"：旧实例 `closeEvent` 里 `bridge.shutdown()`
阻塞 ~0.5s（HTTPServer serve_forever poll 周期）+ `monitor.stop()` join(2s)，
把退出拖慢，新实例因此多等。修改：
- 桥 shutdown 改到 `threading.Thread(daemon)` 触发，不阻塞 GUI（daemon serve_forever
  进程退出时由 OS 回收并释放端口，新实例在旧进程退完后重新绑定，无重叠）；
- `monitor_service.MonitorService.stop()` 的 metrics join 由 2s 降为 0.2s
  （metrics 线程是 daemon，进程退出即回收）。
- 注意：StatusPoller/LogWorker 的 QThread `wait` 是防"运行中被销毁崩溃"的，
  不能为提速削太狠。

***

## #23 HTTP 服务 shutdown() 会阻塞等 poll 周期（~0.5s）
**现象**：退出/重启 GUI 时点"关闭"要卡约半秒才消失。
**根因**：`http.server.HTTPServer.shutdown()` 会阻塞直到 `serve_forever` 的 poll 周期
结束（默认约 0.5s）；桥服务（`chat/server.py` 用 `serve_forever` daemon 线程）在
`MainWindow.closeEvent` 里被同步调用，卡住 GUI。
**解决方案**：把 `_bridge_server.shutdown()` 放到后台 daemon 线程里触发，不阻塞；
进程退出时 daemon serve_forever 被回收、OS 关闭 socket 释放端口，新实例在旧进程
退出后再绑定，无 SO_REUSEADDR 重叠问题。
**教训**：凡是"阻塞等待某个 poll/轮询周期"的优雅关闭，都不该占住 UI 线程；
daemon 线程 + 进程退出时由 OS 回收资源的场景，可安全地转为后台触发。

***

## #24 切换模型后表单残留上一模型的参数，污染新模型脚本

**现象**：从已绑定脚本的模型 A（勾了 MTP/KV 量化等参数）切到无绑定脚本的模型 B
后，B 的表单里仍勾着 A 的参数；直接保存则 B 的脚本带上 A 的配置（小模型被塞
大 ctx、量化错配等），运行行为异常但无任何报错。
**根因**：`ui/script_form_widget.py` 的 `set_preset(config)` 只回填 config 里出现的
字段，未覆盖的字段保持上一个模型的现场；`auto_generate_config` 只产出基础参数，
高级参数不上报 → 残留并随保存固化进 .bat。
**解决方案**：`set_preset` 先调 `_reset_to_defaults()`（按 script_builder.CATEGORIES
出厂默认整体重置勾选与值）再回填 config。
**教训**："增量回填"型 setter 必须先整体复位，否则上一次调用的现场就是本次的
隐性输入；表单是事实源的架构里，这类污染直接写进持久化脚本。

***

## #25 chat handler 层组合式读-改-写不加锁，并发更新互相覆盖（traps #20 延伸）

**现象**：定时任务写对话的同时用户在 Web UI 改标题/增删任务，偶发"刚保存的任务
消失"或"标题被改回旧值"。
**根因**：`chat/handlers.py` 4 处端点（POST tasks、PUT tasks/{tid}、PUT
conversations/{cid}、DELETE tasks/{tid}）各自"读 JSON → 改 → 写回"。traps #20 只
把 repository 单次写串行化了，handler 层跨函数的组合 RMW 仍可交错，后写覆盖先写。
**解决方案**：整个读-改-写段包 `chat/repository.CONV_LOCK`（RLock），响应构造放
锁外；锁内以重读的最新数据为准。
**教训**：锁的粒度必须覆盖完整业务事务（读+算+写），只锁"写"锁不住"读到的
已是旧值"的竞态。

***

## #26 chat.js 并发回复错位 + 轮询覆盖发送中的消息

**现象**：@ 两个角色的消息发出后偶发只回一条、或回复内容写进另一个角色的气泡；
流式生成中切对话再切回，正在生成的回复被消息列表整体刷掉。
**根因**：非流式分支 push 后用 `state.messages[state.messages.length - 1]` 定位，
两个并发请求都 push 后各自取到的"-1"是同一条；`pollConversation` 无条件用轮询
结果整体替换 `state.messages`，覆盖发送中的本地状态。
**解决方案**：push 前记 `const msgIdx = state.messages.length`，按下标定位更新；
`pollConversation` 开头加 `if (state.isSending) return` 守卫。
**教训**：任何"先 append 再定位更新"的前端代码，并发下禁止用 length-1 定位，
必须在 append 前记下标；后台轮询回写列表前必须避开本地进行中的修改窗口。

***

## #27 "清理旧版本"会规划删除运行中服务所在的版本目录

**现象**：某版本 llama-server 正在运行时点"清理旧版本"，确认后该版本的 exe
目录被删；进程虽未死但文件已消失，下次重启服务直接失败。
**根因**：`llamacpp_update_service.plan_version_cleanup` 只排除"当前配置使用中的
exe"，不知道 pids.json 里其他脚本还运行着别的版本目录。
**解决方案**：UI 侧收集运行中目录（pids.json runtime + 脚本 `cd /d "..."` 正则提取）
传入 `plan_version_cleanup(running_dirs=...)`，运行中目录恒保留。
**教训**：磁盘清理类规划必须以"进程真相"（pids.json + .bat 里的工作目录）为输入，
"当前配置路径"只是其中一个消费者。

***

## #28 循环内 lambda 捕获循环变量 / 行号快照，点击时已漂移

**现象**：搜索结果里点某一行的"追踪"按钮，刷新的是最后一行的按钮状态；下载
队列先删一行再点另一行的"移除"，删除的是错误的行（或 KeyError）。
**根因**：Python 闭包晚绑定——`lambda: self._watch_model(mid, watch_btn)` 里
`mid/watch_btn` 是循环变量的引用，点击时循环早已跑完；队列移除按钮捕获创建时的
行号 `row`，期间有行删除后行号整体上移。
**解决方案**：默认参数绑定捕获当前值（`lambda _, e=entry: ...`、
`lambda checked, x=mid, b=watch_btn: ...`）；行号不快照，点击时按 UserRole 存的
`(source, file_path)` 现查 `indexOf` 再 removeRow。
**教训**：Qt 表格/列表里给动态行装回调，一律"默认参数绑定值 + 键现查行号"，
绝不信创建时刻的行号或循环变量。

***

## #29 定时任务引用的对话/任务/角色被删后，调度线程每秒空转扫描

**现象**：删除对话或其中的任务后，日志每秒刷"对话不存在"/"任务不存在"；
删除角色后任务永不执行也不报错，且 next_run_time 不推进导致每轮重试。
**根因**：`chat/scheduler.py` 的 not-found 分支只打日志并 return，不重建索引
（task_index 里仍是已删条目）→ 每轮调度都 miss、每轮全量扫描；agent 缺失分支
同样空转。
**解决方案**：conv/task not-found → `rebuild_task_index()` 后 return（索引自愈）；
agent not-found → `_record_task_error()` 记录错误并推进 next_run_time（防空转）。
**教训**：调度器对"引用的实体已消失"必须有自愈路径（重建索引/推进时间/禁用），
只 log 不动状态 = 死循环刷屏。

***

## #30 parse_bat_params 解析不了带引号的含空格值，短 flag 还会误匹配

**现象**：`--host` 等值含空格时（.bat 里以双引号包裹）表单回填为空/截断；
反向解析出的参数与 .bat 实际值不一致，保存后静默改写脚本。
**根因**：值匹配正则 `\S+` 不吞引号内的空格；短 flag（如 `-m`）无词边界，
会命中 `-mmproj` 等更长 flag 的前缀。
**解决方案**：`service/script_builder.parse_bat_params` 值正则改为
`("[^"]*"|\S+)` 并给 flag 加 `(?<![\w-])` 前置词边界。
**教训**：反向解析生成物（.bat → 表单）必须与生成器（表单 → .bat）的引号规则
对称，否则 roundtrip 会静默丢数据。

***

## #31 GUI 线程阻塞五连：tailscale 探测 / 全量清理 / 本地模型扫描 / 图片迁移 / 清理规划

**现象**：切换模型或服务就绪偶发界面冻结数秒（tailscale 缓存过期）；点"清理全部
llama 进程"到日志出现之间界面无响应（多实例时更久）；切到"模型更新追踪"标签
卡顿（模型库大时秒级）；启动 GUI 后头几秒操作发滞；旧版本多时"清理旧版本"
统计阶段卡 GUI。
**根因**（traps #5/#11 的变体，这次藏在更冷门的路径里）：
`_update_external_url` → `get_tailscale_ipv4()` 缓存 60s 过期后在 GUI 线程同步跑
tailscale 子进程（timeout 3s × 2 候选）；`_cleanup_all_processes` 在 GUI 线程循环
tasklist+taskkill；`merge_local_models` 全盘 os.walk 在 GUI 线程（且
model_watch_tab 曾残留旧同步 `refresh()` 覆盖了后台版）；`migrate_conversation_images`
启动时同步扫全部对话；版本清理的规划/大小统计在 GUI 线程。
**解决方案**：全部挪后台 QThread——`TailscaleProbeWorker`（GUI 侧 60s 缓存只读、
探测异步回传）、`KillAllLlamaWorker`、`WatchMergeWorker`、`CleanupPlanWorker`/
`CleanupExecWorker`（两阶段拆分便于插确认框）、图片迁移改 daemon Thread；
另删掉了 model_watch_tab 里覆盖新实现的旧 `refresh()` 重复定义（见 #32）。
**教训**："GUI 线程禁阻塞"要审所有调用链：缓存过期后的重算、确认框前后的
统计/执行、tab 切换触发的全量扫描、启动时的数据迁移——都是阻塞回潮的高发点。

***

## #32 类体里重复定义同名方法，后者静默覆盖前者

**现象**：把 `model_watch_tab.refresh()` 改造成后台 merge 后，切 tab 依旧卡顿，
后台化"没生效"；代码里两处 `def refresh` 相距 40 行，肉眼 review 极易漏。
**根因**：改造时新增了新版 `refresh()`（调 `_request_merge`），旧的同步
`refresh()`（直接 `merge_local_models`）残留在 `_build_ui` 之后——Python 类体
顺序执行，后定义覆盖前定义，旧实现静默生效。
**解决方案**：删除重复定义；同名方法改造后全文 grep `def <方法名>` 确认唯一。
**教训**：在大方法块中间插入新版实现时，必须确认旧版被完全移除；
`grep -n "def name"` 是收尾必做动作。

***

## #33 QComboBox.setEditable 后手填值不改变 currentIndex/currentData

**现象**：ctx-size 改为"挡位下拉 + 可手填"后，用户手填 50000 保存，.bat 里
写出的却是之前选中的挡位值（如 32768）——所见非所得。

**根因**：可编辑 QComboBox 的 `setEditText()`/用户键入只改编辑框文本，
**不更新 currentIndex**：currentData 仍停留在上一个候选项上。表单读取逻辑
`get_config` 对所有 QComboBox 统一用 `currentData()`，手填值被静默丢弃。

**解决方案**：`ui/script_form_widget.py` 的 `get_config` 对 `isEditable()`
的下拉改读 `currentText()`（挡位显示文本 "128K (131072)" 用正则取括号内
数字，手填纯数字原样返回）；回填 `set_preset` 对不在挡位里的值走
`setEditText()` 而非只 findData。另配 `NoInsert` 防手填值混进候选项。

**教训**：可编辑下拉的"值"有两套（item data vs 编辑框文本），读哪套要看
交互形态：允许手填就必须以 currentText 为准；挡位文案与存储值不一致时
（"128K" vs "131072"）要在读写两侧做同一套转换。

***

## #35 聊天页"手动保存过地址"永久压死自动检测——用户忘了自己保存过

**现象**：切换不同端口/host 的模型后打开聊天窗口，API 地址永远显示
`http://127.0.0.1:8086` 这类旧值（用户某次手动填写并保存过），无论当前
服务用 Tailscale 地址还是 127.0.0.1、端口多少，"自动获取"完全不生效。
后端 provider 修复（#34）正确也无效——问题在前端。

**根因**：`chat.js` init 的优先级设计："手动保存过（llamaUrlManual=true）
→ 永远用手动值"。这个"尊重用户配置"的设计有两个致命伤：
1. 用户根本不记得保存过，localStorage 里的旧值成了幽灵配置；
2. 没有任何 UI 出口把 manual 状态清掉（只能 F12 清 localStorage）。
手动保存的旧值于是成了永久死锁，把后端所有自动检测逻辑架空。

**解决方案**：优先级反转为**自动检测优先**——`init` 改为
`defaultLlmUrl || saved.llamaUrl`（有运行中服务永远跟随当前服务，手动值
只在无服务时兜底）；设置弹窗 API 地址旁新增"自动检测"按钮（拉
`bridge/info` → 填充 + 测连接，即时刷新）；`llamaUrlManual` 标记降级为
纯历史记录（不再参与取值）。

**教训**："尊重用户手动配置"类设计必须同时回答两个问题：用户怎么**知道**
当前处于手动模式？怎么**退出**手动模式？没有可见状态和出口的手动优先
= 幽灵配置死锁。缓存类设计同理：自动值应始终优先于上次的手动快照，
手动值只做无自动值时的回退。

***

## #36 搜索页手动追踪的模型在追踪页消失——后台 worker 持内存旧快照整体覆盖写盘

**现象**：在模型搜索页对未下载的模型点"追踪"（提示成功），切到"模型更新追踪"
标签页却看不到它；检查 data/model_watchlist.json 里只剩 added_by=local 的条目，
手动追踪的条目彻底消失（数据丢失，不只是不显示）。

**根因**：关注列表有多个写入入口（搜索页 add_manual_model / 追踪页 merge_local_models
与 check_updates / 移除），而后台 worker 拿的都是 UI 传入的**内存旧快照**：
1. `ModelWatchTab._request_merge` 把 `self._watchlist`（不含刚追踪的条目）传给
   WatchMergeWorker；
2. worker 扫描完调 `merge_local_models(dir, 旧快照)` → `_save_watchlist(旧快照
   +本地新增)` **整体覆盖写盘**——磁盘上 add_manual_model 刚写入的 manual 条目
   被冲掉；
3. WatchCheckWorker 同样吃旧快照写盘（检查耗时 15s+，窗口更大）。

**解决方案**（双层防御，service 层收口）：
1. `_save_watchlist` 改**合并语义**：写盘前重读磁盘，磁盘有而内存没有的条目
   保留（按 model_id 去重，内存优先），并统一剔除 ignored 条目（防"移除"被
   旧快照复活）；所有写路径返回合并后的全量列表；
2. worker 一律磁盘重读（`merge_local_models(dir)`/`check_updates(load_watchlist())`），
   不再信任 UI 快照；
3. `remove_model` 顺序调整：先写 ignored 再删条目写盘（否则合并语义会把刚删
   的从磁盘捞回）。
补 3 例回归测试（stale 快照保 manual / 检查期间写入不丢 / 移除不被旧快照复活）。

**教训**：多入口读改写同一 JSON 文件时，任何"用读时快照整体覆盖写盘"的后台
耗时操作（扫描/网络检查）都是数据丢失源——写盘必须合并（或加锁 + 重读），
快照只用于展示层。UI 向 worker 传"当前状态"前先问：执行期间别的入口会不会
已经改了磁盘？

***

## #34 聊天页取不到正在运行模型的地址——provider 两层来源都只查"当前选中"

**现象**：运行 A 模型后切到 B 模型调参，点"聊天窗口"打开聊天页，模型 API 地址
没有自动填充（须手动填）。选中 A 时正常，切走即失效。

**根因**：`ui/app.py` `_current_llm_url`（注入桥服务、由 `/bridge/info` 调用）
两层来源都以"当前选中脚本"为唯一候选：
1. 就绪 URL 层：`if current_script_name: candidates.append(_server_urls.get(当前名))`
   ——取到 `None` 也**不回退**到其他就绪 URL（回退分支挂在 `elif` 上，
   选中名非空永远走不到）；
2. pids.json 层：先把 runtime 筛成只剩选中名的记录——选中模型不在运行时
   dict 变空，循环体不执行，整体返回 `""`。
脚本绑定模型（v1.15.0）后"切模型调参"是高频操作，选中名 ≠ 运行中模型成为常态。
另有一处伴生隐患：host 解析里同步调 `get_tailscale_ipv4()`，而 provider 跑在
**桥服务 HTTP 线程**——探测子进程最坏阻塞 ~6s，且绕过 GUI 侧的探测缓存
（v1.16.0 R12 后台化改造的漏改点）。

**解决方案**：候选逻辑抽成模块级纯函数 `_pick_llm_url(current_name,
server_urls, runtime, override, ts_cache)`：两层统一"选中名优先 → 回退任意
运行中记录"；host 解析 `_resolve_llm_host` 只读手动指定/探测缓存（普通属性，
GIL 下读安全），绝不跑子进程/建 QThread（跨线程不安全）。补 7 例回归测试。

**教训**：多实体（多脚本/多模型）场景下，"按当前选中项过滤"的代码要始终
回答"选中项不存在时回退到谁"；跨线程注入的回调（HTTP handler → GUI provider）
内部只能做无副作用的纯读，任何"探测/刷新"类动作都必须留在 GUI 线程。

***

## #37 追踪模型"查看文件"下载来源必须独立于搜索来源——共用 self._current_source 会下错 URL

**现象**（v1.17.0 合并重构时识别，实施前即规避）：用户把来源切换到
HF 镜像搜索，然后到"更新追踪"列表点某模型的"查看文件"进入文件列表，
勾选/单点下载——若下载链路仍读 `self._current_source`（HF 镜像），
会用 hf-mirror 的 URL 拼接 ModelScope 的 model_id/文件路径，下载必然失败。

**根因**：追踪列表的模型来源固定是 ModelScope（`add_manual_model` /
`merge_local_models` 都写 `source: modelscope`），但文件列表页的下载方法
`_download_selected` / `_start_single_download` 原先统一读 UI 的
`self._current_source`（跟随搜索来源下拉）。视图合并后同一个文件列表页
有两个进入路径（搜索结果 / 追踪视图），来源语义分裂：
- 搜索结果进入 → 来源应跟随 combo；
- 追踪视图进入 → 来源必须强制 ModelScope。
共用同一个变量必然有一个路径错。

**解决方案**：引入独立的 `self._filelist_source`——`_show_file_list` 增加
`source` 参数（默认取 `_current_source`，追踪入口显式传 `SOURCE_MODELSCOPE`），
文件列表渲染（`FileListWorker`）、`_download_selected`、`_start_single_download`
全部改读 `_filelist_source`。同时"返回"按钮不能硬编码回搜索结果页，用
`self._filelist_back_index` 记忆进入来源（0=追踪视图 / 1=搜索结果页）。

**教训**：当一个共享视图（文件列表页）被多个入口复用、且各入口的上下文
参数（来源、返回目标）不同时，UI 状态必须按"当前视图"建模（进视图时记录
上下文变量），不能按"全局选中项"建模——后者会让第二个入口悄悄用错参数。
合并功能时，逐一检查被合并视图的所有**入口**和**消费方**（不只是入口本身）。

***

## #38 scripts.json 同模型多绑定 + model_path 与 .bat 的 -m 脱节——跑 MiniCPM5 却显示 gemma-4

**现象**（v1.19.0 修复）：加载 `MiniCPM5-2B-F16.gguf` 运行，"运行控制"
与运行中模型清单显示的却是 `gemma-4-E4B`。核查磁盘发现：
- `data/scripts.json` 中同一 MiniCPM5 路径存在 **3 条**条目
  （`gemma-4-E4B`、`Mini CPM5-2B`、`MiniCPM5-2B-F16_1edbd53f`）；
- `data/scripts/gemma-4-E4B.bat` 第 4 行实际是
  `-m "C:/modelscope/MiniCPM5-2B-F16.gguf"`（脚本名与内容不符）；
- 另有 2 条条目 json 的 `model_path` 与 .bat 的 `-m` 完全对不上
  （如 `Qwen3.8-27B` 的 model_path 写的是 gemma 路径）。

**根因**：
1. `service/script_service.py` 的 `get_script_for_model` 原实现是
   "按归一化路径取**第一条**命中"，而 json 顺序里错位条目
   （第 12 条 `gemma-4-E4B`）先于规范名条目（第 18 条）
   → 返回错脚本名 → 运行、写 `pids.json`、运行中清单全部显示 gemma-4-E4B。
2. 写入端 `_upsert_config_entry` 只按 `name` 判重、不比较 `model_path`，
   同一模型下改名保存会**新增**条目而非合并（脏数据的直接来源：早期
   "脚本名可手改 + 手动绑模型"时代遗留）。
3. 早期版本在表单里改模型路径时，json 的 `model_path` 与 .bat 的 `-m`
   被分别改写，形成**两处事实源**，随后必然漂移。

**解决方案**（三端收口，`service/script_service.py`）：
- **读取端**：新增纯函数 `name_model_score(name, model_path)`（脚本名各 token
  在模型文件名中的命中比例）与 `binding_rank`（契合度 > 置顶 > 最新保存）；
  `get_script_for_model` 对全部同路径候选按 `binding_rank` **择优**返回
  （兜底仍走 `derive_name` 匹配）。
- **写入端**：`_upsert_config_entry` 判重条件加"归一化 `model_path` 相同"，
  改名时用新增的 `_remove_bat(old_name)` 清掉旧 .bat，防孤儿脚本。
- **启动迁移**：新增 `migrate_bindings()` 一次性清理——以 .bat 的 `-m` 为
  **事实源**校正 json 的 `model_path`；同 `model_path` 分组只留 `binding_rank`
  最高者；淘汰条目的 .bat 移动（不删除）到 `data/scripts_replaced`
  （`config/__init__.py` 的 `REPLACED_SCRIPTS_DIR`，可人工找回）；剔除 .bat
  已不存在的幽灵条目；全流程幂等。`ui/app.py` 在 `_init_tray` 之后调用并写日志
  留痕；`process_service.remap_runtime` 同步 `pids.json` 的键。
- 实测本机 18 → 15 条（校正 2、清理 3），二次运行全 0。

**教训**：同一个事实（"这个模型用哪条脚本"）必须只有一个权威来源，且
**写入端与读取端要用同一套等价判定**——读取端按 `model_path` 找、写入端按
`name` 去重，就是典型的"读一套写一套"错配。此外 .bat 是内容唯一来源，
json 里的派生字段（`model_path`）必须能从 .bat 重建，否则一定会漂移。

***

## #39 单元测试的 mkdtemp 不清理：反复跑测试把 C 盘写满（489GB）

**现象**（v1.19.2 顺手修复）：`python -m unittest discover -s tests` 突然失败——
`OSError: [Errno 28] No space left on device`（`tests/test_script_builder.py`
的 `_make_model` 写 8G 假模型时）。查盘：953GB 的 C 盘只剩 **8.1GB** 可用。

**根因**：
1. 测试大量使用 `tempfile.mkdtemp()` 造临时目录，**但从不删除**
   （unittest 不会自动回收，`mkdtemp` 是"只借不还"）。
2. `tests/test_script_builder.py` 的 `test_ctx_tier_by_size` 每次运行都**真写**
   1.5G + 8G 两个假模型（`_make_model` 用 `seek(size-1)` + 写 1 字节填零，
   NTFS 上不是稀疏文件，实占磁盘）——跑一次泄漏约 9.5GB。
3. 本机累计 **442 个 `tmp*` 目录、约 489GB**，把盘吃干；其余测试的小目录
   （几百字节的 .bat/json）是同类问题的小头。

**解决方案**：
- 每个 `tempfile.mkdtemp()` 之后立刻登记回收：
  `self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)`
  ——`setUp` 与测试方法内都能用，不需要 tearDown、不需要新建基类；
  已覆盖 `tests/test_script_builder.py`(4 处)、`test_script_model_binding.py`(4)、
  `test_script_pin.py`、`test_download_entry.py`、`test_llamacpp_update.py`(2)。
- 一次性清历史垃圾：删 `%TEMP%` 下"`tmp*` 且含 `.gguf`"的目录 442 个
  → 释放 **478.8GB**，盘从 8.1GB 恢复到 638GB 可用。

**教训**：会写大文件的测试必须自己回收临时目录，否则本地反复跑测试会线性
膨胀到写满盘（且失败表现为"测试代码报错"，极易误判为业务 bug）。
判据：跑完 `unittest` 后 `%TEMP%` 下"`tmp*` 含 .gguf"的目录数应为 0（本次已核验）。

***

## #40 进程秒退时 LogWorker 丢掉最后几行——报错信息恰好就在被丢的那批

**现象**：运行 `Ternary-Bonsai-2-27B-PQ2_0.gguf`，界面日志只有
"进程已启动 → initializing ... → verbosity = 3 → CORS 警告 → **进程已结束**"，
一句报错都没有；而手工在 cmd 里跑同一个 `.bat`，完整可见
`E gguf_init_from_reader: tensor 'output.weight' has invalid ggml type 142`
与 `E srv llama_server: exiting due to model loading error`（退出码 1）。

**根因**：`ui/workers/log_worker.py` 的 `run()` 主循环每轮只读**一行**
（`read_output`），读完立刻 `is_process_alive` 检查，进程已死就 `break`。
llama-server 加载失败会在 0.3~1 秒内退出并把错误行集中在最后，
管道缓冲区里尚未读取的行全部丢失；随后循环照样 emit "进程已结束"，
界面呈现为"正常启动中突然结束"的假日志，把真正的失败原因藏掉了。

**解决方案**：新增 `_drain_remaining()`——进程已退出后再把管道读到 EOF
（`read_output` 返回 None），上限 `LOG_DRAIN_MAX_LINES = 500` 行，然后才
emit "进程已结束"/`finished_signal`。**仅在进程已断开时读**：进程仍存活时
（如 `stop()` 收尾）不做阻塞读，避免卡住线程。回归测试
`tests/test_log_worker_drain.py`（桩进程服务：存活 N 次后死亡；
断言残余行不丢、顺序为 `…错误行 → 进程已结束`、进程存活时一次都不读）。

**教训**：读子进程输出的循环里，"检查存活"与"读完缓冲"是两个独立条件——
用存活状态提前 `break` 等于主动放弃管道里已到达的数据。凡是"退出前才打印"
的信息（错误行、退出原因）都在最后几行，收尾必须 drain 到 EOF 才能看见。

***

## #41 厂商私有量化的 GGUF：stock llama.cpp 拒载（type 142），需厂商 fork

**现象**：`Ternary-Bonsai-2-27B-PQ2_0.gguf`（ModelScope `prism-ml/Ternary-Bonsai-2-27B-gguf`）
在本机多个 llama.cpp 版本（b11139、b11149 = 当前最新）上均加载失败：

```
E gguf_init_from_reader: tensor 'output.weight' has invalid ggml type 142. should be in [0, 43)
E llama_model_load: error loading model: failed to load model
E srv llama_server: exiting due to model loading error      （退出码 1）
```

GGUF 元数据可佐证这是厂商自定义格式：`general.architecture = qwen35`、
`prism.hadamard.block_size/transform/axis/sign_mode`（旋转基底 Hadamard，
运行时需配套激活变换）。

**根因**：这是 PrismML 的三值（ternary）量化包（PQ2_0 / PTQ1_0），
张量类型号 **142/143 超出 mainline llama.cpp 的类型范围（0~42）**——mainline 不认识
该类型，必然拒载；模型卡明确写"stock llama.cpp will not run these files，
需用 PrismML-Eng/llama.cpp fork 的二进制"。

**实测类型号**（2026-09-24 用 HTTP Range 只抓 GGUF 头部、逐张量解析 851 条 tensor info）：

| 包 | 大小 | general.file_type | 张量类型直方图 | 官方 llama.cpp 结局 |
| --- | --- | --- | --- | --- |
| PQ2_0 | 6.71 GB | 142 | 402 个张量为 **142**、其余合法 | 拒载（`invalid ggml type 142`） |
| PTQ1_0 | 5.54 GB | 143 | 402 个张量为 **143**、96 个 30（BF16）、353 个 0（F32） | 拒载（143 同样 >42） |
| F16 | 50.11 GB | 1 | 全部合法（498 个 F16 + 353 个 F32） | **能加载但会静默胡说**：权重已折入 Hadamard 旋转（`general.basename=folded`、`prism.hadamard.*`），官方版不认这些元数据、不做激活端逆变换 |

所以"换官方 llama.cpp 的更新版本"或"改用全精度包"都不是出路——
**要跑这个模型必须用厂商 fork**。

**解决方案**（运行方式，非本仓库代码问题）：
- 用厂商 fork 的预编译包：`https://github.com/PrismML-Eng/llama.cpp/releases/latest`
  解压后，在"路径配置 → 选择 llama-server.exe"里指到该 fork 的 `llama-server.exe`
  （本软件的版本管理只拉 ggml-org/llama.cpp 官方 release，不覆盖 fork 版本；
  放在 `data/llamacpp/` 之外即可避免被"清理旧版本"清理）。
- fork 侧推荐参数（模型卡）：`-ngl 99 -fa on -c 32768`，采样
  `temp 1.0 / top-p 0.95 / top-k 20`（思考模式）；PQ2_0 在 Blackwell/Ada 上表现见模型卡表格。
- 本机（RTX 5070 Ti Laptop 12GB = Blackwell sm_120）：按模型卡的选包规则
  **PQ2_0 更优**（Blackwell/H100/A100 世代解码更快、预填充各平台都更快），已下载；
  ctx 建议按模型卡用 **32768** 起步——131072 的 q8_0 KV（约 4GB）叠 7.2GB 权重会顶爆 12GB 显存。
- 本机注意：`--mmproj` 配套文件为 `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf`（非必需，仅图像输入）。

**教训**：ModelScope/HF 上"某模型的 GGUF"不等于"任何 llama.cpp 都能跑"——
厂商自定义量化（新张量类型、私有元数据）必须用其配套 runtime。
判据：报错含 `invalid ggml type N` 且 N ≥ 43（超出 mainline 类型表）时，
先去看模型卡的 Quickstart/Requirements，别在换 llama.cpp 版本上白折腾；
**反过来也要警惕"能加载"**——类型合法但元数据含私有变换（如 `prism.hadamard.*`）时，
官方版会安静地跑出垃圾结果，比直接拒载更危险。

***

## #42 模型下拉重启后停在第一项——扫描路径是反斜线、配置存的是正斜线

**现象**：软件重启后"启动脚本"的模型下拉总停在**第一项**，而下方只读的模型路径
（以及右侧表单参数）仍是上次用的模型——下拉与路径显示打架，容易误判"马上要运行哪个模型"。

**根因**：`ui/app.py` 的 `_reload_model_combo()` 用
`self.model_combo.findData(self.settings.model_path)` 定位当前项，而 `findData`
是**精确字符串匹配**，两侧形式天然不同：
- 下拉项 userData 来自 `service/model_scanner.scan_gguf_files()` 的
  `os.path.join(root_dir, f)` → Windows 下是**反斜线**；又因为 `root_dir` 来自配置
  （已是正斜线），实际产物是 `C:/modelscope\MiniCPM5-2B-F16.gguf` 这种**混用**形式；
- `settings.model_path` 经 config 的 setter 收口，统一为**正斜线**
  （`C:/modelscope/…`，见 `utils/path_utils` 约定）。

匹配失败 → `idx = -1` → 跳过 `setCurrentIndex` → 下拉停留在索引 0（第一项）。

**解决方案**：`ui/app.py` 新增纯函数 `combo_index_for(paths, target)`
（两侧都过 `normalize_path` + `lower()` 比较；Windows 路径不区分大小写），
`_reload_model_combo` 改走它，下拉 userData 也统一存 `normalize_path(f)`。
另加兜底：当前模型不在模型目录里（被删/移走）时，在下拉顶部插入
"（不在模型目录）<文件名>"并选中——保证下拉与路径/表单**永不打架**，
而不是默默停在第一项。回归测试 `tests/test_script_model_binding.py::TestComboIndexFor` 4 例。
实测真实数据：旧实现 `index = -1`、新实现 `index = 2`（正确命中
`Qwen3.8-27B-UD-IQ2_XXS.gguf`）。

**教训**：**凡是"按路径匹配 UI 项"，两侧字符串必须先过同一个规范化函数**——
`findData` / `==` 这类精确比较在 Windows 上会因 `\` vs `/`、大小写差异**静默失败**，
而且失败表现是"停在第 0 项"而不是报错，极难被发现。
另注：路径混用形式（`C:/a\b`）本身也是隐患，扫描输出应尽早 `normalize_path`。
**v1.20.0 补充**：删本地模型文件后清空 `settings.model_path` 时，`saved` 为空也要
插入「（未选择模型）」占位项并选中——否则下拉又会退回显示第一项、与空路径打架
（同一坑的第二种触发路径）。

***

## #43 手删本地模型留下死绑定 + 运行中的 .gguf 被 Windows 锁

**现象**：用户想删掉不合适的本地模型，只能自己开资源管理器进模型目录手删。
手删有两个隐患：
1. `.gguf` 删了，但指向它的启动脚本还在（`data/scripts/*.bat` + `scripts.json` 条目）
   → 变成"脚本有、模型无"的死绑定，模型下拉/绑定查询里还留着这条幽灵；
2. 模型正在运行时 `.gguf` 被 llama-server 以 mmap 持有，Windows 拒绝删除
   （`PermissionError [WinError 32]`），报错信息与"删不掉"的原因不直观。

**根因**：此前 App 只提供"远端搜索/下载/更新追踪"，没有本地文件的查看与删除入口；
而 `ScriptService` 也只在 v1.19.2 之前有 `delete_script`（已删），没有"按模型移除绑定"
的入口——`migrate_bindings()` 只清"`.bat` 已不存在"的条目，不管"模型文件已不存在"。

**解决方案**（v1.20.0）：
- 新增"本地模型"视图（`ui/model_tab.py` 栈索引 3，搜索栏右侧入口）：列出模型目录下
  全部 `.gguf`（大小/修改时间/视觉投影标记），勾选或单行删除。
- `service/model_scanner.list_local_models`（列表）+ `service/model_file_service`
  （`pair_mmproj` 配对视觉投影、`delete_model_files` 逐项容错删除）。
- `ScriptService.remove_binding_for_model(model_path, names)`：`.bat` 移入
  `data/scripts_replaced`（不物理删，可找回）+ 清 `scripts.json` 条目；匹配覆盖
  "json 条目（含 .bat 已不存在的过期条目）/ 孤儿 .bat / derive_name 兜底"三路来源。
- **运行中拦下**：删除 worker 先查 `ProcessService.is_running(绑定脚本名)`（该调用内部
  跑 `tasklist`，约 0.6s，故必须在 worker 里），命中则整体不删并提示先去"运行控制"结束。
- 删除后主窗口按信号清理已失效的当前选择（显式 `setText("")` + 重刷下拉，见 traps #42）。

**教训**：**删文件类功能必须同时处理"关联数据"与"占用状态"**——只删文件会留下
死绑定（比删不掉更隐蔽），只看 `os.remove` 的报错会把"文件被占用"说成"删除失败"。

***

