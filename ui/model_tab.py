"""模型搜索与下载标签页。"""
import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QFileDialog,
    QMessageBox, QProgressBar, QStackedWidget, QCheckBox, QSplitter, QComboBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt

from config.config import Settings
from service import model_sources
from service.download_service import DownloadManager


def _format_size(size_bytes):
    if size_bytes <= 0:
        return ""
    if size_bytes >= 10 ** 12:
        return f"{size_bytes / 10 ** 12:.1f}TB"
    if size_bytes >= 10 ** 9:
        return f"{size_bytes / 10 ** 9:.1f}GB"
    if size_bytes >= 10 ** 6:
        return f"{size_bytes / 10 ** 6:.1f}MB"
    return f"{size_bytes / 10 ** 3:.1f}KB"


def _format_params(params):
    if params <= 0:
        return ""
    if params >= 10 ** 9:
        return f"{params / 10 ** 9:.1f}B"
    return f"{params / 10 ** 6:.1f}M"


def _format_date(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return iso_str[:10]


class ModelTab(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = Settings.get_instance()
        self.download_manager = DownloadManager()
        self._current_source = model_sources.SOURCE_MODELSCOPE
        self._current_page = 1
        self._page_size = 20
        self._has_next = False
        self._current_keyword = ""
        self._total_count = 0
        self._current_model_id = ""
        self._init_ui()
        self._connect_download_signals()
        self._restore_queue()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        layout.addWidget(self._build_search_bar())

        splitter = QSplitter(Qt.Orientation.Vertical)

        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_results_page())
        self.stack.addWidget(self._build_filelist_page())
        self.stack.setCurrentIndex(0)
        top_layout.addWidget(self.stack)
        splitter.addWidget(top_widget)

        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(QLabel("下载队列"))
        self.queue_table = QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels(["来源", "文件名", "进度", "速度", "状态", "操作"])
        self.queue_table.horizontalHeader().setStretchLastSection(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(0, 150)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(2, 160)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(3, 80)
        self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(4, 70)
        self.queue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(5, 120)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        bottom_layout.addWidget(self.queue_table)
        splitter.addWidget(bottom_widget)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def _connect_download_signals(self):
        self.download_manager.progress_signal.connect(self._on_dl_progress)
        self.download_manager.speed_signal.connect(self._on_dl_speed)
        self.download_manager.finished_signal.connect(self._on_dl_finished)

    def _build_search_bar(self):
        bar = QHBoxLayout()
        bar.addWidget(QLabel("来源:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem(
            model_sources.get_label(model_sources.SOURCE_MODELSCOPE),
            model_sources.SOURCE_MODELSCOPE,
        )
        self.source_combo.addItem(
            model_sources.get_label(model_sources.SOURCE_HF_MIRROR),
            model_sources.SOURCE_HF_MIRROR,
        )
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        bar.addWidget(self.source_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词搜索 ModelScope 模型...")
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._do_search)
        bar.addWidget(self.search_input)
        bar.addWidget(self.search_btn)
        w = QWidget()
        w.setLayout(bar)
        return w

    def _build_results_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(["模型ID", "参数/标签", "下载量", "更新日期", "操作"])
        self.result_table.horizontalHeader().setStretchLastSection(False)
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(1, 120)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(2, 70)
        self.result_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(3, 100)
        self.result_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(4, 80)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.result_table)

        pagination_bar = QHBoxLayout()
        self.prev_btn = QPushButton("< 上一页")
        self.prev_btn.clicked.connect(self._prev_page)
        pagination_bar.addWidget(self.prev_btn)

        self.page_label = QLabel("第 0/0 页")
        pagination_bar.addWidget(self.page_label)
        pagination_bar.addStretch()

        self.next_btn = QPushButton("下一页 >")
        self.next_btn.clicked.connect(self._next_page)
        pagination_bar.addWidget(self.next_btn)

        w = QWidget()
        w.setLayout(pagination_bar)
        layout.addWidget(w)
        return page

    def _build_filelist_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()
        self.back_btn = QPushButton("\u2190 返回搜索结果")
        self.back_btn.clicked.connect(self._back_to_results)
        top_bar.addWidget(self.back_btn)
        self.filelist_model_label = QLabel("")
        self.filelist_model_label.setStyleSheet("font-weight: bold;")
        top_bar.addWidget(self.filelist_model_label)
        top_bar.addStretch()
        w = QWidget()
        w.setLayout(top_bar)
        layout.addWidget(w)

        self.file_table = QTableWidget(0, 4)
        self.file_table.setHorizontalHeaderLabels(["", "文件名", "大小", "操作"])
        self.file_table.horizontalHeader().setStretchLastSection(False)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.file_table.setColumnWidth(0, 30)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.file_table.setColumnWidth(2, 100)
        self.file_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.file_table.setColumnWidth(3, 80)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.file_table)

        dl_bar = QHBoxLayout()
        self.dl_path_edit = QLineEdit()
        self.dl_path_edit.setReadOnly(True)
        saved = self.settings.download_path
        if saved:
            self.dl_path_edit.setText(saved)
        self.dl_path_btn = QPushButton("选择...")
        self.dl_path_btn.clicked.connect(self._select_dl_path)
        dl_bar.addWidget(QLabel("下载目录:"))
        dl_bar.addWidget(self.dl_path_edit)
        dl_bar.addWidget(self.dl_path_btn)

        self.dl_selected_btn = QPushButton("下载选中项")
        self.dl_selected_btn.clicked.connect(self._download_selected)
        dl_bar.addWidget(self.dl_selected_btn)

        w2 = QWidget()
        w2.setLayout(dl_bar)
        layout.addWidget(w2)
        return page

    def _do_search(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入搜索关键词")
            return
        self._current_keyword = keyword
        self._current_page = 1
        self._load_search_page()

    def _load_search_page(self):
        self.search_btn.setEnabled(False)
        self.search_btn.setText("搜索中...")
        result = model_sources.search_models(
            self._current_source,
            self._current_keyword,
            self._current_page,
            self._page_size,
        )
        self.search_btn.setEnabled(True)
        self.search_btn.setText("搜索")
        error = result.get("error")
        if error:
            QMessageBox.warning(self, "搜索失败", f"来源 {model_sources.get_label(self._current_source)} 搜索失败：{error}")
        self._total_count = result.get("total_count", 0)
        models = result.get("models", [])
        page_size = result.get("page_size", 20)
        self._page_size = page_size
        has_next = result.get("has_next")
        if has_next is None:
            has_next = self._current_page * page_size < self._total_count
        self._has_next = has_next

        if self._total_count > 0:
            total_pages = max(1, (self._total_count + page_size - 1) // page_size)
            self.page_label.setText(f"第 {self._current_page}/{total_pages} 页")
        else:
            self.page_label.setText(f"第 {self._current_page} 页")
        self.prev_btn.setEnabled(self._current_page > 1)
        self.next_btn.setEnabled(self._has_next)

        self.result_table.setRowCount(0)
        for row, m in enumerate(models):
            self.result_table.insertRow(row)
            mid = m.get("id", "")
            self.result_table.setItem(row, 0, QTableWidgetItem(mid))
            if self._current_source == model_sources.SOURCE_HF_MIRROR:
                tag_text = m.get("pipeline_tag") or m.get("library_name") or ""
                self.result_table.setItem(row, 1, QTableWidgetItem(tag_text))
            else:
                self.result_table.setItem(row, 1, QTableWidgetItem(_format_params(m.get("params", 0))))
            self.result_table.setItem(row, 2, QTableWidgetItem(str(m.get("downloads", 0))))
            self.result_table.setItem(row, 3, QTableWidgetItem(_format_date(m.get("last_modified", ""))))

            view_btn = QPushButton("查看文件")
            view_btn.clicked.connect(lambda checked, x=mid: self._show_file_list(x))
            self.result_table.setCellWidget(row, 4, view_btn)

        self.stack.setCurrentIndex(0)

    def _on_source_changed(self):
        source = self.source_combo.currentData()
        if source == self._current_source:
            return
        self._current_source = source
        self._current_keyword = ""
        self._current_page = 1
        self._has_next = False
        self._total_count = 0
        self.search_input.clear()
        if source == model_sources.SOURCE_HF_MIRROR:
            self.search_input.setPlaceholderText("输入关键词搜索 Hugging Face 模型...")
        else:
            self.search_input.setPlaceholderText("输入关键词搜索 ModelScope 模型...")
        self.result_table.setRowCount(0)
        self.page_label.setText("第 0 页")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.stack.setCurrentIndex(0)

    def _prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._load_search_page()

    def _next_page(self):
        if self._has_next:
            self._current_page += 1
            self._load_search_page()

    def _show_file_list(self, model_id):
        self._current_model_id = model_id
        source_label = model_sources.get_label(self._current_source)
        self.filelist_model_label.setText(f"当前模型 [{source_label}]: {model_id}")
        self.filelist_model_label.setStyleSheet("font-weight: bold;")

        self.file_table.setRowCount(0)
        files = model_sources.list_model_files(self._current_source, model_id)
        gguf_files = [f for f in files if f.get("Path", "").lower().endswith(".gguf")]

        if not gguf_files:
            self.file_table.insertRow(0)
            self.file_table.setItem(0, 1, QTableWidgetItem("该模型下没有 .gguf 文件"))
            self.stack.setCurrentIndex(1)
            return

        for row, f in enumerate(gguf_files):
            self.file_table.insertRow(row)
            cb = QCheckBox()
            w = QWidget()
            wl = QHBoxLayout(w)
            wl.addWidget(cb)
            wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.setContentsMargins(0, 0, 0, 0)
            self.file_table.setCellWidget(row, 0, w)

            file_item = QTableWidgetItem(f.get("Path", ""))
            # 原始大小（字节）存进 UserRole，供 _download_selected 直接读取，避免从显示文本有损反解析
            file_item.setData(Qt.ItemDataRole.UserRole, int(f.get("Size", 0) or 0))
            self.file_table.setItem(row, 1, file_item)
            self.file_table.setItem(row, 2, QTableWidgetItem(_format_size(f.get("Size", 0))))

            dl_btn = QPushButton("下载")
            dl_btn.clicked.connect(
                lambda checked, x=model_id, p=f.get("Path", ""), s=f.get("Size", 0): self._start_single_download(x, p, s)
            )
            self.file_table.setCellWidget(row, 3, dl_btn)

        self.stack.setCurrentIndex(1)

    def _back_to_results(self):
        self.stack.setCurrentIndex(0)

    def _select_dl_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择模型下载目录")
        if path:
            self.dl_path_edit.setText(path)
            self.settings.download_path = path
            self.settings.save()

    def _download_selected(self):
        dl_path = self.dl_path_edit.text().strip()
        if not dl_path:
            QMessageBox.warning(self, "提示", "请先设置下载目录")
            return
        if not os.path.isdir(dl_path):
            QMessageBox.warning(self, "提示", "下载目录不存在")
            return

        started = 0
        for row in range(self.file_table.rowCount()):
            w = self.file_table.cellWidget(row, 0)
            if w is None:
                continue
            cb = w.findChild(QCheckBox)
            if cb and cb.isChecked():
                file_path = self.file_table.item(row, 1).text()
                # 读取行构建时存入 UserRole 的原始大小（字节），非 int 时取 0
                size = self.file_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
                if not isinstance(size, int):
                    size = 0
                ok = self.download_manager.start_download(
                    self._current_source, self._current_model_id, file_path, size, dl_path
                )
                if ok:
                    entry = self.download_manager.download_queue.find(
                        self._current_source, self._current_model_id, file_path
                    )
                    if entry:
                        self._add_queue_row(entry)
                    started += 1
        if started == 0:
            QMessageBox.warning(self, "提示", "请先勾选要下载的文件")

    def _start_single_download(self, model_id, file_path, file_size):
        dl_path = self.dl_path_edit.text().strip()
        if not dl_path:
            QMessageBox.warning(self, "提示", "请先设置下载目录")
            return
        if not os.path.isdir(dl_path):
            QMessageBox.warning(self, "提示", "下载目录不存在")
            return
        ok = self.download_manager.start_download(
            self._current_source, model_id, file_path, file_size, dl_path
        )
        if not ok:
            return
        entry = self.download_manager.download_queue.find(
            self._current_source, model_id, file_path
        )
        if entry:
            self._add_queue_row(entry)

    def _add_queue_row(self, entry):
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == (entry.source, entry.file_path):
                self._update_queue_row(row, entry)
                self._set_queue_actions(row, entry)
                return

        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)

        source_item = QTableWidgetItem(model_sources.get_short_label(entry.source))
        self.queue_table.setItem(row, 0, source_item)

        name_item = QTableWidgetItem(entry.filename)
        name_item.setData(Qt.ItemDataRole.UserRole, (entry.source, entry.file_path))
        self.queue_table.setItem(row, 1, name_item)

        prog = QProgressBar()
        prog.setMinimum(0)
        prog.setMaximum(100)
        prog.setValue(entry.progress)
        self.queue_table.setCellWidget(row, 2, prog)

        self.queue_table.setItem(row, 3, QTableWidgetItem(""))
        self.queue_table.setItem(row, 4, QTableWidgetItem(entry.status))

        self._set_queue_actions(row, entry)

    def _update_queue_row(self, row, entry):
        prog = self.queue_table.cellWidget(row, 2)
        if isinstance(prog, QProgressBar):
            prog.setValue(entry.progress)
        self.queue_table.item(row, 4).setText(entry.status)

    def _set_queue_actions(self, row, entry):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        worker_active = self.download_manager.is_worker_active(entry.source, entry.file_path)
        if entry.status == "downloading" and worker_active:
            pause_btn = QPushButton("暂停")
            pause_btn.clicked.connect(lambda: self._pause_download(entry))
            cancel_btn = QPushButton("取消")
            cancel_btn.clicked.connect(lambda: self._cancel_download(entry))
            layout.addWidget(pause_btn)
            layout.addWidget(cancel_btn)
        elif entry.status in ("paused", "pending", "downloading"):
            resume_btn = QPushButton("恢复" if entry.status in ("paused", "downloading") else "开始")
            resume_btn.clicked.connect(lambda: self._resume_download(entry))
            cancel_btn = QPushButton("取消")
            cancel_btn.clicked.connect(lambda: self._cancel_download(entry))
            layout.addWidget(resume_btn)
            layout.addWidget(cancel_btn)
        elif entry.status in ("completed", "failed", "cancelled"):
            if entry.status == "failed":
                retry_btn = QPushButton("重试")
                retry_btn.clicked.connect(lambda: self._retry_download(entry))
                layout.addWidget(retry_btn)
            remove_btn = QPushButton("移除")
            remove_btn.clicked.connect(lambda: self._remove_queue_row(row, entry))
            layout.addWidget(remove_btn)

        self.queue_table.setCellWidget(row, 5, container)

    def _on_dl_progress(self, source, file_path, current, total):
        entry = self._find_entry(source, file_path)
        if entry:
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 1)
                if item and item.data(Qt.ItemDataRole.UserRole) == (source, file_path):
                    self._update_queue_row(row, entry)
                    break

    def _on_dl_speed(self, source, file_path, speed):
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == (source, file_path):
                if speed >= 10 ** 6:
                    text = f"{speed / 10 ** 6:.1f}MB/s"
                else:
                    text = f"{speed / 10 ** 3:.1f}KB/s"
                self.queue_table.item(row, 3).setText(text)
                break

    def _on_dl_finished(self, source, file_path, success, error):
        entry = self._find_entry(source, file_path)
        if entry:
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 1)
                if item and item.data(Qt.ItemDataRole.UserRole) == (source, file_path):
                    self._update_queue_row(row, entry)
                    self._set_queue_actions(row, entry)
                    break
        else:
            self._refresh_queue_ui()

    def _find_entry(self, source, file_path):
        for e in self.download_manager.entries:
            if e.source == source and e.file_path == file_path:
                return e
        return None

    def _refresh_queue_ui(self):
        """全量刷新队列 UI，保持与 DownloadManager 状态同步。"""
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 1)
            if not item:
                continue
            key = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(key, tuple):
                continue
            source, fp = key
            entry = self._find_entry(source, fp)
            if entry:
                self._update_queue_row(row, entry)
                self._set_queue_actions(row, entry)

    def _pause_download(self, entry):
        self.download_manager.pause_download(entry)
        self._refresh_queue_ui()

    def _cancel_download(self, entry):
        self.download_manager.cancel_download(entry)
        self._refresh_queue_ui()

    def _resume_download(self, entry):
        dl_path = self.dl_path_edit.text().strip() or os.path.dirname(entry.dest_path)
        ok = self.download_manager.resume_download(entry, dl_path)
        if ok:
            self._add_queue_row(entry)

    def _retry_download(self, entry):
        dl_path = self.dl_path_edit.text().strip() or os.path.dirname(entry.dest_path)
        ok = self.download_manager.retry_download(entry, dl_path)
        if ok:
            self._add_queue_row(entry)

    def _remove_queue_row(self, row, entry):
        self.queue_table.removeRow(row)
        self.download_manager.remove_download(entry)

    def _restore_queue(self):
        for entry in self.download_manager.pending_downloads():
            self._add_queue_row(entry)
