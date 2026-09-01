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
`allow_reuse_address=1`（Windows 的 SO_REUSEADDR 允许重叠绑定），新起的测试
服务器 bind 18765 "成功"并返回该端口，但实际 TCP 连接被 OS 交给**旧进程**的
socket —— 旧代码没有 `/bridge/info`，走 `_serve_static` 返回 404；debug print
也打在无人接收的 socket 上，自然看不到。

**解决方案**：验证桥服务改动前先确认没有旧 GUI/pythonw 实例占用桥端口
（`Get-NetTCPConnection -LocalPort <port> -State Listen` / `Get-Process pythonw`）；
测试时临时改名 `data/chat/bridge_port.txt` 让新服务绑定**全新端口**，验证完再恢复。
真实使用场景中：改了桥服务代码后，**必须完全退出（含托盘）所有旧实例再重启**，
否则旧桥继续用内存里的旧代码。

**教训**：调试"本地端口服务"时，`bind 成功` ≠ `请求到达我的服务`。Windows 的
SO_REUSEADDR 会让新进程对同一端口"假成功"重叠绑定，请求仍由先绑定者接收。
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
3. `psutil.net_if_addrs()` 枚举网卡匹配 100.64.0.0/10 段的 IPv4 兜底。
每级结果都用 `is_tailscale_ip()` 校验落在 CGNAT 段内才采用；全部失败返回空串，
对话框侧回退 `0.0.0.0` 保证脚本仍能启动。

**教训**：GUI/打包程序里"外部可执行文件是否在 PATH"不可假设，要有已知路径 +
系统 API（psutil 网卡枚举）多级兜底；对检测结果做协议/网段校验能避免误收。
（Tailscale 的 CGNAT 段 100.64.0.0/10 是判断"这是 Tailscale 地址"的可靠依据。）
