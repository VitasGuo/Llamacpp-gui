# LlamaCPP GUI

一个基于 PyQt6 的桌面客户端，专为 [llama.cpp](https://github.com/ggml-org/llama.cpp) 打造。无需命令行即可管理本地大语言模型推理服务。

仅支持 **Windows 10/11**。

## 功能

| 功能 | 说明 |
|------|------|
| 🚀 **llama-server 管理** | 图形化配置路径，参数模板生成启动脚本，一键运行/停止 |
| 🔍 **模型搜索与下载** | 集成 ModelScope 与 hf-mirror.com，搜索模型、浏览 GGUF 文件、断点续传下载 |
| 📊 **性能监控** | CPU/内存/NVIDIA GPU 实时监控 + 推理速度 TPS 折线图 |
| 💬 **聊天界面** | 服务器就绪后一键在浏览器中打开 Web 聊天 UI |
| 🤖 **AI 聊天后端** | 支持多角色对话、长期记忆、定时任务调度的 HTTP 桥服务 |
| 🔄 **版本检查** | 检查 llama.cpp 和本应用的最新 GitHub 版本 |
| 🛡 **系统托盘** | 关闭窗口最小化到托盘，托盘菜单可显示/退出/切换开机自启动 |
| 🚀 **开机自启动** | 一键注册/取消开机自动启动（HKCU Run，源码用 pythonw，打包后自启 exe） |
| 🗂 **本地模型检索** | 指定本地目录自动递归扫描 .gguf，下拉菜单快速选择模型，无需手动逐个选择 |
| 🌐 **Tailscale 外网接入** | 新建脚本时 `--host` 可选"仅本机 / 所有接口 / Tailscale 专用"（自动检测本机 Tailscale IP），服务就绪后显示外网访问地址并可一键复制 |
| 🔄 **llama.cpp 版本管理** | 检测更新（对比本地 build 号与 GitHub 最新版）、按显卡驱动推荐构建变体（CUDA 12.4 / 13.x / CPU / Vulkan 等）、断点续传下载安装（直连或自定义镜像前缀）、版本化目录 + 一键切换回滚（自动批量更新启动脚本路径）、启动时后台静默下载最新版（当前通道 + 推荐变体，就绪后进页一键切换） |

### 支持的推理参数

| 类别 | 参数 |
|------|------|
| 通用 | `--gpu-layers` GPU 层数、`--port` 端口、`--ctx-size` 上下文大小、`--alias` 别名、`--host` 监听方式（仅本机/所有接口/Tailscale 专用） |
| 模型 | `--mmproj` 视觉模型、`--reasoning off` 关闭思考、`--main-gpu` 主 GPU、`-ts` 多卡负载 |
| MTP | `--spec-type` 预测类型、`--spec-draft-n-max` 草稿长度 |
| 量化 | `--cache-type-k`、`--cache-type-v` KV 缓存量化 |
| MOE | `--n-cpu-moe` CPU 线程、`--mmap` 内存映射、`--no-mmap-fallback` 禁用回退 |

## 快速开始

```bash
git clone https://github.com/kkblank/Llamacpp-gui.git
cd Llamacpp-gui
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 打包为独立 EXE

```powershell
pip install pyinstaller
pyinstaller -D -w --name='Lammacpp启动器' --add-data "ui/chat_webui;data/webui" main.py
```

产物位于 `dist/Lammacpp启动器/`。

## 首次使用

1. **配置路径** — 在主控制页选择 `llama-server.exe` 和 `.gguf` 模型文件
2. **创建脚本** — 点击"新建"，勾选参数，自动生成 `.bat` 启动脚本
3. **启动服务** — 选中脚本点击"运行"，在日志窗口查看输出
4. **聊天** — 服务就绪后点击"聊天窗口"在浏览器中打开 Web UI
5. **搜索模型** — 切换到"模型搜索与下载"标签页，选择 ModelScope 或 Hugging Face 镜像搜索模型
6. **监控** — 切换到"性能监控"标签页查看 CPU/GPU/TPS

## 数据文件

应用自动在 `data/` 目录下创建运行时数据：

| 位置 | 说明 |
|------|------|
| `data/app_config.json` | 路径等配置 |
| `data/scripts.json` + `data/scripts/*.bat` | 启动脚本 |
| `data/downloads/queue.json` | 下载队列状态 |
| `data/chat/` | 聊天数据（对话、角色、记忆、定时任务） |
| `data/last_pid.pid` | 旧版本运行中服务器进程 ID（多服务器架构下仅回退用途） |
| `data/app.lock` | 单实例锁（防止多个 GUI 并发运行） |
| `data/webui/` | 聊天前端静态文件 |
| `data/llamacpp/` | llama.cpp 版本管理默认根目录：`{tag}-{variant}/` 每版本一目录（可配置到其他盘），`zips/` 为下载缓存（断点续传） |

## 项目结构

```
main.py
├── config/                 配置 + 路径常量
├── model/                  纯数据模型（ScriptEntry, DownloadEntry）
├── service/                业务逻辑层
│   ├── script_builder.py   启动脚本参数模板与 .bat 生成
│   ├── script_service.py   脚本 CRUD
│   ├── process_service.py  进程生命周期管理
│   ├── tailscale.py        Tailscale IP 检测（外网接入）
│   ├── llamacpp_update_service.py  llama.cpp 版本管理（检测/下载/安装/切换）
│   ├── modelscope.py       ModelScope API 客户端
│   ├── download_service.py 下载管理器
│   └── monitor_service.py  系统资源采样
├── chat/                   独立聊天后端子系统
├── ui/                     界面层
│   ├── app.py              主窗口
│   ├── model_tab.py        模型搜索与下载标签页
│   ├── monitor_tab.py      性能监控标签页
│   ├── update_tab.py       llama.cpp 版本管理标签页
│   ├── dialogs/            对话框
│   └── workers/            后台工作线程（日志/状态轮询/搜索/版本检查/版本安装）
├── tests/                  冒烟测试（unittest，offscreen 可跑）
└── utils/                  通用工具（logger, validator, atomic_io, path_utils）
```

## 路径规范（关键设计决策）

- 所有路径以**正斜线 `/`** 为唯一规范形式存储与展示（`C:/modelscope/...`）。
- 原因：跨平台一致（POSIX 原生）、JSON 免转义、Qt/QFileDialog 原生返回即正斜线、
  Windows 文件 API / cmd / llama-server 完全兼容。
- 统一在**写入点**收口：配置 setter、脚本生成（script_builder）、版本切换
  （replace_bat_dir）均过 `utils/path_utils.normalize_path()`；
  `os.path.join` 等产物（Windows 下产 `\`）在进入存储前必须规范化。
- 脚本名与 .bat 文件名可能不同：`ScriptEntry.sanitize_filename()` 会把空格/
  特殊字符转为下划线，凡"按脚本名定位文件"必须经 sanitize 后的真实文件名
  （详见 traps #17）。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover tests
```

## 系统要求

- Windows 10/11
- Python 3.10+
- 依赖：PyQt6, psutil, nvidia-ml-py, PyQt6-Charts

## 许可证

MIT License
