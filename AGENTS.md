# AGENTS.md

LlamaCPP GUI —— 基于 PyQt6 的桌面客户端（仅支持 Windows 10/11），用于管理本地的 llama.cpp 推理服务器。

## 项目全景

本工具已演变为一个围绕 llama.cpp 推理服务器的多功能桌面客户端，包含四个核心子系统：

| 子系统 | 说明 |
|--------|------|
| **llama.cpp 服务管理** | 管理 `llama-server.exe` 启动脚本的创建/编辑/运行/停止 |
| **模型搜索与下载** | 从 ModelScope 搜索模型、浏览文件、断点续传下载 |
| **系统性能监控** | CPU/内存/NVIDIA GPU 实时监控 + 推理速度 (t/s) 图表 |
| **AI 聊天后端** | HTTP 桥服务（含角色管理、对话管理、长期记忆、定时任务调度） |

## 运行

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

程序入口：`main.py` → `ui.app.main()`。本仓库没有命令行工具或独立的 Web 服务。

## 容易被忽略的约定

- **仅支持 Windows。** 生成的启动脚本是 Windows `.bat` 文件
  （`model/script.py` → `data/scripts/*.bat`）；不要把相关逻辑改写成 POSIX shell。
  进程管理（`service/process_service.py`）使用了 `taskkill`、`tasklist` 等 Windows 命令。
- **配置为单例模式。** 应用配置位于 `config/config.py`，通过
  `Settings.get_instance()` 访问（懒加载自 `data/app_config.json`）。**不要直接**
  实例化 `Settings()` —— 始终使用单例。
- **`data/` 是运行时状态，不是源码。** 已被 gitignore，首次运行时自动创建
  （配置文件、已保存脚本、下载队列、`last_pid.pid`、聊天数据）。不要提交它。
- **没有测试、CI 或 lint/typecheck。** 均未配置。修改后应通过手动运行 GUI 验证
  （需要 PyQt6 和真实显示环境）。
- **GPU 监控仅支持 NVIDIA。** 通过 `nvidia-ml-py` (NVML) 实现；未检测到显卡时在监控页
  会优雅降级显示。
- **QThread 必须放在 UI 层。** 所有继承 `QThread` 的 Worker 类放在 `ui/workers/` 目录下，
  因为它们需要发射 PyQt6 `pyqtSignal`，这是 UI 层的关注点，不应混入 service 层。

## 打包

使用 PyInstaller 打包独立 `.exe`，**在 PowerShell 中执行**（见 `打包命令.txt`）：

```powershell
pyinstaller -D -w --name='Lammacpp启动器' --add-data "ui/chat_webui;data/webui" main.py
```

产物位于 `dist/Lammacpp启动器/`。

## 项目结构（职责划分）

```
main.py                         入口：调用 ui.app.main()
│
├── config/                     配置系统
│   ├── __init__.py             路径常量集中管理（SCRIPTS_DIR, LAST_PID_FILE 等）
│   └── config.py               Settings 单例（llamacpp_path, model_path, download_path 等）
│
├── model/                      纯数据模型（无 Qt 依赖）
│   ├── script.py               ScriptEntry —— 启动脚本数据
│   └── download_entry.py       DownloadEntry + DownloadQueue —— 下载任务数据与队列持久化
│
├── service/                    后端业务逻辑层
│   ├── script_service.py       脚本 CRUD（data/scripts/*.bat + data/scripts.json）
│   ├── script_builder.py       启动脚本参数模板（CATEGORIES）+ .bat 内容生成
│   ├── process_service.py      llama-server.exe 子进程生命周期管理
│   ├── modelscope.py           ModelScope API 客户端（搜索、文件列表、下载）
│   ├── download_service.py     下载管理器（DownloadManager + DownloadWorker QThread 封装）
│   ├── monitor_service.py      系统资源采样（CPU/RAM/GPU 定时推送）
│   └── path_service.py         ensure_webui —— 静态文件部署
│
├── chat/                       独立聊天后端子系统（自包含，不依赖其他模块）
│   ├── __init__.py             导出 start_bridge()
│   ├── server.py               入口：find_free_port, start_bridge, 数据迁移
│   ├── handlers.py             BridgeHandler —— HTTP REST API（GET/POST/PUT/DELETE）
│   ├── repository.py           数据访问层：JSON 文件读写（agents/conversations/memory/settings/task_index）
│   ├── scheduler.py            SchedulerService —— 定时任务调度器（守护线程）
│   └── models.py               DEFAULT_AGENT 常量 + 数据模型
│
├── ui/                         PyQt6 界面层
│   ├── app.py                  主窗口 MainWindow + main() 入口
│   ├── model_tab.py            模型搜索与下载标签页（委托 DownloadManager 执行下载）
│   ├── monitor_tab.py          性能监控标签页（TPS 图表 + CPU/GPU/RAM）
│   ├── dialogs/
│   │   └── new_script_dialog.py 新建启动脚本对话框（使用 script_builder.CATEGORIES）
│   ├── workers/                 QThread 工作线程（UI 层信号通信）
│   │   ├── log_worker.py        LogWorker —— 读取子进程输出、检测就绪 URL、解析 t/s
│   │   └── update_workers.py    CheckUpdateWorker + CheckAppUpdateWorker —— 版本检查
│   └── chat_webui/              聊天前端静态文件（chat.html/css/js + marked.min.js）
│
└── utils/                      通用工具
    ├── logger.py               日志配置
    └── validator.py            文件/路径验证（validate_gguf, validate_llamacpp_file 等）
```

## 模块化开发策略与注意事项

### 各层职责边界

```
┌─────────────────────────────────────────────────┐
│  ui/       PyQt6 表现层：窗口、控件、信号连接    │
│           ↕ 委托调用 / 信号监听                   │
│  service/ 业务逻辑层：不直接操作 Qt 控件          │
│           ↕ 方法调用                              │
│  model/   纯数据层：dataclass, 序列化/反序列化    │
│  chat/    独立子系统：自包含后端，通过 start_bridge 暴露  │
│  config/  配置 + 路径常量                        │
└─────────────────────────────────────────────────┘
```

#### UI 层规则
- **ui/app.py** 只放 MainWindow 和 main()。如果需要对话框或线程，放到 `ui/dialogs/` 或 `ui/workers/`。
- QThread 继承类必须放在 `ui/workers/`，因为它们通过 pyqtSignal 与 UI 通信。
- 对话框 QDialog 放到 `ui/dialogs/`。
- UI 层不直接操作文件、网络请求；委托给 service 层的方法去完成。

#### Service 层规则
- 不导入 `PyQt6.QtWidgets`（可以导入 `PyQt6.QtCore` 如果确实需要 QObject/QTimer）。
- 持有 QThread 的 Worker 类仅在 `download_service.py` 中（DownloadWorker 是 QThread），
  因为下载的生命周期管理是业务逻辑，不是 UI 关注点。
- 不直接操作 QWidget 或 QTableWidget 等控件。

#### chat/ 子系统规则
- 完全自包含：不依赖 `config/`、`service/`、`ui/`、`model/` 等外部模块。
- 对外仅暴露 `start_bridge()` 一个接口。
- 路径常量（CHAT_DIR, AGENTS_DIR 等）只在 chat/ 内部使用，不导出。
- 如果需要新增聊天 API 路由，在 `handlers.py` 的对应 `do_*` 方法中添加；
  涉及数据操作时调用 `repository.py` 的函数，不直接读写文件。

### 扩展指南

| 要做的改动 | 应修改的文件 | 不应动的文件 |
|-----------|-------------|-------------|
| 新增启动脚本参数 | `service/script_builder.py`（CATEGORIES） | `ui/app.py` |
| 新增对话框 | `ui/dialogs/` | `service/` |
| 新增聊天 API 端点 | `chat/handlers.py` + `chat/repository.py` | `ui/`、`service/` |
| 修改下载行为 | `service/download_service.py` | `ui/model_tab.py` |
| 修改监控指标 | `service/monitor_service.py` | `ui/monitor_tab.py` |
| 修改数据文件格式 | `model/` | `ui/`、`service/` |
| 新增配置项 | `config/config.py`（Settings） | `chat/` |

### 新增功能时的检查清单

1. **确定归属层**——这个功能是 UI 交互、业务逻辑还是纯数据？放到对应的包里。
2. **检查现有服务**——`service/` 下是否有同类功能的模块？有则扩展，无则新建一个文件。
3. **路径常量**——如果用到了 `data/` 下的新路径，注册到 `config/__init__.py`（核心系统）
   或 chat 模块的 `repository.py`（聊天子系统）。
4. **不要碰无关文件**——新功能只修改归属层和接口层的文件。
5. **验证**——运行 GUI 手动验证功能正常，确认没破坏主控制页的启动/停止流程。

### 注意事项（重申）
- **禁止大范围无关重构**。新功能只改归属层代码，不要顺手重构无关模块。
- **改动后需要总结**。说明改了什么文件、改动的理由和影响范围。
- **减少手写代码**。有现成的标准库或 PyQt6 内置功能时优先使用。
- QDialog 枚举值（如 `QDialog.DialogCode.Accepted`）通过 `from PyQt6.QtWidgets import QDialog` 导入使用，不要硬编码数值。
- 不要直接实例化 `Settings()`，始终用 `Settings.get_instance()`。
- `.bat` 脚本生成逻辑在 `service/script_builder.py`，不要在其他地方手动拼接批处理命令。
- 权限判定：`utils/validator.py` 中的 `validate_llamacpp_file` 检查文件名是否为 `llama-server.exe`；
  `validate_gguf` 检查文件是否存在且扩展名为 `.gguf`。
- QThread 信号用 `pyqtSignal` 声明，不要在 QThread 子类中直接操作 UI 控件。
