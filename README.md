# LlamaCPP GUI

一个基于 PyQt6 的桌面客户端，专为 [llama.cpp](https://github.com/ggml-org/llama.cpp) 打造。无需命令行即可管理本地大语言模型推理服务。

仅支持 **Windows 10/11**。

## 功能

| 功能 | 说明 |
|------|------|
| 🚀 **llama-server 管理** | 图形化配置路径，参数模板生成启动脚本，一键运行/停止；**选模型即自动生成参数**（脚本绑定模型，无独立脚本名）+ **表单式脚本编辑**（填空/下拉/勾选，可展开看 .bat 原文）+ **重置参数**（清掉已保存脚本、重新生成一版默认脚本）；**运行中模型清单**——多模型可同时运行，每行显示运行时长并可单独"结束"任一模型，顶部"全部结束"一键停掉全部运行中的模型 |
| 🔍 **模型搜索与下载** | 集成 ModelScope 与 hf-mirror.com，搜索模型、浏览 GGUF 文件、断点续传下载；**未搜索时默认显示"更新追踪"视图**——自动关注本地已装模型系列 + 搜索结果手动追踪，后台对比远端 `LastUpdatedTime` 有新版标黄，每行"查看文件"直达该模型文件列表下载 |
| 📊 **性能监控** | 主控制页内嵌 CPU/内存/NVIDIA GPU 实时监控 + 推理速度 t/s（历史采样落盘 `data/history/`，保留 7 天） |
| 💬 **聊天界面** | 服务器就绪后一键在浏览器中打开 Web 聊天 UI |
| 🤖 **AI 聊天后端** | 支持多角色对话、长期记忆、定时任务调度的 HTTP 桥服务 |
| 🛡 **系统托盘** | 关闭窗口最小化到托盘，托盘菜单可显示/退出/切换开机自启动 |
| 🚀 **开机自启动** | 一键注册/取消开机自动启动（HKCU Run，源码用 pythonw，打包后自启 exe） |
| 🗂 **本地模型检索** | 指定本地目录自动递归扫描 .gguf，下拉菜单快速选择模型，无需手动逐个选择 |
| 🌐 **Tailscale 外网接入** | 新建脚本时 `--host` 可选"仅本机 / 所有接口 / Tailscale 专用"（自动检测本机 Tailscale IP），服务就绪后显示外网访问地址并可一键复制 |
| 🔄 **llama.cpp 版本管理** | 检测更新（对比本地 build 号与 GitHub 最新版）、按显卡驱动推荐构建变体（CUDA 12.4 / 13.x / CPU / Vulkan 等）、断点续传下载安装（直连或自定义镜像前缀）、版本化目录 + 一键切换回滚（自动批量更新启动脚本路径）、启动时后台静默下载最新版（当前通道 + 推荐变体，就绪后进页一键切换） |

### 支持的推理参数

| 类别 | 参数 |
|------|------|
| 通用 | `--gpu-layers` GPU 层数、`--port` 端口、`--ctx-size` 上下文大小（挡位下拉：按模型 GGUF 元数据上限生成 8K~1M 挡位，上限<128K 默认取模型最大值、≥128K 默认 128K，可手填任意值）、`--alias` 别名、`--host` 监听方式（仅本机/所有接口/Tailscale 专用） |
| 模型 | `--mmproj` 视觉模型、`--reasoning off` 关闭思考、`--main-gpu` 主 GPU、`-ts` 多卡负载 |
| MTP | `--spec-type` 预测类型、`--spec-draft-n-max` 草稿长度 |
| 量化 | `--cache-type-k`、`--cache-type-v` KV 缓存量化 |
| MOE | `--n-cpu-moe` CPU 线程、`--no-mmap-fallback` 禁用回退 |

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

1. **配置路径** — 在主控制页选择 `llama-server.exe` 和模型目录（自动扫描 `.gguf`）
2. **选模型即生成脚本** — 选中模型自动生成基础参数（脚本绑定模型，无独立脚本名；模型别名=模型文件名自动生成），在常用参数表单微调，点"保存"绑定；想丢弃已保存的微调、回到出厂默认时点"重置参数"；ctx 上下文自动按模型上限选好默认挡位（可下拉换挡或手填）；高级参数（并发/MTP/MOE）在表单底部"高级参数"区可展开调整
3. **启动服务** — 点"运行"（运行当前模型绑定的脚本），在日志窗口查看输出
4. **聊天** — 服务就绪后点击"聊天窗口"在浏览器中打开 Web UI
5. **搜索模型** — 切换到"模型搜索与下载"标签页，选择 ModelScope 或 Hugging Face 镜像搜索模型
6. **监控** — 主控制页右下角已内置压缩版 CPU/GPU/TPS 监控（与日志并排）

## 数据文件

应用自动在 `data/` 目录下创建运行时数据：

| 位置 | 说明 |
|------|------|
| `data/app_config.json` | 路径等配置 |
| `data/scripts.json` + `data/scripts/*.bat` | 启动脚本 |
| `data/scripts_replaced/` | 脚本绑定清理时被淘汰的 .bat 备份（不删除，可人工找回） |
| `data/downloads/queue.json` | 下载队列状态 |
| `data/chat/` | 聊天数据（对话、角色、记忆、定时任务） |
| `data/last_pid.pid` | 旧版本运行中服务器进程 ID（多服务器架构下仅回退用途） |
| `data/history/` | t/s 推理速度历史采样（按天 JSONL，保留 7 天） |
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
│   ├── watchlist_service.py 模型更新追踪关注列表（含 ignored 持久化）
│   ├── download_service.py 下载管理器
│   └── monitor_service.py  系统资源采样
├── chat/                   独立聊天后端子系统
├── ui/                     界面层
│   ├── app.py              主窗口
│   ├── model_tab.py        模型搜索与下载标签页（内含更新追踪默认视图）
│   ├── model_watch_tab.py  更新追踪视图 ModelWatchView（嵌入 model_tab，未搜索时显示）
│   ├── monitor_tab.py      压缩版系统监控 CompactMonitor（内嵌主控制页）
│   ├── update_tab.py       llama.cpp 版本管理标签页
│   ├── dialogs/            对话框
│   └── workers/            后台工作线程（日志/状态轮询/搜索/版本安装/更新追踪/Tailscale 探测/清理规划/进程清理）
├── tests/                  冒烟测试（unittest，offscreen 可跑）
└── utils/                  通用工具（logger, validator, atomic_io, path_utils, semver）
```

## 路径规范（关键设计决策）

- 所有路径以**正斜线 `/`** 为唯一规范形式存储与展示（`C:/modelscope/...`）。
- 原因：跨平台一致（POSIX 原生）、JSON 免转义、Qt/QFileDialog 原生返回即正斜线、
  Windows 文件 API / cmd / llama-server 完全兼容。
- 统一在**写入点**收口：配置 setter、脚本生成（script_builder）、版本切换
  （replace_bat_dir）均过 `utils/path_utils.normalize_path()`；
  `os.path.join` 等产物（Windows 下产 `\`）在进入存储前必须规范化。
- 脚本绑定模型：`ScriptEntry.derive_name(model_path)` 由模型路径推导脚本名（文件名 + 路径短 hash，
  跨目录同名不冲突）；`script_service.get_script_for_model` 按归一化模型路径查找。凡"按脚本定位文件"须经
  `ScriptEntry.derive_name` / `sanitize_filename` 后的真实文件名（详见 traps #17）。
- **一个模型只绑定一条脚本**（不变量，v1.19.0 起强制）：读取端
  `get_script_for_model` 对同 `model_path` 的多个历史候选按
  `name_model_score`（脚本名与模型文件名的 token 契合度）> 置顶 > 最新保存 择优；
  写入端 `_upsert_config_entry` 把"同归一化 `model_path`"视为同一条（改名保存即合并，
  旧 .bat 一并清理）；启动时 `migrate_bindings()` 以 `.bat` 的 `-m` 为事实源校正
  `model_path`、合并重复绑定（淘汰 .bat 备份到 `data/scripts_replaced`）、清掉幽灵条目，
  幂等（详见 traps #38）。**`.bat` 是脚本内容的唯一来源**，`scripts.json` 的派生字段必须能由它重建。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover tests
```

## 系统要求

- Windows 10/11
- Python 3.10+
- 依赖：PyQt6, psutil, nvidia-ml-py, Pillow

## 许可证

MIT License
