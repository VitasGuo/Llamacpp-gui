"""llama.cpp GUI 主窗口。"""
import sys
import os
import webbrowser
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QMessageBox,
    QSplitter, QTabWidget, QFileDialog, QInputDialog, QDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor, QAction, QBrush, QColor

from config.config import Settings
from utils.validator import validate_llamacpp_file, validate_gguf
from utils.logger import error, info
from service.script_service import ScriptService
from service.script_builder import build_bat_content, extract_port
from service.process_service import ProcessService
from service.monitor_service import MonitorService
from service.path_service import ensure_webui
from chat import start_bridge
from model.script import ScriptEntry
from ui.model_tab import ModelTab
from ui.monitor_tab import MonitorTab
from ui.dialogs.new_script_dialog import NewScriptDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.workers.log_worker import LogWorker
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
        self._server_url = ""
        self._bridge_server = None
        self._bridge_port = None
        self._log_append_count = 0

        self.setWindowTitle("llama.cpp GUI Client")
        self.resize(960, 700)

        self.monitor_service = MonitorService(self.process_service)
        self.monitor_tab = MonitorTab(self.monitor_service)

        self._init_ui()
        self._load_saved_paths()
        self._restore_service_state()

        # 多服务器：每 2s 轮询刷新脚本列表状态列与控制面板
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_script_statuses)
        self._status_timer.start()

        self.monitor_service.start()
        ensure_webui()
        self._start_bridge()

    def closeEvent(self, event):
        self._status_timer.stop()
        if self._bridge_server:
            self._bridge_server.shutdown()
        self.monitor_service.stop()
        super().closeEvent(event)

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
        control_layout.addWidget(self._create_script_panel())
        control_layout.addWidget(self._create_control_panel())
        control_layout.addWidget(self._create_log_panel(), stretch=1)

        tabs.addTab(control_widget, "主控制")

        # 模型搜索与下载标签
        self.model_tab = ModelTab()
        tabs.addTab(self.model_tab, "模型搜索与下载")

        # 性能监控标签
        tabs.addTab(self.monitor_tab, "性能监控")

        main_layout.addWidget(tabs)

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
        row2.addWidget(QLabel("模型文件 (.gguf):"))
        self.model_path_edit = QLineEdit()
        self.model_path_edit.setReadOnly(True)
        row2.addWidget(self.model_path_edit)
        browse_btn2 = QPushButton("选择...")
        browse_btn2.clicked.connect(self._select_model_file)
        row2.addWidget(browse_btn2)
        layout.addLayout(row2)

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
        # 双列：脚本名 + 状态（● 运行中 PID=xxx / 已停止）
        self.script_list = QTreeWidget()
        self.script_list.setColumnCount(2)
        self.script_list.setHeaderLabels(["脚本", "状态"])
        self.script_list.setColumnWidth(1, 130)
        self.script_list.setRootIsDecorated(False)
        self.script_list.setUniformRowHeights(True)
        self.script_list.currentItemChanged.connect(self._on_script_selected)
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
        layout = QHBoxLayout(group)

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
        self._refresh_script_list()

    def _restore_service_state(self):
        # 多服务器恢复：对每个保存了 pid 的脚本探测存活并同步 UI
        alive = self.process_service.restore_all_pids()
        for name, pid in alive.items():
            self._append_log(f"检测到脚本 '{name}' 上次启动的服务仍在运行, PID={pid}")
        if not alive:
            # 回退：旧版本 data/last_pid.pid（无脚本归属信息，行为与升级前一致）
            pid = self.process_service.restore_last_pid()
            if pid is not None:
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
        self.script_list.setColumnCount(2)
        scripts = self.script_service.load_scripts()
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

    def _refresh_script_statuses(self):
        """2s 轮询：按 tasklist 探测每个脚本的 pid 存活，刷新状态列与控制面板。"""
        runtime = self.process_service.load_runtime()
        for i in range(self.script_list.topLevelItemCount()):
            item = self.script_list.topLevelItem(i)
            name = item.text(0)
            alive = self.process_service.is_running(name)
            entry = runtime.get(name) or {}
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

        无选中脚本时回退全局 current_pid（旧版本启动/last_pid 恢复的进程）。
        """
        name = self.current_script_name
        if name:
            # 该脚本有活动 LogWorker（刚启动、pids.json 可能尚未落盘）→ 运行中
            running = any(
                getattr(w, "script_name", None) == name for w in self.log_workers
            )
            if not running:
                running = self.process_service.is_running(name)
        else:
            running = self.process_service.is_running()
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
            self._sync_control_panel()
        else:
            # 取消选中 → 控制面板回退全局状态（旧版本启动/last_pid 恢复的进程）
            self.current_script_name = ""
            self.monitor_tab.set_focus_script("")
            self._sync_control_panel()

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
            else:
                QMessageBox.warning(self, "验证失败", "请选择 .gguf 格式的模型文件。")

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

        name, ok = QInputDialog.getText(self, "新建脚本", "请输入脚本名称:")
        if not ok or not name:
            return

        exe_dir = os.path.dirname(llamacpp_path)
        dialog = NewScriptDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            config = dialog.get_config()
            bat_content = build_bat_content(
                exe_dir, model_path, config,
                visual_model_path=self.settings.visual_model_path,
            )
            self.script_editor.setPlainText(bat_content)
            self.current_script_name = name
            entry = ScriptEntry(name=name, content=bat_content, model_path=model_path)
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
            entry = ScriptEntry(
                name=self.current_script_name,
                content=content,
                model_path=self.settings.model_path,
            )
            bat_path = self.script_service.save_script(entry)
            if bat_path:
                self._append_log(f"脚本已保存: {bat_path}")
        else:
            name, ok = QInputDialog.getText(self, "保存脚本", "请输入脚本名称:")
            if ok and name:
                content = self.script_editor.toPlainText()
                entry = ScriptEntry(name=name, content=content, model_path=self.settings.model_path)
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

        bat_path = self.script_service.get_script_path(self.current_script_name)
        if not os.path.exists(bat_path):
            entry = ScriptEntry(
                name=self.current_script_name,
                content=content,
                model_path=self.settings.model_path,
            )
            bat_path = self.script_service.save_script(entry)

        # 端口预检：解析 .bat 的 --port（默认 8080），bind 探测；被占则询问
        port = extract_port(content, default=8080)
        if self.process_service.is_port_in_use(port):
            reply = QMessageBox.question(
                self, "端口占用",
                f"端口 {port} 已被占用，仍要启动？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._server_url = ""
        worker = LogWorker(
            bat_path, self.process_service,
            self.current_script_name, port,
        )
        self.log_worker = worker
        self.log_workers.append(worker)
        worker.log_signal.connect(self._append_log)
        worker.server_ready_signal.connect(self._on_server_ready)
        worker.tps_signal.connect(self.monitor_tab.update_tps)
        worker.finished.connect(lambda w=worker: self._on_run_finished(w))
        worker.finished.connect(self.monitor_tab.on_server_stopped)
        worker.start()

        self.is_running = True
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("\u25cf 运行中")
        self.status_label.setStyleSheet(
            "color: orange; font-size: 12px; font-weight: bold;"
        )
        self._refresh_script_statuses()
        self.monitor_tab.on_server_started()

    def _on_server_ready(self, url):
        url = url.replace("0.0.0.0", "127.0.0.1")
        self._server_url = url
        self._append_log(f"检测到服务已就绪: {url}")

    def _on_run_finished(self, worker=None):
        # 多服务器：某脚本的进程退出 → 清除该脚本运行时记录，刷新状态
        if worker is not None:
            name = getattr(worker, "script_name", "")
            if name:
                self.process_service.clear_runtime(name)
            self.log_workers = [w for w in self.log_workers if w is not worker]
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
        else:
            stopped = self.process_service.stop_by_pid()
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
        self._sync_control_panel()
        self._refresh_script_statuses()

    def _start_bridge(self):
        try:
            self._bridge_server, self._bridge_port = start_bridge()
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
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFont

    app = QApplication(sys.argv)
    app.setFont(QFont())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
