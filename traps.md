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

