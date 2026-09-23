"""版本管理标签页：llama.cpp 检测更新 / 下载安装 / 版本切换。"""
import os
import re

from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QCheckBox,
    QProgressBar, QLineEdit, QFileDialog, QMessageBox, QAbstractItemView,
)

from config.config import Settings
from service import llamacpp_update_service as svc
from service.process_service import ProcessService
from service.script_service import ScriptService
from ui.workers.update_manager_workers import (
    LocalInfoWorker, ReleasesWorker, InstallWorker,
)
from ui.workers.cleanup_worker import CleanupPlanWorker, CleanupExecWorker


def _format_size(size_bytes):
    if size_bytes <= 0:
        return ""
    if size_bytes >= 10 ** 9:
        return f"{size_bytes / 10 ** 9:.1f}GB"
    if size_bytes >= 10 ** 6:
        return f"{size_bytes / 10 ** 6:.0f}MB"
    return f"{size_bytes / 10 ** 3:.0f}KB"


class UpdateTab(QWidget):
    """llama.cpp 版本管理。

    对外信号：version_switched(str) —— 切换版本后发出新 exe 路径，
    主窗口据此刷新主控制页路径显示；
    restart_requested() —— 用户选择"立即重启"后发出，主窗口执行重启。
    """
    version_switched = pyqtSignal(str)
    restart_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = Settings.get_instance()
        self._local_build = 0
        self._local_variant = ""
        self._gpu_info = {"nvidia": False}
        self._releases = []
        self._workers = []          # 短任务 worker 保引用防 GC
        self._install_worker = None
        self._install_silent = False     # 后台静默模式：完成/失败不弹对话框
        self._local_info_done = False    # 本地版本检测完成（自动下载的前置条件）
        self._auto_handled_tag = ""      # 已规划过自动下载的最新 tag（防重复触发）
        self._auto_queue = []            # 待静默安装的 [(tag, variant)]
        self._installed_dirs = []        # 已安装版本目录（CUDA DLL 复制来源）
        self._init_ui()
        self._refresh_local_info()
        self._refresh_installed()
        # 启动即后台检查 + 静默下载最新版（当前通道 + 推荐变体）
        if self.settings.llamacpp_auto_download:
            self._check_releases()

    # ── UI 组装 ────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._create_local_group())
        layout.addWidget(self._create_releases_group())
        layout.addWidget(self._create_progress_group())
        layout.addWidget(self._create_installed_group())
        layout.addWidget(self._create_settings_group())

    def _create_local_group(self):
        group = QGroupBox("本机状态")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("当前版本:"))
        self.local_version_label = QLabel("检测中...")
        self.local_version_label.setStyleSheet("font-weight: bold;")
        row1.addWidget(self.local_version_label)
        row1.addStretch(1)
        self.refresh_local_btn = QPushButton("刷新本机信息")
        self.refresh_local_btn.clicked.connect(self._refresh_local_info)
        row1.addWidget(self.refresh_local_btn)
        layout.addLayout(row1)

        self.local_path_label = QLabel("")
        self.local_path_label.setStyleSheet("color: gray; font-size: 11px;")
        self.local_path_label.setWordWrap(True)
        layout.addWidget(self.local_path_label)

        self.gpu_label = QLabel("")
        self.gpu_label.setWordWrap(True)
        layout.addWidget(self.gpu_label)
        return group

    def _create_releases_group(self):
        group = QGroupBox("可用版本（GitHub）")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        self.check_btn = QPushButton("检查更新")
        self.check_btn.clicked.connect(self._check_releases)
        row1.addWidget(self.check_btn)
        self.releases_status_label = QLabel("")
        self.releases_status_label.setStyleSheet("color: gray;")
        row1.addWidget(self.releases_status_label, stretch=1)
        layout.addLayout(row1)

        self.releases_table = QTableWidget(0, 3)
        self.releases_table.setHorizontalHeaderLabels(["版本", "发布日期", "状态"])
        self.releases_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.releases_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.releases_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.releases_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.releases_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.releases_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.releases_table.itemSelectionChanged.connect(self._on_release_selected)
        layout.addWidget(self.releases_table)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("构建变体:"))
        self.variant_combo = QComboBox()
        self.variant_combo.setEnabled(False)
        self.variant_combo.currentIndexChanged.connect(self._on_variant_changed)
        row2.addWidget(self.variant_combo, stretch=1)
        layout.addLayout(row2)

        self.cudart_check = QCheckBox("同时下载 CUDA 运行库（约 380MB，系统未检测到 CUDA 时首次安装需要）")
        self.cudart_check.setChecked(True)
        self.cudart_check.setVisible(False)
        self.cudart_check.toggled.connect(lambda _: self._update_asset_hint())
        layout.addWidget(self.cudart_check)

        row3 = QHBoxLayout()
        self.download_btn = QPushButton("下载并安装")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._download_and_install)
        row3.addWidget(self.download_btn)
        self.import_btn = QPushButton("手动导入 zip 安装...")
        self.import_btn.clicked.connect(self._import_zip)
        row3.addWidget(self.import_btn)
        self.asset_hint_label = QLabel("")
        self.asset_hint_label.setStyleSheet("color: gray; font-size: 11px;")
        row3.addWidget(self.asset_hint_label, stretch=1)
        layout.addLayout(row3)
        return group

    def _create_progress_group(self):
        group = QGroupBox("下载与安装进度")
        layout = QVBoxLayout(group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        row = QHBoxLayout()
        self.progress_status_label = QLabel("空闲")
        self.progress_status_label.setStyleSheet("color: gray;")
        row.addWidget(self.progress_status_label, stretch=1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_install)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)
        return group

    def _create_installed_group(self):
        group = QGroupBox("已安装版本")
        layout = QVBoxLayout(group)

        self.installed_table = QTableWidget(0, 5)
        self.installed_table.setHorizontalHeaderLabels(
            ["版本", "变体", "安装时间", "状态", "操作"])
        header = self.installed_table.horizontalHeader()
        for col in (0, 1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.installed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.installed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.installed_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.installed_table)

        row = QHBoxLayout()
        self.open_root_btn = QPushButton("打开安装目录")
        self.open_root_btn.clicked.connect(self._open_install_root)
        row.addWidget(self.open_root_btn)
        self.refresh_installed_btn = QPushButton("刷新列表")
        self.refresh_installed_btn.clicked.connect(self._refresh_installed)
        row.addWidget(self.refresh_installed_btn)
        self.cleanup_btn = QPushButton("清理旧版本")
        self.cleanup_btn.clicked.connect(self._cleanup_old_versions)
        self.cleanup_btn.setToolTip(
            "按规则清理：保留当前使用版本 + 每个变体系列（cuda-13.3/13.4/...）"
            "最新 2 个，其余旧版本及已解压的下载缓存 zip 一并删除")
        row.addWidget(self.cleanup_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _create_settings_group(self):
        group = QGroupBox("下载设置")
        layout = QVBoxLayout(group)

        self.auto_dl_check = QCheckBox(
            "启动时后台静默下载最新版（当前通道 + 推荐变体），就绪后在\"已安装版本\"中一键切换")
        self.auto_dl_check.setChecked(self.settings.llamacpp_auto_download)
        self.auto_dl_check.toggled.connect(self._on_auto_dl_toggled)
        layout.addWidget(self.auto_dl_check)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("镜像前缀:"))
        self.mirror_edit = QLineEdit(self.settings.gh_mirror_prefix)
        self.mirror_edit.setPlaceholderText("留空直连 GitHub；示例 https://gh-proxy.com/（直连慢时可用）")
        self.mirror_edit.editingFinished.connect(self._save_mirror)
        row1.addWidget(self.mirror_edit, stretch=1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("安装根目录:"))
        self.root_edit = QLineEdit(self.settings.llamacpp_install_root)
        self.root_edit.setReadOnly(True)
        row2.addWidget(self.root_edit, stretch=1)
        browse_btn = QPushButton("选择...")
        browse_btn.clicked.connect(self._select_root)
        row2.addWidget(browse_btn)
        layout.addLayout(row2)

        hint = QLabel("提示：镜像前缀仅影响在线下载，手动导入的 zip 不受影响；"
                      "每个版本独立安装（约 0.5GB，CUDA 版含运行库），旧版本可随时切回。")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return group

    # ── 本机状态 ───────────────────────────────────────────────

    def _refresh_local_info(self):
        self.refresh_local_btn.setEnabled(False)
        self.local_version_label.setText("检测中...")
        worker = LocalInfoWorker(self.settings.llamacpp_path, parent=self)
        worker.result_signal.connect(self._on_local_info)
        self._workers.append(worker)
        worker.start()

    def _on_local_info(self, result):
        self.refresh_local_btn.setEnabled(True)
        self._local_build = result["build"]
        self._local_variant = result["variant_hint"]
        self._gpu_info = result["gpu"]
        self._local_info_done = True

        exe = self.settings.llamacpp_path
        self.local_path_label.setText(exe or "未配置 llama-server.exe 路径")

        if result["build"]:
            variant_text = svc.variant_label(self._local_variant)
            version_text = f"b{result['build']}"
            if variant_text:
                version_text += f"（{variant_text}）"
            self.local_version_label.setText(version_text)
        else:
            self.local_version_label.setText("未知" if exe else "未配置")

        if result["gpu"].get("nvidia"):
            caps = []
            if result["gpu"].get("cuda_13_ok"):
                caps.append("支持 CUDA 12.x / 13.x")
            elif result["gpu"].get("cuda_12_ok"):
                caps.append("支持 CUDA 12.x")
            else:
                caps.append("驱动过旧，CUDA 版本可能无法运行")
            self.gpu_label.setText(
                f"GPU: {result['gpu'].get('name', '')}，驱动 {result['gpu'].get('driver', '')}（{'; '.join(caps)}）")
        else:
            self.gpu_label.setText("GPU: 未检测到 NVIDIA 显卡（CUDA 版本不可用，可选择 CPU / Vulkan）")

        if result["error"]:
            self._set_releases_status(result["error"], "red")
        self._refresh_release_states()
        self._refresh_installed()      # 回填"可切换"标记
        self._maybe_auto_download()

    # ── release 列表 ───────────────────────────────────────────

    def _check_releases(self):
        self.check_btn.setEnabled(False)
        self.check_btn.setText("检查中...")
        self._set_releases_status("正在从 GitHub 获取版本列表...")
        worker = ReleasesWorker(parent=self)
        worker.result_signal.connect(self._on_releases)
        self._workers.append(worker)
        worker.start()

    def _on_releases(self, releases, error_msg):
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")
        self._releases = releases
        if error_msg:
            self._set_releases_status(error_msg, "red")
        elif not releases:
            self._set_releases_status("未获取到版本列表", "red")
        else:
            self._set_releases_status(f"共 {len(releases)} 个版本（新→旧）")
        self._fill_releases_table()
        self._maybe_auto_download()

    # ── 后台静默下载（当前通道 + 推荐变体）──────────────────────

    def _maybe_auto_download(self):
        """本地信息与 release 列表都就绪后，规划并启动后台静默下载。

        规划时传整个 releases 列表：若最新 release 资产尚未传完（只有 cudart），
        plan_auto_download 会自动落到下一个"可下载的最新推荐版本"。
        """
        if not self.settings.llamacpp_auto_download:
            return
        if not self._local_info_done or not self._releases or self._install_worker:
            return
        latest_tag = self._releases[0]["tag"]
        if not latest_tag or self._auto_handled_tag == latest_tag:
            return
        self._auto_handled_tag = latest_tag
        installed_keys = {os.path.basename(d) for d in self._installed_dirs}
        plans = svc.plan_auto_download(
            self._releases, self._local_build, self._local_variant,
            self._gpu_info, installed_keys)
        if not plans:
            return
        self._auto_queue = plans
        self._start_next_auto()

    def _start_next_auto(self):
        """顺序执行自动下载队列（一个 InstallWorker 槽位）。"""
        while self._auto_queue:
            tag, variant = self._auto_queue.pop(0)
            release = next((r for r in self._releases if r["tag"] == tag), None)
            if not release:
                continue
            tasks = self._build_tasks(release, variant, auto=True)
            if not tasks:
                continue
            # 静默标志先于 _start_install 设置（后者内部会再次赋值，幂等）
            self._install_silent = True
            self._start_install(tasks, tag, variant, silent=True)
            return

    def _fill_releases_table(self):
        self.releases_table.setRowCount(0)
        for r in self._releases:
            row = self.releases_table.rowCount()
            self.releases_table.insertRow(row)
            self.releases_table.setItem(row, 0, QTableWidgetItem(r["tag"]))
            self.releases_table.setItem(row, 1, QTableWidgetItem(r.get("date_str", "")))
            self.releases_table.setItem(row, 2, QTableWidgetItem(""))
        self._refresh_release_states()

    def _refresh_release_states(self):
        """按 _local_build 刷新状态列（本地版本检测完成后回填）。"""
        for row in range(self.releases_table.rowCount()):
            if row >= len(self._releases):
                break
            build = self._releases[row]["build"]
            if not self._local_build:
                state = "—"
            elif build == self._local_build:
                state = "已是最新"
            elif build > self._local_build:
                state = "可更新"
            else:
                state = "更旧（可安装）"
            item = self.releases_table.item(row, 2)
            if item:
                item.setText(state)
                item.setForeground(
                    Qt.GlobalColor.darkGreen if state == "可更新" else Qt.GlobalColor.gray)

    def _selected_release(self):
        row = self.releases_table.currentRow()
        if row < 0 or row >= len(self._releases):
            return None
        return self._releases[row]

    def _on_release_selected(self):
        release = self._selected_release()
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        try:
            if not release:
                self.variant_combo.setEnabled(False)
                self.download_btn.setEnabled(False)
                self.cudart_check.setVisible(False)
                self._update_asset_hint()
                return
            variants = [v for v in release["assets"] if not v.startswith("cudart-")]
            if not variants:
                self.variant_combo.setEnabled(False)
                self.download_btn.setEnabled(False)
                return
            recommended = svc.recommend_variant(self._gpu_info, variants)
            for v in variants:
                label = svc.variant_label(v)
                size = release["assets"][v].get("size", 0)
                text = f"{label}（{_format_size(size)}）"
                if v == recommended:
                    text += "  ← 推荐"
                self.variant_combo.addItem(text, v)
                if v == recommended:
                    self.variant_combo.setCurrentIndex(self.variant_combo.count() - 1)
            self.variant_combo.setEnabled(True)
            self.download_btn.setEnabled(True)
        finally:
            # 提前 return 分支也必须恢复信号（否则 currentIndexChanged 被永久屏蔽）
            self.variant_combo.blockSignals(False)
        self._on_variant_changed()

    def _on_variant_changed(self):
        variant = self.variant_combo.currentData()
        # CUDA 变体按 cudart_plan 决定：需要下载 cudart 包时才显示复选框
        show = False
        if variant and variant.startswith("cuda-"):
            release = self._selected_release()
            if release:
                show = svc.cudart_plan(release, variant, self._copy_sources()) == "download"
        self.cudart_check.setVisible(show)
        self._update_asset_hint()

    def _update_asset_hint(self):
        release = self._selected_release()
        variant = self.variant_combo.currentData()
        if not release or not variant:
            self.asset_hint_label.setText("")
            return
        parts = [svc.variant_label(variant)]
        if variant.startswith("cuda-"):
            plan = svc.cudart_plan(release, variant, self._copy_sources())
            if plan == "download" and self.cudart_check.isChecked():
                cudart = release["assets"].get(f"cudart-{variant}")
                if cudart:
                    parts.append(f"含 CUDA 运行库 {_format_size(cudart.get('size', 0))}")
            elif plan == "copy":
                parts.append("自动复制现有 CUDA 运行库")
        self.asset_hint_label.setText("将下载: " + " + ".join(parts))

    def _set_releases_status(self, text, color="gray"):
        self.releases_status_label.setText(text)
        self.releases_status_label.setStyleSheet(f"color: {color};")

    # ── 下载与安装 ─────────────────────────────────────────────

    def _zips_dir(self):
        return os.path.join(self.settings.llamacpp_install_root, "zips")

    def _copy_sources(self):
        """CUDA 运行库 DLL 的复制来源：当前 llama.cpp 目录 + 已安装版本目录。"""
        sources = []
        current_dir = os.path.dirname(self.settings.llamacpp_path or "")
        if current_dir:
            sources.append(current_dir)
        sources.extend(self._installed_dirs)
        return sources

    def _build_tasks(self, release, variant, auto=False):
        """构建下载任务：主包 +（按 cudart_plan）可选 cudart 运行库包。

        auto=True（后台静默）时跳过复选框直接按计划执行。
        """
        asset = release["assets"].get(variant)
        if not asset:
            return []
        mirror = self.settings.gh_mirror_prefix
        tasks = [{
            "url": svc.download_url(release["tag"], asset["name"], mirror),
            "dest": os.path.join(self._zips_dir(), asset["name"]),
            "digest": asset.get("digest", ""),
            "size": asset.get("size", 0),
            "label": svc.variant_label(variant),
        }]
        if variant.startswith("cuda-"):
            plan = svc.cudart_plan(release, variant, self._copy_sources())
            include = plan == "download" and (auto or self.cudart_check.isChecked())
            if include:
                cudart = release["assets"].get(f"cudart-{variant}")
                if cudart:
                    tasks.append({
                        "url": svc.download_url(release["tag"], cudart["name"], mirror),
                        "dest": os.path.join(self._zips_dir(), cudart["name"]),
                        "digest": cudart.get("digest", ""),
                        "size": cudart.get("size", 0),
                        "label": "CUDA 运行库",
                    })
        return tasks

    def _download_and_install(self):
        release = self._selected_release()
        variant = self.variant_combo.currentData()
        if not release or not variant or self._install_worker:
            return
        tasks = self._build_tasks(release, variant)
        if not tasks:
            return
        self._start_install(tasks, release["tag"], variant)

    def _import_zip(self):
        if self._install_worker:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 llama.cpp 发行包 zip", "", "zip 文件 (*.zip)")
        if not path:
            return
        basename = os.path.basename(path)
        kind, variant = svc.parse_asset_name(basename)
        m = re.match(r"^llama-(b\d+)-", basename)
        if kind != "llama" or not m:
            QMessageBox.warning(
                self, "无法识别",
                "文件名不符合 llama.cpp 官方发行包命名：\n"
                "llama-bXXXX-bin-win-<变体>-x64.zip\n"
                f"当前: {basename}")
            return
        self._start_install([], m.group(1), variant, local_zip=path)

    def _start_install(self, tasks, tag, variant, local_zip=None, silent=False):
        self._install_silent = silent
        self.download_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        prefix = "后台下载: " if silent else ""
        self.progress_status_label.setText(prefix + "准备中...")
        self.progress_status_label.setStyleSheet("color: gray;")
        self._install_worker = InstallWorker(
            tasks, self.settings.llamacpp_install_root, tag, variant,
            local_zip=local_zip, dll_sources=self._copy_sources(), parent=self)
        self._install_worker.progress_signal.connect(self._on_install_progress)
        self._install_worker.status_signal.connect(self._on_install_status)
        self._install_worker.finished_signal.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_progress(self, current, total, speed):
        prefix = "后台下载: " if self._install_silent else ""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(min(current, total))
            speed_text = f" {_format_size(speed)}/s" if speed else ""
            self.progress_status_label.setText(
                f"{prefix}下载中 {_format_size(current)} / {_format_size(total)}{speed_text}")
        else:
            self.progress_status_label.setText(f"{prefix}下载中 {_format_size(current)}")

    def _on_install_status(self, text):
        self.progress_status_label.setText(text)

    def _cancel_install(self):
        if self._install_worker:
            self._install_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.progress_status_label.setText("正在取消（已下载部分将保留，可续传）...")

    def _on_install_finished(self, ok, message, tag):
        self._install_worker = None
        self.cancel_btn.setEnabled(False)
        self.download_btn.setEnabled(self.releases_table.currentRow() >= 0)
        self.import_btn.setEnabled(True)
        if ok:
            self.progress_bar.setValue(self.progress_bar.maximum())
            self._refresh_installed()
            if self._install_silent:
                # 后台静默模式：不弹窗，继续队列，等用户进来自行切换
                self.progress_status_label.setText(f"后台下载完成: {tag}（已就绪，可切换）")
                self.progress_status_label.setStyleSheet("color: darkgreen;")
                if self._auto_queue:
                    self._start_next_auto()
                return
            self.progress_status_label.setText(f"安装完成: {tag}")
            self.progress_status_label.setStyleSheet("color: darkgreen;")
            self._refresh_local_info()
            reply = QMessageBox.question(
                self, "安装完成",
                f"{tag} 安装完成。\n\n是否立即切换到该版本？\n（启动脚本中的路径将自动更新）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._switch_to(message)
        else:
            if self._install_silent:
                self.progress_status_label.setText(f"后台下载未完成: {message}")
                self.progress_status_label.setStyleSheet("color: #a0522d;")
                if self._auto_queue:
                    self._start_next_auto()
                return
            self.progress_status_label.setText(f"未完成: {message}")
            self.progress_status_label.setStyleSheet("color: red;")
            # 数百 MB 下载失败不能只在小字显示：弹窗明确告知，附重试指引
            QMessageBox.warning(
                self, "安装未完成",
                f"{message}\n\n下载缓存已保留，可点击\"重试\"从断点继续，"
                "或在下载设置中更换镜像前缀后重试。")

    # ── 已安装版本 ─────────────────────────────────────────────

    def _refresh_installed(self):
        root = self.settings.llamacpp_install_root
        self.root_edit.setText(root)
        current_dir = os.path.normcase(
            os.path.dirname(os.path.abspath(self.settings.llamacpp_path)))
        self.installed_table.setRowCount(0)
        items = svc.list_installed(root)
        self._installed_dirs = [item["dir"] for item in items]
        for item in items:
            row = self.installed_table.rowCount()
            self.installed_table.insertRow(row)
            self.installed_table.setItem(row, 0, QTableWidgetItem(item["tag"]))
            self.installed_table.setItem(
                row, 1, QTableWidgetItem(svc.variant_label(item["variant"])))
            self.installed_table.setItem(row, 2, QTableWidgetItem(item["date"]))
            is_current = os.path.normcase(item["dir"]) == current_dir
            status_item = QTableWidgetItem("使用中" if is_current else "")
            if is_current:
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            self.installed_table.setItem(row, 3, status_item)
            btn = QPushButton("切换到此版本")
            btn.setEnabled(not is_current)
            btn.clicked.connect(lambda _, exe=item["exe"]: self._switch_to(exe))
            self.installed_table.setCellWidget(row, 4, btn)

    def _switch_to(self, exe_path):
        try:
            updated = svc.switch_version(exe_path)
        except Exception as e:
            QMessageBox.critical(self, "切换失败", str(e))
            return
        self.version_switched.emit(exe_path)
        self._refresh_local_info()
        self._refresh_installed()
        # 切换完成 → 用户选择"立即重启"（重启 GUI 加载新版本）或"稍后重启"
        msg = QMessageBox(self)
        msg.setWindowTitle("切换完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"已切换到:\n{exe_path}\n\n自动更新了 {updated} 个启动脚本中的路径。\n"
            "运行中的服务不受影响，重启 GUI 后新版本生效。"
        )
        restart_btn = msg.addButton("立即重启", QMessageBox.ButtonRole.YesRole)
        later_btn = msg.addButton("稍后重启", QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(later_btn)  # 默认稍后重启，避免误触重启打断当前会话
        msg.exec()
        if msg.clickedButton() is restart_btn:
            self.restart_requested.emit()  # 由主窗口执行应用重启

    def _open_install_root(self):
        root = self.settings.llamacpp_install_root
        os.makedirs(root, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(root)))

    def _collect_running_dirs(self):
        """收集运行中服务（pids.json）的 exe 所在目录。

        清理规划须排除这些目录：从旧版本目录启动的 llama-server 仍在跑时，
        rmtree 会把目录删成半残（未锁文件先删、锁文件才报错）。
        """
        dirs = []
        try:
            runtime = ProcessService().load_runtime()
        except Exception:
            return dirs
        scripts = ScriptService()
        for name in runtime:
            try:
                content = scripts.load_script_content(name) or ""
            except Exception:
                continue
            m = re.search(r'cd /d "([^"]+)"', content)
            if m:
                dirs.append(m.group(1))
        return dirs

    def _cleanup_old_versions(self):
        """按规则清理旧版本：后台规划 → 确认 → 后台删除。

        保留规则：当前使用版本 + 运行中服务的目录恒保留，每个变体系列
        各保留最新 2 个，其余删除。规划（递归统计大小）与删除（rmtree）
        均在后台线程执行，不冻结 GUI。
        """
        btn = self.sender()
        if isinstance(btn, QPushButton):
            btn.setEnabled(False)
            btn.setText("统计中...")
        root = self.settings.llamacpp_install_root
        worker = CleanupPlanWorker(root, self.settings.llamacpp_path,
                                   self._collect_running_dirs())
        self._workers.append(worker)

        def on_planned(del_versions, del_zips, size_ver, size_zip):
            if isinstance(btn, QPushButton):
                btn.setEnabled(True)
                btn.setText("清理旧版本")
            if not del_versions and not del_zips:
                QMessageBox.information(self, "清理旧版本", "没有需要清理的旧版本或缓存。")
                return
            if not self._confirm_cleanup(del_versions, del_zips, size_ver, size_zip):
                return
            self._exec_cleanup(del_versions, del_zips, btn)

        worker.planned.connect(on_planned)
        worker.finished.connect(lambda w=worker: self._drop_worker(w))
        worker.start()

    def _confirm_cleanup(self, del_versions, del_zips, size_ver, size_zip):
        """弹确认框（默认取消防误触）。返回用户是否确认删除。"""
        lines = []
        if del_versions:
            names = "、".join(f"{d['tag']}（{svc.variant_label(d['variant'])}）" for d in del_versions)
            freed = _format_size(size_ver)
            lines.append(f"• 旧版本目录（{len(del_versions)} 个，释放 {freed}）：\n    {names}")
            lines.append("")
        if del_zips:
            files = "、".join(os.path.basename(z) for z in del_zips[:6])
            if len(del_zips) > 6:
                files += f" 等 {len(del_zips)} 个"
            freed = _format_size(size_zip)
            lines.append(f"• 已解压的下载缓存 zip（{len(del_zips)} 个，释放 {freed}）：\n    {files}")
            lines.append("")
        lines.append(f"共可释放约 {_format_size(size_ver + size_zip)}。\n"
                     "保留：当前使用版本 + 运行中服务目录 + 每个变体系列最新 2 个。确定删除？")
        msg = QMessageBox(self)
        msg.setWindowTitle("清理旧版本")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("\n".join(lines))
        ok = msg.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(msg.buttons()[1])  # 默认取消，防误触
        msg.exec()
        return msg.clickedButton() is ok

    def _exec_cleanup(self, del_versions, del_zips, btn):
        """后台执行删除，完成后刷新并汇总。"""
        if isinstance(btn, QPushButton):
            btn.setEnabled(False)
            btn.setText("清理中...")
        worker = CleanupExecWorker(del_versions, del_zips)
        self._workers.append(worker)

        def on_done(freed, errors):
            if isinstance(btn, QPushButton):
                btn.setEnabled(True)
                btn.setText("清理旧版本")
            self._refresh_installed()
            summary = f"已清理，释放 {_format_size(freed)}。"
            if errors:
                summary += "\n以下未能删除（可能正被运行中服务占用）：\n" + "\n".join(errors[:5])
            QMessageBox.information(self, "清理完成", summary)

        worker.done.connect(on_done)
        worker.finished.connect(lambda w=worker: self._drop_worker(w))
        worker.start()

    def _drop_worker(self, worker):
        """worker 结束后移出引用列表并销毁（防会话级累积）。"""
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    # ── 下载设置 ───────────────────────────────────────────────

    def _on_auto_dl_toggled(self, on):
        self.settings.llamacpp_auto_download = on
        self.settings.save()
        if on:
            self._maybe_auto_download()

    def _save_mirror(self):
        self.settings.gh_mirror_prefix = self.mirror_edit.text().strip()
        self.settings.save()

    def _select_root(self):
        root = QFileDialog.getExistingDirectory(self, "选择安装根目录")
        if not root:
            return
        self.settings.llamacpp_install_root = root
        self.settings.save()
        self._refresh_installed()
