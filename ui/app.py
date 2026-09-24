"""llama.cpp GUI 主窗口。"""
import sys
import os
import re
import time
import threading
import subprocess
import webbrowser
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QTextEdit,
    QMessageBox,
    QTabWidget, QFileDialog, QSplitter,
    QSystemTrayIcon, QMenu, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor, QAction, QColor, QPixmap, QPainter, QFont, QIcon

from config.config import Settings
from utils.validator import validate_llamacpp_file, validate_gguf
from utils.path_utils import normalize_path
from utils.logger import error, info
from service.script_service import ScriptService
from service.script_builder import (
    build_bat_content, extract_port, extract_host, find_mmproj,
    auto_generate_config, parse_bat_params, parse_model_path_from_bat,
)
from service.process_service import ProcessService
from service.tailscale import is_tailscale_ip
from service.monitor_service import MonitorService
from service.path_service import ensure_webui
from service import autostart_service
from service.model_scanner import scan_gguf_files
from chat import start_bridge
from model.script import ScriptEntry
from ui.model_tab import ModelTab
from ui.monitor_tab import CompactMonitor, format_uptime
from ui.script_form_widget import ScriptFormWidget
from ui.update_tab import UpdateTab
from ui.dialogs.settings_dialog import SettingsDialog
from ui.workers.log_worker import LogWorker
from ui.workers.status_worker import StatusPoller
from ui.workers.tailscale_worker import TailscaleProbeWorker
from ui.workers.process_worker import KillAllLlamaWorker

# 日志面板行数上限：保留最近 N 个块（行）；每追加 M 条检查一次，避免刷屏时频繁裁剪
LOG_PANEL_MAX_BLOCKS = 2000
LOG_PANEL_TRIM_EVERY = 50

# Tailscale 自动探测的 GUI 侧缓存窗口（与 service.tailscale 的探测缓存同量级）
TS_PROBE_TTL = 60.0

# "运行控制"运行中清单里旧版 data/last_pid.pid 恢复实例的展示名
LEGACY_NAME = "旧实例"


def _resolve_llm_host(host, tailscale_override, ts_cache):
    """聊天地址 host 解析（线程安全，纯读）：0.0.0.0/空 → 手动指定的
    Tailscale IP → 自动探测缓存 → 127.0.0.1。绝不跑探测子进程（该方法会
    被桥服务 HTTP 线程调用，探测/QThread 都只能在 GUI 线程发起）。"""
    host = (host or "").strip()
    if host in ("0.0.0.0", ""):
        if tailscale_override and is_tailscale_ip(tailscale_override):
            return tailscale_override
        return ts_cache or "127.0.0.1"
    return host


def _pick_llm_url(current_name, server_urls, runtime, tailscale_override, ts_cache):
    """挑选聊天页自动填充的模型 API 地址（纯函数，便于回归测试）。

    两层来源，每层都"选中脚本的记录优先，否则回退任意运行中记录"：
    1. 本会话服务就绪 URL（LogWorker 报告，可能含 0.0.0.0/Tailscale host）；
    2. pids.json 运行时记录（跨会话恢复的服务）。
    脚本绑定模型后用户常切到其他模型调参——回退保证聊天页仍指向
    正在运行的那个模型（否则两层都只查选中名，切走即取不到，traps #34）。
    """
    urls = []
    if current_name and current_name in server_urls:
        urls.append(server_urls[current_name])
    urls.extend(u for k, u in server_urls.items() if k != current_name)
    entries = []
    if current_name and current_name in runtime:
        entries.append(runtime[current_name])
    entries.extend(v for k, v in runtime.items() if k != current_name)
    for url in urls:
        m = re.match(r"http://([^:]+):(\d+)", url or "")
        if m:
            host = _resolve_llm_host(m.group(1), tailscale_override, ts_cache)
            return f"http://{host}:{m.group(2)}"
    for entry in entries:
        port = entry.get("port")
        if isinstance(port, int) and port > 0:
            host = _resolve_llm_host(entry.get("host") or "127.0.0.1",
                                     tailscale_override, ts_cache)
            return f"http://{host}:{port}"
    return ""


def combo_index_for(paths, target):
    """在路径列表里找目标项下标（规范化 + 大小写不敏感；找不到返回 -1）。

    Windows 路径不区分大小写；且扫描结果来自 os.path.join（反斜线），
    配置里存的却是 normalize_path 后的正斜线——不归一就永远匹配不上，
    下拉会停在第一项而下方路径/表单仍是原模型（traps #42）。
    """
    want = normalize_path(target or "").lower()
    if not want:
        return -1
    for i, p in enumerate(paths):
        if normalize_path(p or "").lower() == want:
            return i
    return -1


def stop_all_targets(run_rows, runtime, legacy_running):
    """「全部结束」的停止目标（纯函数，便于回归测试）。

    清单（run_rows 的键）∪ pids.json 运行时记录（runtime 的键），去重保序：
    跨会话恢复的服务可能还没进清单，只按清单结束会漏掉它、残留占用端口。
    旧实例（无脚本归属、不在 runtime 键里）由第二项返回值单独标识。
    """
    names = [n for n in run_rows if n and n != LEGACY_NAME]
    for name in runtime or {}:
        if name and name != LEGACY_NAME and name not in names:
            names.append(name)
    legacy = bool(legacy_running) or (LEGACY_NAME in run_rows)
    return names, legacy


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.get_instance()
        self.script_service = ScriptService()
        self.process_service = ProcessService()
        self.current_script_name = ""
        self.is_running = False
        self.log_worker = None
        self.log_workers = []  # 多服务器：所有活动 LogWorker（保持引用防 GC）
        self._server_urls = {}  # 多服务器：脚本名 -> 服务就绪 URL（按脚本归属，避免串扰）
        self._ts_url = ""
        self._bridge_server = None
        self._bridge_port = None
        self._log_append_count = 0
        self._force_quit = False
        self.tray_icon = None
        # 后台轮询结果的缓存（GUI 线程内判断运行状态只查缓存，
        # 绝不再同步调 tasklist —— 那会阻塞主线程 ~0.6s）
        self._last_runtime = {}
        self._last_alive = set()
        # 旧版本 data/last_pid.pid 恢复的全局进程是否存活（启动时验证一次）
        self._legacy_running = False
        # "运行控制"运行中清单：脚本名 -> 启动时间 ts；行控件缓存（脚本名 -> (时长标签, 结束按钮)）
        self._run_times = {}
        self._run_rows = {}
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._uptime_timer.start()
        # Tailscale 自动探测缓存（GUI 只读，探测在后台 TailscaleProbeWorker）
        self._ts_ip = ""
        self._ts_ip_probed_at = 0.0
        self._ts_probe_worker = None
        # "清理全部llama进程"后台 worker（保引用防 GC）
        self._killall_worker = None

        self.setWindowTitle("llama.cpp GUI Client")
        self.resize(960, 700)

        self.monitor_service = MonitorService(self.process_service)
        # 主控制页嵌入压缩版系统监控（与日志并排）：CPU/RAM/GPU/VRAM/温度/TPS，
        # 加载模型时边看日志边看负载。原独立"性能监控"标签页已并入此处（v1.15.0）。
        self.monitor_compact = CompactMonitor(self.monitor_service)

        # 多服务器：后台线程每 2s 轮询 tasklist 刷新脚本列表状态列与控制面板
        # （tasklist 约 0.5~0.7s，放 GUI 线程会卡顿，故用后台 QThread）。
        # 必须在 _init_ui/_restore_service_state 之前创建，因为它们内部会调用
        # _refresh_script_statuses() → _status_poller.request_poll()。
        self._status_poller = StatusPoller(self.process_service, interval=2.0, parent=self)
        self._status_poller.status_refreshed.connect(self._apply_script_statuses)
        self._status_poller.start()

        self._init_ui()
        self._init_tray()
        self._migrate_script_bindings()
        self._load_saved_paths()
        self._restore_service_state()

        self.monitor_service.start()
        ensure_webui()
        self._start_bridge()

    def closeEvent(self, event):
        # 托盘可用且非主动退出时，关闭仅隐藏到系统托盘，不终止进程
        if self.tray_icon is not None and not self._force_quit:
            self.hide()
            event.ignore()
            return
        self._status_poller.stop()
        self._status_poller.wait(300)  # 轮询线程正跑 tasklist 时不能运行中被销毁
        # Tailscale 后台探测线程同理：短暂等待后不再等（进程退出时线程终止）
        if self._ts_probe_worker and self._ts_probe_worker.isRunning():
            self._ts_probe_worker.wait(300)
        # 通知所有 LogWorker 退出；readline 可能阻塞，短暂等待后不再等
        # （保留引用防止 QThread 在运行中被销毁导致崩溃，进程退出时线程终止）
        for w in self.log_workers:
            w.stop()
        for w in self.log_workers:
            w.wait(300)
        # 桥服务 shutdown() 会阻塞等 serve_forever 的 poll 周期（~0.5s）；
        # serve_forever 是 daemon 线程，进程退出即被 OS 回收并关闭 socket 释放端口，
        # 故在后台线程触发、不阻塞 GUI，避免拖慢退出/托盘重启。
        if self._bridge_server:
            t = threading.Thread(target=self._bridge_server.shutdown, daemon=True)
            t.start()
        self.monitor_service.stop()
        super().closeEvent(event)

    def _init_tray(self):
        """初始化系统托盘图标（系统不支持时静默跳过）。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(self)
        icon = self._create_app_icon()
        self.setWindowIcon(icon)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("llama.cpp GUI Client")
        self.tray_icon.activated.connect(self._on_tray_activated)

        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._show_from_tray)
        menu.addAction(show_action)

        self.auto_start_action = QAction("开机自启动", self)
        self.auto_start_action.setCheckable(True)
        self.auto_start_action.setChecked(autostart_service.is_enabled())
        self.auto_start_action.toggled.connect(self._on_auto_start_toggled)
        menu.addAction(self.auto_start_action)

        menu.addSeparator()
        restart_action = QAction("重启", self)
        restart_action.triggered.connect(self._restart_app)
        menu.addAction(restart_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def _create_app_icon(self):
        """用绘制方式生成托盘/窗口图标（避免依赖外部图标资源）。"""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#2d8cf0"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "llm")
        painter.end()
        return QIcon(pixmap)

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_auto_start_toggled(self, checked):
        autostart_service.set_enabled(checked)
        info(f"开机自启动已{'启用' if checked else '关闭'}")

    def _quit_app(self):
        """从托盘菜单真正退出。"""
        self._force_quit = True
        self.close()

    def _restart_app(self):
        """托盘菜单"重启"：以当前解释器+参数重新启动应用（打包后为 exe），随后退出本实例。

        给新实例传 LLAMACPP_RESTARTING=1 标记，使其在 main() 抢单实例锁时用更长的
        重试窗口——旧实例退出释放锁需要一点时间（停 poller/logworker/bridge/monitor），
        否则新实例会误报"已在运行"。
        """
        self._force_quit = True
        cmd = [sys.executable] + list(sys.argv)
        env = dict(os.environ)
        env["LLAMACPP_RESTARTING"] = "1"
        try:
            # CREATE_NO_WINDOW：pythonw 无控制台运行时避免闪现 cmd 窗口（项目硬性约定）
            subprocess.Popen(
                cmd, cwd=os.getcwd(), env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            pass  # 启动失败时至少完成退出，不阻塞
        self.close()

    def _init_ui(self):
        # 菜单：设置入口
        settings_menu = self.menuBar().addMenu("设置")
        settings_action = QAction("设置...", self)
        settings_action.triggered.connect(self._open_settings)
        settings_menu.addAction(settings_action)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        tabs = QTabWidget()
        self.tabs_holder = tabs

        # 脚本参数表单（脚本绑定模型）：常用参数进主控制页，高级参数进独立标签页
        self.script_form = ScriptFormWidget()

        # 主控制标签
        control_widget = QWidget()
        control_layout = QVBoxLayout(control_widget)

        control_layout.addWidget(self._create_path_panel())
        control_layout.addWidget(self._create_script_panel(), stretch=2)

        # 日志与压缩系统监控并排：加载模型时边看日志边看负载，免切标签
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._create_log_panel(), stretch=3)
        bottom_row.addWidget(self.monitor_compact, stretch=1)
        control_layout.addLayout(bottom_row, stretch=1)

        tabs.addTab(control_widget, "主控制")

        # 模型搜索与下载标签（内含更新追踪默认视图）
        self.model_tab = ModelTab()
        self.model_tab.local_models_changed.connect(self._on_local_models_deleted)
        tabs.addTab(self.model_tab, "模型搜索与下载")

        # 版本管理标签（llama.cpp 检测更新/下载安装/切换）
        self.update_tab = UpdateTab()
        self.update_tab.version_switched.connect(self._on_version_switched)
        # 版本切换后用户选"立即重启" → 由主窗口执行应用重启
        self.update_tab.restart_requested.connect(self._restart_app)
        tabs.addTab(self.update_tab, "版本管理")

        # 切到"模型搜索与下载"时刷新追踪视图（并入新下载的本地模型 + 后台检查）
        tabs.currentChanged.connect(self._on_main_tab_changed)

        main_layout.addWidget(tabs)

    def _on_main_tab_changed(self, index):
        if self.tabs_holder.widget(index) is self.model_tab:
            self.model_tab.refresh_watch()

    def _on_version_switched(self, new_exe_path):
        """版本管理页切换 llama.cpp 版本后，同步主控制页路径显示。"""
        self.llamacpp_path_edit.setText(new_exe_path)

    def _create_path_panel(self):
        group = QGroupBox("路径配置")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("llama-server.exe 路径:"))
        self.llamacpp_path_edit = QLineEdit()
        self.llamacpp_path_edit.setReadOnly(True)
        row1.addWidget(self.llamacpp_path_edit)
        browse_btn1 = QPushButton("选择...")
        browse_btn1.clicked.connect(self._select_llamacpp_path)
        row1.addWidget(browse_btn1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("模型目录:"))
        self.model_dir_edit = QLineEdit()
        self.model_dir_edit.setPlaceholderText("选择目录后自动扫描其中 .gguf 模型")
        self.model_dir_edit.setReadOnly(True)
        row2.addWidget(self.model_dir_edit)
        browse_dir_btn = QPushButton("选择目录...")
        browse_dir_btn.clicked.connect(self._select_model_dir)
        row2.addWidget(browse_dir_btn)
        layout.addLayout(row2)

        return group

    def _create_script_panel(self):
        """启动脚本面板：模型即脚本。左右分栏 —— 左侧窄操作区（模型/视觉/动作），
        右侧宽参数表单（横向利用率高、垂向占用小）。"""
        group = QGroupBox("启动脚本")
        root_layout = QHBoxLayout(group)
        root_layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：操作区（模型选择、视觉模型、保存/删除/查看原文）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # 模型文件：从"路径配置"的模型目录自动扫描的下拉里选（不再提供浏览兜底）
        left_layout.addWidget(QLabel("模型文件 (.gguf):"))
        self.model_combo = QComboBox()
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_selected)
        left_layout.addWidget(self.model_combo)
        self.model_path_edit = QLineEdit(self)
        self.model_path_edit.setReadOnly(True)
        self.model_path_edit.setPlaceholderText("（模型的完整路径，只读）")
        left_layout.addWidget(self.model_path_edit)

        left_layout.addSpacing(6)
        left_layout.addWidget(QLabel("外挂视觉模型 (.gguf):"))
        self.visual_model_path_edit = QLineEdit(self)
        self.visual_model_path_edit.setReadOnly(True)
        left_layout.addWidget(self.visual_model_path_edit)
        browse_btn3 = QPushButton("选择...")
        browse_btn3.clicked.connect(self._select_visual_model_file)
        left_layout.addWidget(browse_btn3)

        left_layout.addSpacing(6)
        # 动作按钮：保存 / 重置参数（清掉已保存脚本、重新生成默认版）/ 查看生成原文
        act_row = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_script)
        act_row.addWidget(save_btn)
        reset_btn = QPushButton("重置参数")
        reset_btn.setToolTip(
            "清掉该模型已保存的脚本参数，按模型重新生成一版默认脚本"
            "（对模型文件无影响）"
        )
        reset_btn.clicked.connect(self._reset_script)
        act_row.addWidget(reset_btn)
        left_layout.addLayout(act_row)
        self.view_raw_btn = QPushButton("查看生成的脚本")
        self.view_raw_btn.setCheckable(True)
        self.view_raw_btn.toggled.connect(self._toggle_raw_view)
        left_layout.addWidget(self.view_raw_btn)

        left_layout.addStretch(1)

        # Tailscale 独立区块（外网访问配置）
        ts_box = QGroupBox("Tailscale")
        ts_lay = QVBoxLayout(ts_box)
        ts_lay.setSpacing(6)
        ts_row = QHBoxLayout()
        ts_row.addWidget(QLabel("IP:"))
        self.ts_ip_edit = QLineEdit()
        self.ts_ip_edit.setPlaceholderText("留空自动检测")
        self.ts_ip_edit.setText(self.settings.tailscale_ip)
        self.ts_ip_edit.setToolTip("指定 Tailscale 默认 IP（填 100.x.x.x），覆盖自动检测")
        self.ts_ip_edit.editingFinished.connect(self._on_ts_ip_changed)
        ts_row.addWidget(self.ts_ip_edit)
        ts_lay.addLayout(ts_row)
        ts_hint = QLabel("外网访问用。手动填 IP 覆盖自动检测；留空则自动检测。")
        ts_hint.setWordWrap(True)
        ts_lay.addWidget(ts_hint)
        left_layout.addWidget(ts_box)

        # 运行控制（运行/结束/状态/聊天/清理 + 外网地址），左栏最底部
        left_layout.addWidget(self._build_run_controls())

        splitter.addWidget(left)

        # 右：参数表单（常用参数在上，高级参数收在底部可折叠区）
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.script_form.widget)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([280, 900])
        root_layout.addWidget(splitter, 1)

        # 原文视图（默认隐藏；仅作查看，编辑以表单为准，占满整行宽度）
        self.script_editor = QTextEdit()
        self.script_editor.setReadOnly(True)
        self.script_editor.setPlaceholderText("（生成的 .bat 内容，只读预览）")
        self.script_editor.setVisible(False)
        root_layout.addWidget(self.script_editor)
        return group

    def _toggle_raw_view(self, checked):
        """展开/收起"查看生成的脚本"原文视图。"""
        self.script_editor.setVisible(checked)
        if checked:
            self.script_editor.setPlainText(self._form_to_content())

    def _form_to_content(self):
        """由表单 + 当前路径配置生成 .bat 内容（脚本渲染的事实源）。"""
        model_path = self.settings.model_path or parse_model_path_from_bat(
            self.script_editor.toPlainText())
        if not model_path:
            return ""
        exe_dir = os.path.dirname(self.settings.llamacpp_path) if self.settings.llamacpp_path else ""
        cfg = self.script_form.get_config()
        return build_bat_content(
            exe_dir, model_path, cfg,
            visual_model_path=self.settings.visual_model_path,
        )

    def _build_run_controls(self):
        """构建"运行控制"控件组（运行/结束/状态/聊天/清理 + 外网地址），
        并入启动脚本左栏（不再作为独立"控制面板"占一行垂向空间）。

        返回承载这些控件的小部件，由调用方加入布局。
        """
        box = QGroupBox("运行控制")
        root = QVBoxLayout(box)
        root.setSpacing(6)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("运行")
        self.run_btn.clicked.connect(self._run_script)
        btn_row.addWidget(self.run_btn)
        self.stop_btn = QPushButton("全部结束")
        self.stop_btn.setToolTip(
            "结束清单中全部运行中的模型（逐个结束；未通过本软件启动的实例"
            "请用下方\"清理全部llama进程\"）"
        )
        self.stop_btn.clicked.connect(self._stop_script)
        btn_row.addWidget(self.stop_btn)
        root.addLayout(btn_row)

        self.status_label = QLabel("\u25cf 就绪")
        self.status_label.setStyleSheet(
            "color: green; font-size: 12px; font-weight: bold;"
        )
        root.addWidget(self.status_label)

        # 正在运行的模型清单（支持多模型同时运行）：每行 模型名 | 运行时长 | 结束
        # （不再加"正在运行:"标题——清单本身就是答案，占一行垂直空间）
        self.run_list = QVBoxLayout()
        self.run_list.setSpacing(2)
        self.run_list_placeholder = QLabel("无模型运行")
        self.run_list_placeholder.setStyleSheet("color: #888; font-size: 12px;")
        self.run_list.addWidget(self.run_list_placeholder)
        root.addLayout(self.run_list)

        self.chat_btn = QPushButton("聊天窗口")
        self.chat_btn.setEnabled(True)
        self.chat_btn.clicked.connect(self._open_chat_window)
        root.addWidget(self.chat_btn)

        # 显式清理入口：不常用、放不突出位置，避免误触
        self.cleanup_all_btn = QPushButton("清理全部llama进程")
        self.cleanup_all_btn.setToolTip(
            "结束本机所有 llama-server.exe / main.exe 进程"
            "（包括未通过本软件启动的实例），点击需确认。"
        )
        self.cleanup_all_btn.clicked.connect(self._cleanup_all_processes)
        root.addWidget(self.cleanup_all_btn)

        # 外网访问地址行（Tailscale）：服务以 0.0.0.0 或 Tailscale IP 监听时显示
        ext_row = QHBoxLayout()
        self.ts_url_label = QLabel("")
        self.ts_url_label.setStyleSheet(
            "color: #2d8cf0; font-size: 12px; font-weight: bold;"
        )
        self.ts_url_label.setVisible(False)
        ext_row.addWidget(self.ts_url_label)
        self.ts_copy_btn = QPushButton("复制")
        self.ts_copy_btn.setVisible(False)
        self.ts_copy_btn.clicked.connect(self._copy_ts_url)
        ext_row.addWidget(self.ts_copy_btn)
        ext_row.addStretch()
        root.addLayout(ext_row)
        return box

    def _create_log_panel(self):
        group = QGroupBox("日志输出")
        layout = QVBoxLayout(group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_text.clear)
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

        return group

    def _migrate_script_bindings(self):
        """启动时清理脚本绑定脏数据：同模型多绑定、脚本名与模型错位。

        必须在 _load_saved_paths 之前跑——后者会按模型查绑定脚本，脏数据
        会让 current_script_name 取到别的模型的名字（跑 MiniCPM5 显示
        gemma-4-E4B）。清理后同步 pids.json 的键，运行中清单立刻用上正确名。
        """
        report = self.script_service.migrate_bindings()
        if report.get("corrected"):
            self._append_log(
                f"已按脚本内容校正 {report['corrected']} 条脚本的模型绑定路径")
        if report.get("removed"):
            self._append_log(
                f"已清理 {report['removed']} 条重复/错位的脚本绑定"
                f"（原 .bat 已备份到 {report.get('backup_dir', '')}，可人工找回）")
        moved = self.process_service.remap_runtime(report.get("remap") or {})
        if moved:
            self._append_log(f"已同步 {moved} 条运行中记录的脚本名")
        # 数据变更留痕到 data/logs/app.log（_append_log 只写界面面板，重启即丢）
        if report.get("corrected") or report.get("removed") or report.get("dropped"):
            detail = (f"脚本绑定清理: 校正 {report['corrected']} 条 model_path、"
                      f"合并 {report['removed']} 条重复绑定"
                      f"（备份 {report.get('backup_dir', '')}）、"
                      f"剔除 {report['dropped']} 条无 .bat 的过期条目")
            if moved:
                detail += f"；同步 {moved} 条运行中记录的脚本名"
            info(detail)

    def _on_local_models_deleted(self, deleted_paths):
        """"本地模型"页删除了文件 → 同步主控制页的模型选择。

        当前模型（或外挂视觉模型）正是被删文件时必须清空配置并**显式清空**
        两个只读路径框——`_load_saved_paths` 只在值非空时 setText，不清会
        残留旧文本、与下拉/表单显示打架（同 traps #42 的"两处显示不一致"）。
        未删到当前模型时只刷新下拉项，不重载表单（避免抹掉未保存的微调）。
        """
        gone = {normalize_path(p).lower() for p in (deleted_paths or []) if p}
        cleared = False
        for attr, edit in (("model_path", self.model_path_edit),
                           ("visual_model_path", self.visual_model_path_edit)):
            cur = normalize_path(getattr(self.settings, attr) or "").lower()
            if cur and cur in gone:
                setattr(self.settings, attr, "")
                edit.setText("")
                cleared = True
        if cleared:
            self.settings.save()
            self._append_log("当前模型文件已被删除，已清空模型选择")
        self._reload_model_combo()
        if cleared:
            self._on_model_changed()

    def _load_saved_paths(self):
        if self.settings.llamacpp_path:
            self.llamacpp_path_edit.setText(self.settings.llamacpp_path)
        if self.settings.model_path:
            self.model_path_edit.setText(self.settings.model_path)
        self.script_form.set_model_path(self.settings.model_path)
        if self.settings.visual_model_path:
            self.visual_model_path_edit.setText(self.settings.visual_model_path)
        if self.settings.model_dir:
            self.model_dir_edit.setText(self.settings.model_dir)
        self._reload_model_combo()
        self._on_model_changed()

    def _restore_service_state(self):
        # 多服务器恢复：对每个保存了 pid 的脚本探测存活并同步 UI
        alive = self.process_service.restore_all_pids()
        for name, pid in alive.items():
            self._append_log(f"检测到脚本 '{name}' 上次启动的服务仍在运行, PID={pid}")
            # 同步到"运行控制"运行中清单（否则恢复后显示"无模型运行"，与实际不符）
            self._on_model_started(name)
        if not alive:
            # 回退：旧版本 data/last_pid.pid（无脚本归属信息，行为与升级前一致）
            pid = self.process_service.restore_last_pid()
            if pid is not None:
                # restore_last_pid 返回非 None 即已验证进程存活（此处一次性验证，
                # 之后 GUI 线程只查缓存，不再同步跑 tasklist）
                self._legacy_running = True
                self._on_model_started(LEGACY_NAME)
                self._append_log(f"检测到上次启动的服务仍在运行, PID={pid}")
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _refresh_script_statuses(self):
        """立即刷新控制面板运行状态。

        旧实现在这里同步跑 tasklist（阻塞 ~0.6s）导致 GUI 卡顿；现改为
        向后台 StatusPoller 请求一轮探测，探测结果经 _apply_script_statuses 回传。
        """
        self._status_poller.request_poll()

    def _apply_script_statuses(self, runtime, alive_set):
        """GUI 线程：缓存后台探测结果并刷新控制面板运行状态（纯内存，不卡顿）。

        同时检测"恢复的服务已退出"（有运行时记录、PID 不在存活集、且无
        LogWorker 代理其生命周期）：清除运行时记录并同步运行中清单——
        LogWorker 的 finished 路径只覆盖本次会话内启动的服务，恢复的服务
        退出后若不在此处理，清单会一直挂着"运行中"。
        """
        self._last_runtime = runtime
        self._last_alive = alive_set
        worker_names = {getattr(w, "script_name", None) for w in self.log_workers}
        for name, entry in runtime.items():
            pid = (entry or {}).get("pid")
            if (name not in worker_names and isinstance(pid, int)
                    and pid > 0 and pid not in alive_set):
                self.process_service.clear_runtime(name)
                self._last_runtime.pop(name, None)
                self._on_model_stopped(name)
        self._sync_control_panel()

    def _sync_control_panel(self):
        """运行/结束按钮与状态标签反映选中脚本的运行状态（多服务器按脚本独立）。

        判断依据（避免 GUI 线程同步跑 tasklist）：
        1. 该脚本有活动 LogWorker（刚启动）→ 运行中；
        2. 后台 StatusPoller 最近一轮缓存的 runtime + alive_set；
        3. 无选中脚本时回退全局 legacy 状态（启动时验证过一次的 last_pid）。
        """
        name = self.current_script_name
        if name:
            running = any(
                getattr(w, "script_name", None) == name for w in self.log_workers
            )
            if not running:
                entry = self._last_runtime.get(name) or {}
                pid = entry.get("pid")
                running = (
                    isinstance(pid, int) and pid > 0 and pid in self._last_alive
                )
        else:
            running = self._legacy_running
        self.is_running = running
        self.run_btn.setEnabled(not running)
        # "全部结束"对准清单里所有运行中的模型：任一在跑就可点
        self.stop_btn.setEnabled(running or bool(self._run_rows))
        if running:
            self.status_label.setText("\u25cf 运行中")
            self.status_label.setStyleSheet(
                "color: orange; font-size: 12px; font-weight: bold;"
            )
        else:
            self.status_label.setText("\u25cf 就绪")
            self.status_label.setStyleSheet(
                "color: green; font-size: 12px; font-weight: bold;"
            )

    # ── "运行控制"运行中清单（多模型：每行 模型名 | 运行时长 | 结束）──────

    def _on_model_started(self, name):
        """某模型服务开始运行（启动/恢复时调用）：登记启动时间并加入清单。"""
        name = name or "default"
        self._run_times[name] = time.time()
        if name not in self._run_rows:
            self._add_run_row(name)

    def _on_model_stopped(self, name):
        """某模型服务停止（进程退出/手动结束时调用）：移出清单。"""
        name = name or "default"
        self._run_times.pop(name, None)
        self._remove_run_row(name)

    def _on_all_models_stopped(self):
        """全部 llama 进程被清理后整体重置（清理入口统一调用）。"""
        self._run_times.clear()
        for name in list(self._run_rows):
            self._remove_run_row(name)
        self.run_list_placeholder.setVisible(True)

    def _add_run_row(self, name):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold;")
        name_label.setWordWrap(True)
        h.addWidget(name_label)
        h.addStretch(1)
        uptime_label = QLabel("运行时长: --")
        uptime_label.setStyleSheet("color: #555; font-size: 12px;")
        h.addWidget(uptime_label)
        stop_btn = QPushButton("结束")
        stop_btn.setFixedWidth(48)
        stop_btn.setToolTip(f"结束模型 '{name}' 的进程")
        stop_btn.clicked.connect(
            lambda _=False, n=name: self._stop_single_model(n)
        )
        h.addWidget(stop_btn)
        self.run_list.addWidget(row)
        self._run_rows[name] = (uptime_label, stop_btn)
        self.run_list_placeholder.setVisible(False)

    def _remove_run_row(self, name):
        pair = self._run_rows.pop(name, None)
        if pair is None:
            return
        row = pair[0].parentWidget()
        self.run_list.removeWidget(row)
        row.deleteLater()
        if not self._run_rows:
            self.run_list_placeholder.setVisible(True)

    def _tick_uptime(self):
        """每秒刷新清单各行的运行时长（无行时仅空转，不阻塞）。"""
        now = time.time()
        for name, (uptime_label, _btn) in self._run_rows.items():
            start = self._run_times.get(name)
            if start is None:
                uptime_label.setText("运行时长: --")
            else:
                uptime_label.setText(f"运行时长: {format_uptime(now - start)}")

    def _stop_single_model(self, name):
        """结束运行中清单里的单个模型进程（行内"结束"按钮回调）。

        常规按脚本名 stop_by_pid；旧实例无脚本归属，走全局 PID 回退。
        """
        if name == LEGACY_NAME:
            stopped = self.process_service.stop_by_pid()
            self._legacy_running = False
        else:
            stopped = self.process_service.stop_by_pid(name)
            self._last_runtime.pop(name, None)
            self._server_urls.pop(name, None)
        if stopped:
            self._on_model_stopped(name)
            self._append_log(f"已结束模型 '{name}' 的进程")
        else:
            self._append_log(f"尝试结束 '{name}'，但可能未找到相关进程")
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _script_name_for(self, model_path):
        """模型路径 → 脚本名：已有绑定（路径比较大小写不敏感）复用其名，
        防止同一文件以不同大小写重新选择时派生出重复脚本；未绑定才派生。"""
        if not model_path:
            return ""
        existing = self.script_service.get_script_for_model(model_path)
        return existing.name if existing else ScriptEntry.derive_name(model_path)

    def _on_model_changed(self):
        """模型切换后的统一刷新：绑定脚本自动带出；无绑定则自动生成基础参数。

        脚本即模型，无独立脚本名。current_script_name 为模型推导出的内部 key。
        """
        self.current_script_name = self._script_name_for(self.settings.model_path)
        # 先刷新模型信息与 ctx 挡位（GGUF 上限），再回填表单——
        # set_preset 的整体重置会用到新模型的挡位默认值
        self.script_form.set_model_path(self.settings.model_path)
        entry = self.script_service.get_script_for_model(self.settings.model_path)
        if entry and entry.content:
            self.script_form.set_preset(parse_bat_params(entry.content))
            self._append_log(f"已载入该模型的绑定脚本参数")
        elif self.settings.model_path:
            # 懒人流：选中模型即自动生成基础参数，用户直接在表单微调
            self.script_form.set_preset(auto_generate_config(self.settings.model_path))
            self._append_log("已按该模型自动生成基础参数，可在表单微调后保存")
        self.script_form.set_alias_auto(self.settings.model_path)
        self.monitor_compact.set_focus_script(self.current_script_name)
        self._sync_control_panel()
        self._update_external_url()
        # 原文视图展开时统一刷新：绑定脚本原文 / 表单渲染 / 清空（无模型）三种来源
        if self.view_raw_btn.isChecked():
            self.script_editor.setPlainText(
                entry.content if entry and entry.content
                else self._form_to_content()
            )

    def _on_ts_ip_changed(self):
        """Tailscale IP 手动指定/修改后：写入配置、刷新 host 下拉与外网地址。"""
        v = self.ts_ip_edit.text().strip()
        self.settings.tailscale_ip = v
        self.settings.save()
        self.script_form.refresh_tailscale_ip()
        self._update_external_url()

    def _select_llamacpp_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 llama-server.exe", "", "Executable Files (*.exe)"
        )
        if path:
            if validate_llamacpp_file(path):
                self.llamacpp_path_edit.setText(path)
                self.settings.llamacpp_path = path
                self.settings.save()
                self._append_log(f"llama-server.exe 路径已设置: {path}")
            else:
                QMessageBox.warning(
                    self, "验证失败",
                    "请选择 llama-server.exe 文件。",
                )

    def _select_model_dir(self):
        """选择本地模型目录 → 递归扫描填充下拉。"""
        start = self.settings.model_dir or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "选择模型目录", start,
        )
        if not path:
            return
        self.model_dir_edit.setText(path)
        self.settings.model_dir = path
        self.settings.save()
        self._reload_model_combo()
        self._append_log(f"模型目录已设置: {path}")

    def _reload_model_combo(self):
        """扫描已保存的模型目录，填充下拉（显示文件名、存规范化完整路径）并同步当前选中项。

        比较统一走 `combo_index_for`（normalize_path + 大小写不敏感）：
        扫描结果来自 `os.path.join`（Windows 下是反斜线），配置里存的却是
        正斜线，直接 findData 永远匹配不上——重启后下拉停在第一项、而下方
        路径/表单仍是原模型，两边显示打架（traps #42）。
        """
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        files = scan_gguf_files(self.settings.model_dir)
        for f in files:
            # 下拉显示友好文件名，完整路径存 userData（避免超长路径撑爆下拉）
            self.model_combo.addItem(os.path.basename(f), normalize_path(f))
        saved = normalize_path(self.settings.model_path or "")
        idx = combo_index_for(
            [self.model_combo.itemData(i) for i in range(self.model_combo.count())],
            saved,
        )
        if idx < 0 and (files or saved):
            # 无当前模型（清空选择后）或它不在模型目录里（被删/移走/配置指向
            # 别处）：插入占位项并选中，保证下拉与下方路径一致，而不是默默
            # 停在第一项（traps #42）
            label = (f"（不在模型目录）{os.path.basename(saved)}" if saved
                     else "（未选择模型）")
            self.model_combo.insertItem(0, label, saved)
            idx = 0
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.model_combo.setEnabled(bool(files))
        self.model_combo.blockSignals(False)

    def _on_model_combo_selected(self, index):
        """下拉选中模型 → 写入当前模型路径。"""
        path = self.model_combo.itemData(index)
        if not path:
            return
        if validate_gguf(path):
            self.model_path_edit.setText(path)
            self.settings.model_path = path
            self.settings.save()
            self._auto_bind_visual_model(path)
            self._on_model_changed()
            self._append_log(f"模型已选择: {path}")
        else:
            QMessageBox.warning(self, "验证失败", "请选择 .gguf 格式的模型文件。")
            # 回滚下拉选中项到当前生效模型，避免显示与实际不一致
            self._reload_model_combo()

    def _auto_bind_visual_model(self, model_path):
        """按所选模型自动填充外挂视觉模型路径；无 mmproj 时清空，避免误绑到非视觉模型。"""
        vp = find_mmproj(model_path)
        self.visual_model_path_edit.setText(vp)
        if self.settings.visual_model_path != vp:
            self.settings.visual_model_path = vp
            self.settings.save()
        if vp:
            self._append_log(f"检测到视觉编码器: {vp}")

    def _select_visual_model_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择外挂视觉模型文件", "", "GGUF Files (*.gguf)"
        )
        if path:
            if validate_gguf(path):
                self.visual_model_path_edit.setText(path)
                self.settings.visual_model_path = path
                self.settings.save()
                self._append_log(f"外挂视觉模型已设置: {path}")
            else:
                QMessageBox.warning(self, "验证失败", "请选择 .gguf 格式的模型文件。")

    def _reset_script(self):
        """重置参数：清掉该模型已保存的脚本，按模型重新生成一版默认脚本。

        v1.19.2 合并原「一键生成」（回到默认参数）与「删除该模型脚本」
        （解绑清理）——懒人流下选中模型即自动生成参数，两个按钮实际只服务
        "回到默认状态"这一个诉求。重置后磁盘上仍是一条绑定（参数为默认值），
        脚本名沿用原绑定名以免运行中清单/记录错位。
        """
        model_path = self.settings.model_path
        if not model_path:
            QMessageBox.warning(self, "提示", "请先选择模型文件（.gguf）。")
            return
        if not self.settings.llamacpp_path:
            QMessageBox.warning(self, "提示", "请先选择 llama-server.exe。")
            return
        reply = QMessageBox.question(
            self, "确认重置参数",
            "将清掉当前模型已保存的脚本参数，重新生成一版默认脚本。\n"
            "（对模型文件无影响）\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 先刷新模型信息与 ctx 挡位，再回填（同 _on_model_changed 的顺序约束）
        self.script_form.set_model_path(model_path)
        self.script_form.set_preset(auto_generate_config(model_path))
        self.script_form.set_alias_auto(model_path)
        content = build_bat_content(
            os.path.dirname(self.settings.llamacpp_path), model_path,
            self.script_form.get_config(),
            visual_model_path=self.settings.visual_model_path,
        )
        if not content.strip():
            QMessageBox.warning(self, "提示", "请至少勾选一个参数。")
            return
        name = self.script_service.reset_script(model_path, content)
        self.current_script_name = name
        if self.view_raw_btn.isChecked():
            self.script_editor.setPlainText(content)
        self._append_log(f"已重置参数并重新生成默认脚本: {name}")
        self._sync_control_panel()

    def _save_script(self):
        """从表单生成 .bat 并绑定保存到当前模型（模型即脚本标识）。"""
        model_path = self.settings.model_path
        if not model_path:
            QMessageBox.warning(self, "提示", "请先选择模型文件（.gguf）。")
            return
        if not self.settings.llamacpp_path:
            QMessageBox.warning(self, "提示", "请先选择 llama-server.exe。")
            return
        exe_dir = os.path.dirname(self.settings.llamacpp_path)
        content = build_bat_content(
            exe_dir, model_path, self.script_form.get_config(),
            visual_model_path=self.settings.visual_model_path,
        )
        if not content.strip():
            QMessageBox.warning(self, "提示", "请至少勾选一个参数。")
            return
        name = self._script_name_for(model_path)
        bat_path = self.script_service.save_script(
            ScriptEntry(name=name, content=content, model_path=model_path))
        if not bat_path:
            return
        self.current_script_name = name
        if self.view_raw_btn.isChecked():
            self.script_editor.setPlainText(content)
        self._append_log(f"脚本已绑定保存到该模型: {bat_path}")

    def _run_script(self):
        if self.is_running:
            QMessageBox.warning(self, "提示", "该脚本已有进程在运行，请先结束。")
            return

        if not self.current_script_name:
            QMessageBox.warning(self, "提示", "请先选择模型文件（脚本绑定模型）。")
            return

        content = self._form_to_content()
        m_path = parse_model_path_from_bat(content) or self.settings.model_path
        if not content.strip():
            QMessageBox.warning(self, "提示", "参数表单为空，请先勾选参数。")
            return

        # 运行前自动保存：编辑器内容与磁盘不一致（或 .bat 不存在）时先落盘，
        # 保证"所见即所执行"——否则执行的是磁盘旧参数而界面显示新参数
        bat_path = self.script_service.get_script_path(self.current_script_name)
        disk_content = self.script_service.load_script_content(self.current_script_name)
        if content != disk_content or not disk_content:
            entry = ScriptEntry(
                name=self.current_script_name,
                content=content,
                model_path=m_path,
            )
            bat_path = self.script_service.save_script(entry)
            self._append_log("脚本内容已修改，运行前自动保存")

        # 端口预检：解析 .bat 的 --port（默认 8080）与 --host，bind 探测；
        # 被占时建议顺延到下一个可用端口（也可坚持用原端口或取消）
        port = extract_port(content, default=8080)
        host = extract_host(content, default="127.0.0.1")
        if self.process_service.is_port_in_use(port, host):
            next_port = self.process_service.find_free_port(port, host)
            if next_port:
                reply = QMessageBox.question(
                    self, "端口占用",
                    f"端口 {port} 已被占用。\n\n"
                    f"是：自动改用端口 {next_port} 并保存脚本\n"
                    f"否：仍用端口 {port} 启动\n"
                    f"取消：不启动",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    return
                if reply == QMessageBox.StandardButton.Yes:
                    content = re.sub(
                        rf"--port[=\s]+{port}\b", f"--port {next_port}", content
                    )
                    port = next_port
                    # 端口改写保存（脚本绑定模型，无置顶概念）
                    entry = ScriptEntry(
                        name=self.current_script_name,
                        content=content,
                        model_path=m_path,
                    )
                    self.script_service.save_script(entry)
                    # 端口改写同步回表单，保证表单与运行内容一致
                    self.script_form.set_preset(parse_bat_params(content))
                    if self.view_raw_btn.isChecked():
                        self.script_editor.setPlainText(content)
                    self._append_log(f"端口已自动改为 {next_port} 并保存")
            else:
                reply = QMessageBox.question(
                    self, "端口占用",
                    f"端口 {port} 已被占用（未找到空闲端口），仍要启动？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        self._server_urls.pop(self.current_script_name, None)
        worker = LogWorker(
            bat_path, self.process_service,
            self.current_script_name, port, host,
        )
        self.log_worker = worker
        self.log_workers.append(worker)
        worker.log_signal.connect(self._append_log)
        # 多服务器：带上脚本名，URL/外网地址按脚本归属记录，避免串扰
        worker.server_ready_signal.connect(
            lambda url, name=self.current_script_name: self._on_server_ready(name, url)
        )
        worker.tps_signal.connect(self.monitor_compact.update_tps)
        worker.finished.connect(lambda w=worker: self._on_run_finished(w))
        # 多服务器：退出时带上脚本名，只重置该服务的状态（不顶掉其他服务）
        worker.finished.connect(
            lambda w=worker: self._on_model_stopped(
                getattr(w, "script_name", "") or "default"
            )
        )
        worker.start()

        self.is_running = True
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("\u25cf 运行中")
        self.status_label.setStyleSheet(
            "color: orange; font-size: 12px; font-weight: bold;"
        )
        self._refresh_script_statuses()
        self._on_model_started(self.current_script_name or "default")

    def _on_server_ready(self, name, url):
        """服务就绪（按脚本归属）：记录该脚本的 URL 并刷新外网地址显示。

        保留原始 host（可能是 0.0.0.0），由消费方（聊天地址等）统一把
        0.0.0.0 解析为 Tailscale IP / 127.0.0.1，避免过早丢失"所有接口"信息。
        """
        self._server_urls[name] = url
        self._append_log(f"检测到服务已就绪: {url}")
        self._update_external_url()
        # 可选：服务就绪后自动打开聊天页（设置中开关，默认关闭）
        if self.settings.auto_open_chat:
            self._open_chat_window()

    def _update_external_url(self):
        """就绪后按选中脚本的 --host 显示 Tailscale 外网访问地址（含复制按钮）。

        仅当该脚本正在运行，且以 0.0.0.0（所有接口）或 Tailscale IP 监听、
        检测到 Tailscale 时显示；其余情况（仅本机/未运行）隐藏，避免误导。
        """
        name = self.current_script_name
        running = bool(name) and (
            name in self._server_urls
            or any(getattr(w, "script_name", None) == name for w in self.log_workers)
        )
        ts_ip = self._ts_ip_fast()
        if running and ts_ip:
            content = self._form_to_content()
            host = extract_host(content, default="127.0.0.1")
            if host in ("0.0.0.0", ts_ip):
                port = extract_port(content, default=8080)
                self._ts_url = f"http://{ts_ip}:{port}"
                self.ts_url_label.setText(self._ts_url)
                self.ts_url_label.setVisible(True)
                self.ts_copy_btn.setVisible(True)
                return
        self._hide_external_url()

    def _hide_external_url(self):
        self._ts_url = ""
        self.ts_url_label.setVisible(False)
        self.ts_copy_btn.setVisible(False)

    # ── Tailscale IP（后台探测，GUI 线程只读缓存）──────────────────

    def _ts_ip_fast(self):
        """GUI 线程快速取 Tailscale IP：手动指定即时生效（无子进程）；
        自动检测只读上次探测缓存，缓存缺失/超过 TTL 时触发后台探测——
        绝不在此同步跑 tailscale 子进程（timeout 3s×2，最坏阻塞 GUI ~6s）。
        """
        override = (self.settings.tailscale_ip or "").strip()
        if override and is_tailscale_ip(override):
            return override
        if time.monotonic() - self._ts_ip_probed_at >= TS_PROBE_TTL:
            self._request_ts_probe()
        return self._ts_ip

    def _request_ts_probe(self):
        """后台探测 Tailscale IP（探测中不叠加 worker）。"""
        if self._ts_probe_worker and self._ts_probe_worker.isRunning():
            return
        self._ts_ip_probed_at = time.monotonic()
        worker = TailscaleProbeWorker(parent=self)
        worker.ip_signal.connect(self._on_ts_ip_probed)
        worker.finished.connect(lambda w=worker: self._drop_ts_worker(w))
        self._ts_probe_worker = worker
        worker.start()

    def _drop_ts_worker(self, worker):
        """探测线程结束：移出引用并释放（防会话内累积、防悬空引用）。"""
        if self._ts_probe_worker is worker:
            self._ts_probe_worker = None
        worker.deleteLater()

    def _on_ts_ip_probed(self, ip):
        """探测完成：IP 变化才重算外网地址（未变则保持现状）。"""
        if ip != self._ts_ip:
            self._ts_ip = ip
            self._update_external_url()

    def _copy_ts_url(self):
        if self._ts_url:
            QApplication.clipboard().setText(self._ts_url)
            self._append_log(f"已复制外网访问地址: {self._ts_url}")

    def _on_run_finished(self, worker=None):
        # 多服务器：某脚本的进程退出 → 清除该脚本运行时记录，刷新状态
        if worker is not None:
            name = getattr(worker, "script_name", "")
            if name:
                self.process_service.clear_runtime(name)
                self._server_urls.pop(name, None)
                # 同步清缓存，避免等下一轮轮询期间状态列仍显示"运行中"
                self._last_runtime.pop(name, None)
            self.log_workers = [w for w in self.log_workers if w is not worker]
        # 其他服务可能仍在运行：按当前选中脚本重算外网地址（而非无条件隐藏）
        self._update_external_url()
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _open_chat_window(self):
        if not self._bridge_port:
            QMessageBox.warning(self, "提示", "聊天桥服务未启动。")
            return

        # 打开前重新确保静态文件已部署（幂等，可修复 data/webui 缺失）
        ensure_webui()
        webui_file = os.path.abspath(os.path.join("data", "webui", "chat.html"))
        if not os.path.isfile(webui_file):
            self._append_log(f"聊天页面文件缺失: {webui_file}")
            error(f"打开聊天页面失败: {webui_file} 不存在（CWD={os.getcwd()}）")
            QMessageBox.warning(
                self, "无法打开聊天页面",
                f"找不到聊天页面文件:\n{webui_file}\n\n"
                f"当前工作目录: {os.getcwd()}\n\n"
                "请确认程序从项目根目录（或 PyInstaller 产物所在目录）启动，\n"
                "且 ui/chat_webui 目录完整后重试。",
            )
            return

        url = f"http://127.0.0.1:{self._bridge_port}/chat.html"
        webbrowser.open(url)
        self._append_log(f"已打开聊天页面: {url}")

    def _stop_script(self):
        """顶部"全部结束"：逐个结束清单里全部运行中的模型（多服务器）。

        每行已有各自的"结束"按钮，此按钮承担"一键全停"；逐个结束使单个
        失败不影响其余。旧实例（无脚本归属）走全局 PID 回退。只结束本软件
        跟踪的进程——未跟踪的实例仍归下方"清理全部llama进程"（带确认）。

        清单尚未覆盖的运行时记录（跨会话恢复的服务）也要结束：以 pids.json
        为准并集，避免按钮"看不到"的实例残留占用 8080 端口。
        """
        names, legacy = stop_all_targets(
            self._run_rows, self.process_service.load_runtime(),
            self._legacy_running)

        stopped = []
        for name in names:
            if self.process_service.stop_by_pid(name):
                stopped.append(name)
            # 同步清缓存（不等下一轮轮询），按钮/状态列立即反映
            self._last_runtime.pop(name, None)
            self._server_urls.pop(name, None)
            self._on_model_stopped(name)
        if legacy:
            if self.process_service.stop_by_pid():
                stopped.append(LEGACY_NAME)
            self._legacy_running = False
            self._on_model_stopped(LEGACY_NAME)

        if stopped:
            self._append_log(f"已结束全部模型（{len(stopped)} 个）: "
                             + "、".join(stopped))
        else:
            self._append_log("没有找到可结束的运行中进程")

        self._sync_control_panel()
        self._refresh_script_statuses()

    def _cleanup_all_processes(self):
        """显式清理全部 llama 进程（含手动启动的实例），需确认。

        tasklist+taskkill 循环（多实例时秒级）放后台 KillAllLlamaWorker 执行，
        不阻塞 GUI；完成后回 GUI 线程统一重置状态。
        """
        reply = QMessageBox.question(
            self, "确认清理全部 llama 进程",
            "此操作将结束本机所有 llama-server.exe / main.exe 进程，"
            "包括未通过本软件启动的实例。\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.cleanup_all_btn.setEnabled(False)
        self.cleanup_all_btn.setText("清理中...")
        self._killall_worker = KillAllLlamaWorker(self.process_service, parent=self)
        self._killall_worker.killed_signal.connect(self._on_cleanup_all_done)
        self._killall_worker.start()

    def _on_cleanup_all_done(self, killed):
        """清理完成（GUI 线程）：记录日志并重置全部运行状态。"""
        self.cleanup_all_btn.setEnabled(True)
        self.cleanup_all_btn.setText("清理全部llama进程")
        if killed:
            detail = ", ".join(f"{k['name']}(PID={k['pid']})" for k in killed)
            self._append_log(f"已清理全部 llama 进程: {detail}")
            info(f"用户手动清理全部 llama 进程: {detail}")
        else:
            self._append_log("清理完成，未发现相关进程")
            info("用户手动清理全部 llama 进程: 未发现相关进程")

        # stop_by_name 已清空 pids.json 全部条目；各 LogWorker 检测到进程退出后自行结束
        self._last_runtime = {}
        self._last_alive = set()
        self._legacy_running = False
        self._server_urls.clear()
        # 恢复的服务没有 LogWorker 代理 finished，这里整体重置运行中清单
        self._on_all_models_stopped()
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _start_bridge(self):
        try:
            self._bridge_server, self._bridge_port = start_bridge(
                llm_url_provider=self._current_llm_url
            )
            self._append_log(f"聊天桥服务已启动，端口: {self._bridge_port}")
            webui_file = os.path.abspath(os.path.join("data", "webui", "chat.html"))
            if os.path.isfile(webui_file):
                info(f"聊天页面文件就绪: {webui_file}")
            else:
                error(f"聊天页面文件缺失（聊天页将返回 404）: {webui_file}（CWD={os.getcwd()}）")
        except Exception as e:
            self._bridge_server = None
            self._bridge_port = None
            self._append_log(f"聊天桥服务启动失败: {e}")

    def _current_llm_url(self):
        """返回当前运行中模型的 API 地址（供聊天页自动填充）。

        委托模块级 _pick_llm_url：选中脚本的记录优先，回退任意运行中记录
        （脚本绑定模型后用户常切到其他模型调参，此时聊天页仍应指向
        正在运行的那个）。host 解析读手动指定/探测缓存，不跑子进程——
        该方法由桥服务 HTTP 线程调用，须线程安全（traps #34）。
        """
        return _pick_llm_url(
            self.current_script_name,
            self._server_urls,
            self.process_service.load_runtime(),
            (self.settings.tailscale_ip or "").strip(),
            self._ts_ip,
        )

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        self._log_append_count += 1
        if self._log_append_count % LOG_PANEL_TRIM_EVERY == 0:
            self._trim_log_panel()

    def _trim_log_panel(self):
        """删除日志面板头部超出上限的块，仅保留最近 2000 行。

        纯文本追加（QTextEdit.append），按 document 块数裁剪：选中头部多余块
        后整体删除，其余内容不受影响。裁剪不操作控件光标/滚动条（不做
        moveCursor(End)）：那样会把滚动到上方读历史的用户强制拉回底部。
        裁剪后视口行为与裁剪前 append 一致——用户位于底部时块数减少后
        滚动条自动钳制回底部，新日志继续跟随；用户读历史时视口原地不动。
        """
        doc = self.log_text.document()
        excess = doc.blockCount() - LOG_PANEL_MAX_BLOCKS
        if excess <= 0:
            return
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor,
            excess,
        )
        cursor.removeSelectedText()


def main():
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtGui import QFont
    from PyQt6.QtCore import QLockFile

    app = QApplication(sys.argv)
    app.setFont(QFont())

    # 单实例锁：防止多个 GUI 并发运行（旧实例占用桥端口、
    # Windows SO_REUSEADDR 重叠绑定导致"新代码不生效"假象，见 traps #7）。
    # 托盘"重启"拉起的实例跳过此锁：重启是用户显式意图，旧实例很快退出，
    # 锁若被残留/僵尸实例占住会误拦重启。普通首次启动仍受单实例保护。
    if os.environ.get("LLAMACPP_RESTARTING") != "1":
        os.makedirs("data", exist_ok=True)
        lock = QLockFile(os.path.join("data", "app.lock"))
        if not lock.tryLock():
            # 普通手动重复启动：短暂重试后仍占用则提示，尽快返回避免假死
            locked = False
            for _ in range(3):
                time.sleep(0.15)
                if lock.tryLock():
                    locked = True
                    break
            if not locked:
                QMessageBox.warning(
                    None, "已在运行",
                    "LlamaCPP GUI 已在运行。\n请使用已有窗口（或从系统托盘唤起），"
                    "或先退出旧实例后再启动。",
                )
                return

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
