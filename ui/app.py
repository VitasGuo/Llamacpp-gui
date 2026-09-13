"""llama.cpp GUI 主窗口。"""
import sys
import os
import re
import subprocess
import webbrowser
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QMessageBox,
    QSplitter, QTabWidget, QFileDialog, QInputDialog, QDialog,
    QSystemTrayIcon, QMenu, QComboBox, QHeaderView, QToolButton,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor, QAction, QBrush, QColor, QPixmap, QPainter, QFont, QIcon

from config.config import Settings
from utils.validator import validate_llamacpp_file, validate_gguf
from utils.logger import error, info
from service.script_service import ScriptService
from service.script_builder import build_bat_content, extract_port, extract_host, find_mmproj
from service.process_service import ProcessService
from service.tailscale import get_tailscale_ipv4
from service.monitor_service import MonitorService
from service.path_service import ensure_webui
from service import autostart_service
from service.model_scanner import scan_gguf_files
from chat import start_bridge
from model.script import ScriptEntry
from ui.model_tab import ModelTab
from ui.monitor_tab import MonitorTab, CompactMonitor
from ui.update_tab import UpdateTab
from ui.dialogs.new_script_dialog import NewScriptDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.workers.log_worker import LogWorker
from ui.workers.status_worker import StatusPoller
from ui.workers.update_workers import CheckUpdateWorker, CheckAppUpdateWorker

# 日志面板行数上限：保留最近 N 个块（行）；每追加 M 条检查一次，避免刷屏时频繁裁剪
LOG_PANEL_MAX_BLOCKS = 2000
LOG_PANEL_TRIM_EVERY = 50


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
        self._script_entries = []
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

        self.setWindowTitle("llama.cpp GUI Client")
        self.resize(960, 700)

        self.monitor_service = MonitorService(self.process_service)
        self.monitor_tab = MonitorTab(self.monitor_service)
        # 主控制页嵌入的压缩版监控（与日志并排）：同一数据源，加载模型时边看日志边看负载
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
        # 通知所有 LogWorker 退出；readline 可能阻塞，短暂等待后不再等
        # （保留引用防止 QThread 在运行中被销毁导致崩溃，进程退出时线程终止）
        for w in self.log_workers:
            w.stop()
        for w in self.log_workers:
            w.wait(300)
        if self._bridge_server:
            self._bridge_server.shutdown()
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

        单实例锁由 main() 的短暂重试兜底：旧实例退出释放锁期间，新实例能拿到锁。
        """
        self._force_quit = True
        cmd = [sys.executable] + list(sys.argv)
        try:
            subprocess.Popen(cmd, cwd=os.getcwd())
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

        # 主控制标签
        control_widget = QWidget()
        control_layout = QVBoxLayout(control_widget)

        control_layout.addWidget(self._create_path_panel())
        # 脚本面板占更大空间（脚本多时列表能显示更多行）
        control_layout.addWidget(self._create_script_panel(), stretch=2)
        control_layout.addWidget(self._create_control_panel())

        # 日志与压缩系统监控并排：加载模型时边看日志边看负载，免切标签
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._create_log_panel(), stretch=3)
        bottom_row.addWidget(self.monitor_compact, stretch=1)
        control_layout.addLayout(bottom_row, stretch=1)

        tabs.addTab(control_widget, "主控制")

        # 模型搜索与下载标签
        self.model_tab = ModelTab()
        tabs.addTab(self.model_tab, "模型搜索与下载")

        # 性能监控标签
        tabs.addTab(self.monitor_tab, "性能监控")

        # 版本管理标签（llama.cpp 检测更新/下载安装/切换）
        self.update_tab = UpdateTab()
        self.update_tab.version_switched.connect(self._on_version_switched)
        # 版本切换后用户选"立即重启" → 由主窗口执行应用重启
        self.update_tab.restart_requested.connect(self._restart_app)
        tabs.addTab(self.update_tab, "版本管理")

        main_layout.addWidget(tabs)

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

        row2b = QHBoxLayout()
        row2b.addWidget(QLabel("模型文件 (.gguf):"))
        self.model_path_edit = QLineEdit()
        self.model_path_edit.setReadOnly(True)
        row2b.addWidget(self.model_path_edit)
        self.model_combo = QComboBox()
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_selected)
        row2b.addWidget(self.model_combo)
        browse_btn2 = QPushButton("浏览...")
        browse_btn2.clicked.connect(self._select_model_file)
        row2b.addWidget(browse_btn2)
        layout.addLayout(row2b)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("外挂视觉模型 (.gguf):"))
        self.visual_model_path_edit = QLineEdit()
        self.visual_model_path_edit.setReadOnly(True)
        row3.addWidget(self.visual_model_path_edit)
        browse_btn3 = QPushButton("选择...")
        browse_btn3.clicked.connect(self._select_visual_model_file)
        row3.addWidget(browse_btn3)
        layout.addLayout(row3)

        return group

    def _create_script_panel(self):
        group = QGroupBox("启动脚本")
        layout = QHBoxLayout(group)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_layout = QVBoxLayout()
        # 三列：脚本名（自动拉伸）+ 状态（按内容）+ 置顶按钮（固定宽度）
        self.script_list = QTreeWidget()
        self.script_list.setColumnCount(3)
        self.script_list.setHeaderLabels(["脚本", "状态", ""])
        header = self.script_list.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.script_list.setColumnWidth(2, 68)
        self.script_list.setRootIsDecorated(False)
        self.script_list.setUniformRowHeights(True)
        self.script_list.currentItemChanged.connect(self._on_script_selected)
        # 双击脚本 = 直接运行（常用操作的快捷路径）
        self.script_list.itemDoubleClicked.connect(
            lambda item: self._run_script()
        )
        # 右键菜单：置顶/取消置顶（补充路径，主入口为行内按钮）
        self.script_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.script_list.customContextMenuRequested.connect(self._show_script_context_menu)
        left_layout.addWidget(self.script_list)

        btn_layout = QHBoxLayout()
        new_btn = QPushButton("新建")
        new_btn.clicked.connect(self._new_script)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_script)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._delete_script)
        btn_layout.addWidget(new_btn)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(delete_btn)
        left_layout.addLayout(btn_layout)

        left_panel = QWidget()
        left_panel.setLayout(left_layout)

        self.script_editor = QTextEdit()
        self.script_editor.setPlaceholderText("在此输入启动脚本内容。")

        splitter.addWidget(left_panel)
        splitter.addWidget(self.script_editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)
        return group

    def _create_control_panel(self):
        group = QGroupBox("控制面板")
        root = QVBoxLayout(group)
        layout = QHBoxLayout()
        root.addLayout(layout)

        self.run_btn = QPushButton("运行")
        self.run_btn.clicked.connect(self._run_script)
        layout.addWidget(self.run_btn)

        self.stop_btn = QPushButton("结束")
        self.stop_btn.clicked.connect(self._stop_script)
        layout.addWidget(self.stop_btn)

        self.status_label = QLabel("\u25cf 就绪")
        self.status_label.setStyleSheet(
            "color: green; font-size: 12px; font-weight: bold;"
        )
        layout.addWidget(self.status_label)

        self.chat_btn = QPushButton("聊天窗口")
        self.chat_btn.setEnabled(True)
        self.chat_btn.clicked.connect(self._open_chat_window)
        layout.addWidget(self.chat_btn)

        layout.addStretch()

        self.check_update_btn = QPushButton("检查llama.cpp更新")
        self.check_update_btn.clicked.connect(self._check_update)
        layout.addWidget(self.check_update_btn)

        self.update_date_label = QLabel("")
        self.update_date_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.update_date_label)

        self.check_app_update_btn = QPushButton("检查软件更新")
        self.check_app_update_btn.clicked.connect(self._check_app_update)
        layout.addWidget(self.check_app_update_btn)

        self.app_update_date_label = QLabel("")
        self.app_update_date_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.app_update_date_label)

        # 显式清理入口：不常用、放右侧不突出位置，避免误触
        self.cleanup_all_btn = QPushButton("清理全部llama进程")
        self.cleanup_all_btn.setToolTip(
            "结束本机所有 llama-server.exe / main.exe 进程"
            "（包括未通过本软件启动的实例），点击需确认。"
        )
        self.cleanup_all_btn.clicked.connect(self._cleanup_all_processes)
        layout.addWidget(self.cleanup_all_btn)

        # 外网访问地址行（Tailscale）：服务以 0.0.0.0 或 Tailscale IP 监听时显示
        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("外网访问:"))
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

        return group

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

    def _load_saved_paths(self):
        if self.settings.llamacpp_path:
            self.llamacpp_path_edit.setText(self.settings.llamacpp_path)
        if self.settings.model_path:
            self.model_path_edit.setText(self.settings.model_path)
        if self.settings.visual_model_path:
            self.visual_model_path_edit.setText(self.settings.visual_model_path)
        if self.settings.model_dir:
            self.model_dir_edit.setText(self.settings.model_dir)
        self._reload_model_combo()
        self._refresh_script_list()

    def _restore_service_state(self):
        # 多服务器恢复：对每个保存了 pid 的脚本探测存活并同步 UI
        alive = self.process_service.restore_all_pids()
        for name, pid in alive.items():
            self._append_log(f"检测到脚本 '{name}' 上次启动的服务仍在运行, PID={pid}")
            # 同步到监控页状态面板（否则恢复后显示"未运行"，与实际不符）
            self.monitor_tab.on_server_started(name)
            self.monitor_compact.on_server_started(name)
        if not alive:
            # 回退：旧版本 data/last_pid.pid（无脚本归属信息，行为与升级前一致）
            pid = self.process_service.restore_last_pid()
            if pid is not None:
                # restore_last_pid 返回非 None 即已验证进程存活（此处一次性验证，
                # 之后 GUI 线程只查缓存，不再同步跑 tasklist）
                self._legacy_running = True
                self._append_log(f"检测到上次启动的服务仍在运行, PID={pid}")
        self._refresh_script_list()
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _runtime_status_text(self, name, runtime):
        entry = runtime.get(name)
        if entry and isinstance(entry.get("pid"), int):
            return f"● 运行中 PID={entry['pid']}"
        return "已停止"

    def _refresh_script_list(self):
        self.script_list.clear()
        self.script_list.setColumnCount(3)
        scripts = self.script_service.load_scripts()
        # 置顶脚本排前面（稳定排序：置顶内保持原相对顺序，其余不变）
        scripts.sort(key=lambda s: not s.pinned)
        self._script_entries = scripts
        runtime = self.process_service.load_runtime()
        for script in scripts:
            # 合并运行时字段（多服务器：pid/started_at/port 来自 pids.json）
            rt = runtime.get(script.name) or {}
            script.pid = rt.get("pid")
            script.started_at = rt.get("started_at", "")
            script.port = rt.get("port")
            item = QTreeWidgetItem()
            item.setText(0, script.name)
            item.setText(1, self._runtime_status_text(script.name, runtime))
            self.script_list.addTopLevelItem(item)
            self._set_pin_button(item, script)

    def _set_pin_button(self, item, script):
        """在脚本行内置顶状态按钮：已置顶=橙色"置顶"，未置顶=灰色"未置顶"（点击切换）。

        用文字而非 emoji：QSS color 无法给彩色 emoji 字形着色，emoji 版
        置顶/非置顶视觉无差别（用户反馈）。
        """
        btn = QToolButton()
        btn.setFixedSize(64, 24)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if script.pinned:
            btn.setText("置顶")
            btn.setToolTip("已置顶，点击取消置顶")
            btn.setStyleSheet("QToolButton { color: #e67e22; font-weight: bold; }")
        else:
            btn.setText("未置顶")
            btn.setToolTip("置顶：点击后排在列表前面")
            btn.setStyleSheet("QToolButton { color: #909090; }")
        btn.clicked.connect(lambda _, n=script.name: self._toggle_pin(n))
        self.script_list.setItemWidget(item, 2, btn)

    def _show_script_context_menu(self, pos):
        """脚本列表右键菜单：置顶/取消置顶。"""
        item = self.script_list.itemAt(pos)
        if not item:
            return
        self.script_list.setCurrentItem(item)
        name = item.text(0)
        entry = next((e for e in self._script_entries if e.name == name), None)
        if entry is None:
            return
        menu = QMenu(self)
        action = QAction("取消置顶" if entry.pinned else "置顶", self)
        action.triggered.connect(lambda: self._toggle_pin(name))
        menu.addAction(action)
        menu.exec(self.script_list.viewport().mapToGlobal(pos))

    def _toggle_pin(self, name):
        """切换脚本置顶状态并持久化（写入 scripts.json），随后重排列表并保持选中。

        用磁盘上的脚本内容保存：编辑器未保存的修改不回退（save_script 会写 content）。
        """
        entry = next((e for e in self._script_entries if e.name == name), None)
        if entry is None:
            return
        entry.pinned = not entry.pinned
        entry.content = self.script_service.load_script_content(name) or entry.content
        entry.model_path = self.settings.model_path
        self.script_service.save_script(entry)
        self._refresh_script_list()
        items = self.script_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.script_list.setCurrentItem(items[0])
        self._append_log(f"脚本已{'置顶' if entry.pinned else '取消置顶'}: {name}")

    def _refresh_script_statuses(self):
        """立即刷新脚本列表状态列与控制面板。

        旧实现在这里同步跑 tasklist（阻塞 ~0.6s）导致 GUI 卡顿；现改为
        向后台 StatusPoller 请求一轮探测，探测结果经 _apply_script_statuses 回传。
        """
        self._status_poller.request_poll()

    def _apply_script_statuses(self, runtime, alive_set):
        """GUI 线程：把后台探测到的存活集合渲染到列表与面板（纯内存操作，不卡顿）。"""
        # 缓存本轮结果：_sync_control_panel 判断运行状态只查缓存，
        # 不再经 process_service.is_running 同步跑 tasklist 阻塞 GUI 线程
        self._last_runtime = runtime
        self._last_alive = alive_set
        for i in range(self.script_list.topLevelItemCount()):
            item = self.script_list.topLevelItem(i)
            name = item.text(0)
            entry = runtime.get(name) or {}
            pid = entry.get("pid")
            alive = isinstance(pid, int) and pid > 0 and pid in alive_set
            if alive:
                item.setText(1, f"● 运行中 PID={entry.get('pid')}")
                item.setForeground(0, QBrush(QColor("#e67e22")))
                item.setForeground(1, QBrush(QColor("#e67e22")))
            else:
                item.setText(1, "已停止")
                item.setForeground(0, QBrush(QColor("#999999")))
                item.setForeground(1, QBrush(QColor("#999999")))
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
        self.stop_btn.setEnabled(running)
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

    def _on_script_selected(self, current, previous):
        if current:
            name = current.text(0)
            self.current_script_name = name
            content = self.script_service.load_script_content(name)
            self.script_editor.setPlainText(content)
            self.monitor_tab.set_focus_script(name)
            self.monitor_compact.set_focus_script(name)
            self._sync_control_panel()
            # 外网地址/聊天地址按选中脚本重算（多服务器下各脚本 host/port 不同）
            self._update_external_url()
        else:
            # 取消选中 → 控制面板回退全局状态（旧版本启动/last_pid 恢复的进程）
            self.current_script_name = ""
            self.monitor_tab.set_focus_script("")
            self.monitor_compact.set_focus_script("")
            self._sync_control_panel()
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

    def _select_model_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 GGUF 模型文件", "", "GGUF Files (*.gguf)"
        )
        if path:
            if validate_gguf(path):
                self.model_path_edit.setText(path)
                self.settings.model_path = path
                self.settings.save()
                self._append_log(f"模型文件已设置: {path}")
                self._auto_bind_visual_model(path)
            else:
                QMessageBox.warning(self, "验证失败", "请选择 .gguf 格式的模型文件。")

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
        """扫描已保存的模型目录，填充下拉（显示文件名、存完整路径）并同步当前选中项。"""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        files = scan_gguf_files(self.settings.model_dir)
        for f in files:
            # 下拉显示友好文件名，完整路径存 userData（避免超长路径撑爆下拉）
            self.model_combo.addItem(os.path.basename(f), f)
        self.model_combo.setEnabled(bool(files))
        # 当前已选模型若存在于列表则定位到对应项（按完整路径精确匹配）
        idx = self.model_combo.findData(self.settings.model_path)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
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
            self._append_log(f"模型已选择: {path}")
            self._auto_bind_visual_model(path)
        else:
            QMessageBox.warning(self, "验证失败", "请选择 .gguf 格式的模型文件。")

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

    def _new_script(self):
        self.current_script_name = ""
        self.script_list.clearSelection()
        llamacpp_path = self.settings.llamacpp_path
        model_path = self.settings.model_path

        if not llamacpp_path or not model_path:
            QMessageBox.warning(
                self, "提示",
                "请先设置 llama-server.exe 和模型文件的路径。",
            )
            self.script_editor.clear()
            self.script_editor.setPlaceholderText(
                "请先在上方路径配置中选择 llama-server.exe 和 .gguf 模型文件。"
            )
            return

        # 自动命名：默认取所选模型文件名（去扩展名），对话框内可修改
        default_name = os.path.splitext(os.path.basename(model_path))[0]
        exe_dir = os.path.dirname(llamacpp_path)
        dialog = NewScriptDialog(
            self,
            visual_model_path=self.settings.visual_model_path,
            default_name=default_name,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name:
                QMessageBox.warning(self, "提示", "脚本名称不能为空。")
                return
            config = dialog.get_config()
            bat_content = build_bat_content(
                exe_dir, model_path, config,
                visual_model_path=self.settings.visual_model_path,
            )
            self.script_editor.setPlainText(bat_content)
            self.current_script_name = name
            # 新建脚本默认置顶（用户可右键取消）
            entry = ScriptEntry(
                name=name, content=bat_content, model_path=model_path, pinned=True
            )
            bat_path = self.script_service.save_script(entry)
            self._refresh_script_list()
            item = self.script_list.findItems(name, Qt.MatchFlag.MatchExactly)
            if item:
                self.script_list.setCurrentItem(item[0])
            self._append_log(f"脚本已新建并保存: {bat_path}")

    def _save_script(self):
        if self.current_script_name:
            content = self.script_editor.toPlainText()
            if not content.strip():
                QMessageBox.warning(self, "提示", "脚本内容为空，无法保存。")
                return
            # 保存既有脚本时保留其置顶状态
            pinned = next(
                (e.pinned for e in self._script_entries if e.name == self.current_script_name),
                False,
            )
            entry = ScriptEntry(
                name=self.current_script_name,
                content=content,
                model_path=self.settings.model_path,
                pinned=pinned,
            )
            bat_path = self.script_service.save_script(entry)
            if bat_path:
                self._append_log(f"脚本已保存: {bat_path}")
        else:
            # 保存新脚本：预填所选模型名作为默认名称（可改）；新建默认置顶
            default_name = os.path.splitext(os.path.basename(self.settings.model_path))[0]
            name, ok = QInputDialog.getText(
                self, "保存脚本", "请输入脚本名称:", text=default_name,
            )
            if ok and name:
                content = self.script_editor.toPlainText()
                entry = ScriptEntry(
                    name=name, content=content,
                    model_path=self.settings.model_path, pinned=True,
                )
                bat_path = self.script_service.save_script(entry)
                if bat_path:
                    self._refresh_script_list()
                    item = self.script_list.findItems(name, Qt.MatchFlag.MatchExactly)
                    if item:
                        self.script_list.setCurrentItem(item[0])
                    self._append_log(f"脚本已保存: {bat_path}")

    def _delete_script(self):
        current = self.script_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选择一个脚本。")
            return
        name = current.text(0)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除脚本 '{name}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.script_service.delete_script(name)
            self._refresh_script_list()
            self.script_editor.clear()
            self.current_script_name = ""
            self._append_log(f"脚本已删除: {name}")

    def _run_script(self):
        if self.is_running:
            QMessageBox.warning(self, "提示", "该脚本已有进程在运行，请先结束。")
            return

        if not self.current_script_name:
            if self.script_list.topLevelItemCount() > 0:
                self.script_list.setCurrentItem(self.script_list.topLevelItem(0))
                if not self.current_script_name:
                    return
            else:
                QMessageBox.warning(self, "提示", "没有可运行的脚本，请先新建脚本。")
                return

        content = self.script_editor.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "提示", "脚本内容为空，请先编写或生成脚本。")
            return

        # 运行前自动保存：编辑器内容与磁盘不一致（或 .bat 不存在）时先落盘，
        # 保证"所见即所执行"——否则执行的是磁盘旧参数而界面显示新参数
        bat_path = self.script_service.get_script_path(self.current_script_name)
        disk_content = self.script_service.load_script_content(self.current_script_name)
        if content != disk_content:
            # 保留置顶状态：upsert 会整条目覆盖，漏传 pinned 会把置顶静默取消
            pinned = next(
                (e.pinned for e in self._script_entries if e.name == self.current_script_name),
                False,
            )
            entry = ScriptEntry(
                name=self.current_script_name,
                content=content,
                model_path=self.settings.model_path,
                pinned=pinned,
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
                    # 端口改写保存同样保留置顶状态（漏传会静默取消置顶）
                    pinned = next(
                        (e.pinned for e in self._script_entries
                         if e.name == self.current_script_name),
                        False,
                    )
                    entry = ScriptEntry(
                        name=self.current_script_name,
                        content=content,
                        model_path=self.settings.model_path,
                        pinned=pinned,
                    )
                    self.script_service.save_script(entry)
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
        worker.tps_signal.connect(self.monitor_tab.update_tps)
        worker.tps_signal.connect(self.monitor_compact.update_tps)
        worker.finished.connect(lambda w=worker: self._on_run_finished(w))
        # 多服务器：退出时带上脚本名，只重置该服务的状态（不顶掉其他服务）
        worker.finished.connect(
            lambda w=worker: self.monitor_tab.on_server_stopped(
                getattr(w, "script_name", "") or "default"
            )
        )
        worker.finished.connect(
            lambda w=worker: self.monitor_compact.on_server_stopped(
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
        self.monitor_tab.on_server_started(self.current_script_name or "default")
        self.monitor_compact.on_server_started(self.current_script_name or "default")

    def _on_server_ready(self, name, url):
        """服务就绪（按脚本归属）：记录该脚本的 URL 并刷新外网地址显示。"""
        url = url.replace("0.0.0.0", "127.0.0.1")
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
        ts_ip = get_tailscale_ipv4()
        if running and ts_ip:
            content = self.script_editor.toPlainText()
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
        if not self.is_running:
            return

        # 常规停止只按 PID（本软件跟踪的进程）；"清理全部"走 _cleanup_all_processes
        # 多服务器：停止选中脚本自己的进程；无脚本归属时走全局 current_pid（兼容旧版本）
        name = self.current_script_name
        if name and self.process_service.load_runtime().get(name, {}).get("pid"):
            stopped = self.process_service.stop_by_pid(name)
            # 同步清缓存（不等下一轮轮询），按钮/状态列立即反映
            self._last_runtime.pop(name, None)
            self._server_urls.pop(name, None)
        else:
            stopped = self.process_service.stop_by_pid()
            self._legacy_running = False
        if stopped:
            self._append_log("进程已终止")
        else:
            self._append_log("尝试终止进程，但可能未找到相关进程")

        self._sync_control_panel()
        self._refresh_script_statuses()

    def _cleanup_all_processes(self):
        """显式清理全部 llama 进程（含手动启动的实例），需确认。"""
        reply = QMessageBox.question(
            self, "确认清理全部 llama 进程",
            "此操作将结束本机所有 llama-server.exe / main.exe 进程，"
            "包括未通过本软件启动的实例。\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        killed = self.process_service.stop_by_name()
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

        优先顺序：选中脚本的就绪 URL → 其他就绪 URL（最近一次）→
        pids.json 中记录的脚本端口 + host 推导地址（--host 为 Tailscale IP
        时用该 IP，保证本地浏览器可达）。
        """
        if self.current_script_name:
            url = self._server_urls.get(self.current_script_name)
            if url:
                return url
        elif self._server_urls:
            return next(iter(self._server_urls.values()))
        runtime = self.process_service.load_runtime()
        if self.current_script_name and self.current_script_name in runtime:
            runtime = {self.current_script_name: runtime[self.current_script_name]}
        for entry in runtime.values():
            port = entry.get("port")
            if isinstance(port, int) and port > 0:
                host = entry.get("host") or "127.0.0.1"
                if host in ("0.0.0.0", ""):
                    host = "127.0.0.1"
                return f"http://{host}:{port}"
        return ""

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _check_update(self):
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("检查中...")
        self.update_date_label.setText("")
        self.worker = CheckUpdateWorker()
        self.worker.result_signal.connect(self._on_update_result)
        self.worker.start()

    def _on_update_result(self, date_str, error_msg):
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("检查llama.cpp更新")
        if date_str:
            self.update_date_label.setText(f"最后更新: {date_str}")
            self._append_log(f"llama.cpp 最新版本发布日期: {date_str}")
        else:
            self.update_date_label.setText("获取失败")
            reason = f"检查llama.cpp更新失败: {error_msg}" if error_msg else "检查llama.cpp更新失败，请检查网络连接"
            self._append_log(reason)

    def _check_app_update(self):
        self.check_app_update_btn.setEnabled(False)
        self.check_app_update_btn.setText("检查中...")
        self.app_update_date_label.setText("")
        self.app_worker = CheckAppUpdateWorker()
        self.app_worker.result_signal.connect(self._on_app_update_result)
        self.app_worker.start()

    def _on_app_update_result(self, date_str, error_msg):
        self.check_app_update_btn.setEnabled(True)
        self.check_app_update_btn.setText("检查软件更新")
        if date_str:
            self.app_update_date_label.setText(f"最新发行: {date_str}")
            self._append_log(f"软件最新版本发布日期: {date_str}")
        else:
            self.app_update_date_label.setText("获取失败")
            reason = f"检查软件更新失败: {error_msg}" if error_msg else "检查软件更新失败，请检查网络连接"
            self._append_log(reason)

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
    import time
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtGui import QFont
    from PyQt6.QtCore import QLockFile

    app = QApplication(sys.argv)
    app.setFont(QFont())

    # 单实例锁：防止多个 GUI 并发运行（旧实例占用桥端口、
    # Windows SO_REUSEADDR 重叠绑定导致"新代码不生效"假象，见 traps #7）
    os.makedirs("data", exist_ok=True)
    lock = QLockFile(os.path.join("data", "app.lock"))
    if not lock.tryLock():
        # 托盘"重启"时新实例可能先于旧实例退出启动：短暂重试等旧实例释放锁
        locked = False
        for _ in range(10):
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
