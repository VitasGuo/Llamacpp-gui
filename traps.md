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

**当前状态**：#2 已修复（v1.2.1）。

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
教训：跨回调的就绪标志（done/handled 类）必须在其数据源回调里显式置位，
且这类"条件永远不满足"的 bug 只能靠链路级冒烟测试暴露——单测纯函数全绿
不代表 UI 编排层正确。

***

