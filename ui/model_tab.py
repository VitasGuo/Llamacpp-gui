"""模型搜索与下载标签页。"""
import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QFileDialog,
    QMessageBox, QProgressBar, QStackedWidget, QCheckBox, QSplitter, QComboBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSystemTrayIcon

from config.config import Settings
from service import model_file_service
from service import model_sources
from service import watchlist_service
from service.download_service import DownloadManager
from service.script_service import ScriptService
from ui.workers.search_worker import SearchWorker, FileListWorker
from ui.workers.local_model_workers import (
    LocalModelDeleteWorker, LocalModelScanWorker,
)
from ui.model_watch_tab import ModelWatchView
from utils.logger import info


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


def _format_mtime(ts):
    """文件修改时间戳 → 'YYYY-MM-DD HH:MM'；None/异常返回 '-'。"""
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return "-"


def _local_model_row(model):
    """本地模型表格行的显示文本（纯函数，便于回归测试）。"""
    name = model.get("name", "")
    return {
        "display_name": f"{name}（视觉投影）" if model.get("is_mmproj") else name,
        "size": _format_size(model.get("size", 0)) or "-",
        "mtime": _format_mtime(model.get("mtime")),
    }


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


def _make_progress_cell(value):
    """下载队列"进度"单元格：进度条 + 右侧百分比 QLabel（阿拉伯数字）。

    QProgressBar 的条内建文本（默认格式 %p%）在本环境渲染为乱码，看着像
    中文字符、数字完全认不出（traps #2，monitor_tab 同样处理）——故关闭
    setTextVisible，数值一律由 QLabel 以普通 ASCII 数字显示。
    """
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(2, 0, 2, 0)
    layout.setSpacing(6)
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(value)
    bar.setTextVisible(False)
    label = QLabel(f"{value}%")
    label.setFixedWidth(38)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(bar, 1)
    layout.addWidget(label)
    return container


def _set_progress_cell(cell, value):
    """更新进度单元格（条 + 文本），与 _make_progress_cell 成对使用。"""
    if cell is None:
        return
    bar = cell.findChild(QProgressBar)
    if bar is not None:
        bar.setValue(value)
    label = cell.findChild(QLabel)
    if label is not None:
        label.setText(f"{value}%")


class ModelTab(QWidget):
    # 本地模型文件被删除（携带被删路径）→ 宿主主窗口据此清理已失效的当前选择
    local_models_changed = pyqtSignal(list)

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
        # 当前文件列表的来源：搜索查看跟随 combo（默认），追踪查看强制 ModelScope
        self._filelist_source = model_sources.SOURCE_MODELSCOPE
        # 文件列表"返回"目标：0=追踪视图 1=搜索结果页
        self._filelist_back_index = 0
        self._watchlist = []          # 关注列表（watchlist_service 读入，供搜索结果追踪按钮判断）
        # "本地模型"视图（索引 3）：最近一次扫描结果 + 后台 worker 引用（防 GC）
        self._local_models = []
        self._local_worker = None
        self._local_delete_workers = []
        self.watch_view = ModelWatchView()  # 更新追踪视图（默认视图）
        self._init_ui()
        self._connect_download_signals()
        self._restore_queue()
        # 追踪视图"查看文件"→ 复用文件列表浏览+下载（追踪列表只有 ModelScope 模型）
        self.watch_view.view_model_requested.connect(
            lambda mid: self._show_file_list(
                mid, source=model_sources.SOURCE_MODELSCOPE, back_index=0)
        )

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        layout.addWidget(self._build_search_bar())

        splitter = QSplitter(Qt.Orientation.Vertical)

        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.watch_view)              # 0: 更新追踪（默认视图）
        self.stack.addWidget(self._build_results_page())   # 1: 搜索结果
        self.stack.addWidget(self._build_filelist_page())  # 2: 文件列表
        self.stack.addWidget(self._build_local_models_page())  # 3: 本地模型
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
        # 清空搜索框并回到默认的更新追踪视图（搜索后返回追踪的唯一入口）
        self.clear_search_btn = QPushButton("×")
        self.clear_search_btn.setFixedWidth(28)
        self.clear_search_btn.setToolTip("清空搜索，返回追踪视图")
        self.clear_search_btn.clicked.connect(self._clear_search)
        bar.addWidget(self.search_input)
        bar.addWidget(self.clear_search_btn)
        bar.addWidget(self.search_btn)
        bar.addStretch(1)
        self.local_models_btn = QPushButton("本地模型管理")
        self.local_models_btn.setToolTip(
            "查看并删除模型目录里的本地 .gguf 文件（含视觉投影）"
        )
        self.local_models_btn.clicked.connect(self._show_local_models)
        bar.addWidget(self.local_models_btn)
        w = QWidget()
        w.setLayout(bar)
        return w

    def _clear_search(self):
        """清空搜索并回到默认的更新追踪视图。"""
        self.search_input.clear()
        self.stack.setCurrentIndex(0)

    def _build_results_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.result_table = QTableWidget(0, 6)
        self.result_table.setHorizontalHeaderLabels(["模型ID", "参数/标签", "下载量", "更新日期", "操作", "追踪"])
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
        self.result_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(5, 70)
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
        self.back_btn = QPushButton("\u2190 返回")
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

    def _watch_model(self, model_id, btn):
        """搜索结果手动追踪一个模型（加入关注列表，供追踪视图查看/下载）。"""
        watchlist_service.add_manual_model(model_id)
        self._watchlist = watchlist_service.load_watchlist()
        btn.setText("已追踪")
        btn.setEnabled(False)
        # 轻量同步追踪视图（单行插入，不整表重渲染、不触发网络检查）
        self.watch_view.notify_added(model_id)

    def refresh_watch(self):
        """主窗口切回本标签页时刷新追踪视图（并入新本地模型 + 空闲检查）。"""
        self.watch_view.refresh_watch()

    def _do_search(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入搜索关键词")
            return
        # 刷新关注列表，使结果行"追踪"按钮反映独立追踪页的最新状态
        self._watchlist = watchlist_service.load_watchlist()
        self._current_keyword = keyword
        self._current_page = 1
        self._load_search_page()

    def _load_search_page(self):
        # 网络请求放到后台 QThread：GUI 线程同步执行（超时可到 15s）
        # 会冻结整个窗口，搜索期间无法操作也无法取消
        self.search_btn.setEnabled(False)
        self.search_btn.setText("搜索中...")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self._search_worker = SearchWorker(
            self._current_source,
            self._current_keyword,
            self._current_page,
            self._page_size,
        )
        self._search_worker.result_signal.connect(
            lambda result, w=self._search_worker: self._on_search_result(w, result)
        )
        self._search_worker.start()

    def _on_search_result(self, worker, result):
        # 过期保护：连续搜索/切来源后，旧 worker 的迟到结果不得覆盖新结果
        if worker is not self._search_worker:
            return
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

            tracked = any(w["model_id"] == mid for w in self._watchlist)
            watch_btn = QPushButton("已追踪" if tracked else "追踪")
            watch_btn.setEnabled(not tracked)
            # watch_btn 必须用默认参数绑定：lambda 只捕获循环变量引用，
            # 不绑定的话点任意行生效的都是最后一行的按钮
            watch_btn.clicked.connect(lambda checked, x=mid, b=watch_btn: self._watch_model(x, b))
            self.result_table.setCellWidget(row, 5, watch_btn)

        self.stack.setCurrentIndex(1)

    def _on_source_changed(self):
        source = self.source_combo.currentData()
        if source == self._current_source:
            return
        self._current_source = source
        if source == model_sources.SOURCE_HF_MIRROR:
            self.search_input.setPlaceholderText("输入关键词搜索 Hugging Face 模型...")
        else:
            self.search_input.setPlaceholderText("输入关键词搜索 ModelScope 模型...")
        # 空输入：切换来源不打扰当前视图（追踪界面保持正常，不重新加载）
        if not self.search_input.text().strip():
            return
        # 有输入：保留输入内容；旧来源的搜索结果作废，清空并回到默认
        # 的更新追踪视图，重新点搜索才显示新来源结果
        self._current_keyword = ""
        self._current_page = 1
        self._has_next = False
        self._total_count = 0
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

    def _show_file_list(self, model_id, source=None, back_index=1):
        """进入某模型的 gguf 文件列表页（供下载）。

        source：文件列表与下载所用的来源——搜索结果跟随当前 combo（默认）；
        追踪视图查看强制 ModelScope（追踪列表只有 ModelScope 模型，避免
        combo 停在 HF 镜像时下载 URL 错误）。
        back_index："返回"目标，0=追踪视图 1=搜索结果页。
        """
        self._current_model_id = model_id
        self._filelist_source = source or self._current_source
        self._filelist_back_index = back_index
        source_label = model_sources.get_label(self._filelist_source)
        self.filelist_model_label.setText(f"当前模型 [{source_label}]: {model_id}")
        self.filelist_model_label.setStyleSheet("font-weight: bold;")

        # 文件列表同样是网络请求 → 后台线程加载，先显示占位行
        self.file_table.setRowCount(1)
        self.file_table.setItem(0, 1, QTableWidgetItem("文件列表加载中..."))
        self.stack.setCurrentIndex(2)
        self._filelist_worker = FileListWorker(self._filelist_source, model_id)
        self._filelist_worker.result_signal.connect(self._on_filelist_result)
        self._filelist_worker.start()

    def _on_filelist_result(self, model_id, files):
        # 迟到的结果（用户已切走/换了模型）直接丢弃
        if model_id != self._current_model_id:
            return
        self.file_table.setRowCount(0)
        if files is None:
            self.file_table.insertRow(0)
            self.file_table.setItem(0, 1, QTableWidgetItem("文件列表加载失败，请重试"))
            return
        gguf_files = [f for f in files if f.get("Path", "").lower().endswith(".gguf")]

        if not gguf_files:
            self.file_table.insertRow(0)
            self.file_table.setItem(0, 1, QTableWidgetItem("该模型下没有 .gguf 文件"))
            self.stack.setCurrentIndex(2)
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

        self.stack.setCurrentIndex(2)

    def _back_to_results(self):
        # 按进入文件列表时的来源返回：搜索查看→搜索结果页，追踪查看→追踪视图
        self.stack.setCurrentIndex(self._filelist_back_index)

    # ── 本地模型文件管理（索引 3：扫描 / 查看 / 删除）────────────────────────

    def _build_local_models_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        top_bar = QHBoxLayout()
        back_btn = QPushButton("\u2190 返回")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        top_bar.addWidget(back_btn)
        self.local_dir_label = QLabel("")
        self.local_dir_label.setStyleSheet("font-weight: bold;")
        top_bar.addWidget(self.local_dir_label)
        top_bar.addStretch()
        self.local_refresh_btn = QPushButton("刷新")
        self.local_refresh_btn.clicked.connect(self._load_local_models)
        top_bar.addWidget(self.local_refresh_btn)
        w = QWidget()
        w.setLayout(top_bar)
        layout.addWidget(w)

        self.local_table = QTableWidget(0, 5)
        self.local_table.setHorizontalHeaderLabels(
            ["", "文件名", "大小", "修改时间", "操作"])
        self.local_table.horizontalHeader().setStretchLastSection(False)
        self.local_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.local_table.setColumnWidth(0, 30)
        self.local_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.local_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.local_table.setColumnWidth(2, 100)
        self.local_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.local_table.setColumnWidth(3, 140)
        self.local_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.local_table.setColumnWidth(4, 80)
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.local_table)

        bottom_bar = QHBoxLayout()
        self.local_summary_label = QLabel("")
        self.local_summary_label.setStyleSheet("color: gray; font-size: 12px;")
        bottom_bar.addWidget(self.local_summary_label, stretch=1)
        self.local_delete_btn = QPushButton("删除选中项")
        self.local_delete_btn.clicked.connect(self._delete_local_selected)
        bottom_bar.addWidget(self.local_delete_btn)
        w2 = QWidget()
        w2.setLayout(bottom_bar)
        layout.addWidget(w2)
        return page

    def _show_local_models(self):
        """进入"本地模型"视图并后台扫描模型目录（os.walk 大库较慢，不进 GUI 线程）。"""
        self.local_dir_label.setText(
            f"模型目录：{self.settings.model_dir or '（未配置）'}")
        self.stack.setCurrentIndex(3)
        self._load_local_models()

    def _load_local_models(self):
        self.local_refresh_btn.setEnabled(False)
        self.local_delete_btn.setEnabled(False)
        self.local_table.setRowCount(0)
        self.local_table.insertRow(0)
        self.local_table.setItem(0, 1, QTableWidgetItem("扫描中..."))
        self.local_summary_label.setText("")
        self._local_worker = LocalModelScanWorker(self.settings.model_dir)
        self._local_worker.scanned.connect(
            lambda models, w=self._local_worker: self._on_local_scan(models, w))
        self._local_worker.start()

    def _on_local_scan(self, models, worker):
        # 过期保护：连续刷新/重进视图后，旧 worker 的迟到结果不得覆盖新结果
        if worker is not self._local_worker:
            return
        self._local_refresh_btn_enable()
        self._local_models = models or []
        self._fill_local_table(self._local_models)

    def _local_refresh_btn_enable(self):
        self.local_refresh_btn.setEnabled(True)
        self.local_delete_btn.setEnabled(True)

    def _fill_local_table(self, models):
        self.local_table.setRowCount(0)
        for m in models:
            row = self.local_table.rowCount()
            self.local_table.insertRow(row)
            cb = QCheckBox()
            cw = QWidget()
            cl = QHBoxLayout(cw)
            cl.addWidget(cb)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.setContentsMargins(0, 0, 0, 0)
            self.local_table.setCellWidget(row, 0, cw)

            disp = _local_model_row(m)
            name_item = QTableWidgetItem(disp["display_name"])
            # 原始路径（正斜线）存 UserRole：删除与配对 mmproj 都按它定位，
            # 不从显示文本反解析（traps #42：路径比对必须走同一规范形式）
            name_item.setData(Qt.ItemDataRole.UserRole, m.get("path", ""))
            self.local_table.setItem(row, 1, name_item)
            self.local_table.setItem(row, 2, QTableWidgetItem(disp["size"]))
            self.local_table.setItem(row, 3, QTableWidgetItem(disp["mtime"]))

            del_btn = QPushButton("删除")
            del_btn.clicked.connect(
                lambda _, p=m.get("path", ""): self._delete_local_paths([p]))
            self.local_table.setCellWidget(row, 4, del_btn)

        total = sum(m.get("size", 0) for m in models)
        self.local_summary_label.setText(
            f"共 {len(models)} 个文件，合计 {_format_size(total) or '0KB'}"
            if models else "模型目录里没有 .gguf 文件")

    def _checked_local_paths(self):
        """勾选的模型文件路径（去重保序）。"""
        paths = []
        for row in range(self.local_table.rowCount()):
            cell = self.local_table.cellWidget(row, 0)
            item = self.local_table.item(row, 1)
            cb = cell.findChild(QCheckBox) if cell else None
            if not (cb and cb.isChecked() and item):
                continue
            p = item.data(Qt.ItemDataRole.UserRole)
            if p and p not in paths:
                paths.append(p)
        return paths

    def _delete_local_selected(self):
        paths = self._checked_local_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先勾选要删除的模型文件。")
            return
        self._delete_local_paths(paths)

    def _delete_local_paths(self, paths):
        """确认后永久删除指定 .gguf（含配对 mmproj）+ 清理脚本绑定。

        删除不可恢复，故确认框列出实际文件与合计大小、默认按钮为"取消"；
        配对 mmproj 也一并列出（同目录多个 mmproj 时只配首个，用户可取消）。
        """
        by_path = {m.get("path", ""): m for m in self._local_models}
        targets = list(paths)
        main_models = [p for p in paths if not by_path.get(p, {}).get("is_mmproj")]
        for p in main_models:
            pair = model_file_service.pair_mmproj(p, self._local_models)
            if pair and pair not in targets:
                targets.append(pair)

        if not self._confirm_local_delete(targets, by_path, main_models):
            return

        self.local_delete_btn.setEnabled(False)
        self.local_delete_btn.setText("删除中...")
        worker = LocalModelDeleteWorker(targets, model_paths=main_models)
        self._local_delete_workers.append(worker)
        worker.done.connect(self._on_local_delete_done)
        worker.finished.connect(
            lambda w=worker: self._drop_local_delete_worker(w))
        worker.start()

    def _confirm_local_delete(self, targets, by_path, main_models):
        """删除确认框：列出实际将删的文件与合计大小，默认按钮"取消"。"""
        lines = []
        total = 0
        for p in targets:
            m = by_path.get(p, {})
            size = m.get("size", 0)
            total += size
            mark = "（视觉投影）" if m.get("is_mmproj") else ""
            lines.append(f"• {os.path.basename(p)}{mark}　{_format_size(size) or '大小未知'}")

        bindings = [self._script_binding_name(p) for p in main_models]
        bindings = [b for b in bindings if b]

        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除模型文件")
        msg.setIcon(QMessageBox.Icon.Warning)
        text = ("将永久删除以下文件（不进回收站，删除后无法恢复）：\n\n"
                + "\n".join(lines)
                + f"\n\n合计约 {_format_size(total) or '未知'}。")
        if bindings:
            text += (f"\n同时清理启动脚本绑定：{'、'.join(bindings)}"
                     "（原 .bat 会备份到 data/scripts_replaced，可人工找回）。")
        text += "\n确定继续吗？"
        msg.setText(text)
        ok_btn = msg.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(cancel_btn)
        msg.exec()
        return msg.clickedButton() is ok_btn

    @staticmethod
    def _script_binding_name(model_path):
        """该模型当前绑定的脚本名（仅用于确认框展示绑定将被清理）。"""
        try:
            entry = ScriptService().get_script_for_model(model_path)
        except Exception:
            return ""
        return entry.name if entry else ""

    def _drop_local_delete_worker(self, worker):
        if worker in self._local_delete_workers:
            self._local_delete_workers.remove(worker)

    def _on_local_delete_done(self, result):
        self.local_delete_btn.setEnabled(True)
        self.local_delete_btn.setText("删除选中项")

        if result.get("blocked"):
            QMessageBox.warning(
                self, "模型正在运行",
                f"{result['blocked']} 正在运行，未执行删除。\n\n"
                "请先在主控制页「运行控制」里结束该模型再删除"
                "（运行中的文件被占用，删除也会失败）。")
            return

        deleted = result.get("deleted", [])
        bindings = [b for b in (result.get("bindings") or []) if b]
        binding_names = [n for b in bindings for n in b.get("names", [])]
        parts = [f"已删除 {len(deleted)} 个文件，释放 {_format_size(result.get('freed', 0)) or '0KB'}"]
        if binding_names:
            parts.append(f"清理脚本绑定 {len(binding_names)} 条（{'、'.join(binding_names)}）"
                         f"，原 .bat 已备份到 {bindings[0].get('backup_dir', '')}")
        if result.get("errors"):
            parts.append("失败：" + "；".join(result["errors"]))
        text = "；".join(parts)
        self.local_summary_label.setText(text)
        info(f"本地模型删除: {text}")

        if result.get("errors"):
            QMessageBox.warning(self, "部分文件未能删除", text)

        if deleted:
            self.local_models_changed.emit(deleted)
        # 整表重扫：禁止按旧行号 removeRow（删除后行号会漂移）
        self._load_local_models()

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
                    self._filelist_source, self._current_model_id, file_path, size, dl_path
                )
                if ok:
                    entry = self.download_manager.download_queue.find(
                        self._filelist_source, self._current_model_id, file_path
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
            self._filelist_source, model_id, file_path, file_size, dl_path
        )
        if not ok:
            return
        entry = self.download_manager.download_queue.find(
            self._filelist_source, model_id, file_path
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

        self.queue_table.setCellWidget(row, 2, _make_progress_cell(entry.progress))

        self.queue_table.setItem(row, 3, QTableWidgetItem(""))
        self.queue_table.setItem(row, 4, QTableWidgetItem(entry.status))

        self._set_queue_actions(row, entry)

    def _update_queue_row(self, row, entry):
        _set_progress_cell(self.queue_table.cellWidget(row, 2), entry.progress)
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
            remove_btn.clicked.connect(lambda _, e=entry: self._remove_download(e))
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
        # 下载完成托盘通知：模型页常在后台下载，用户在其他页/最小化时
        # 无从感知完成（此前只改队列表格里的一行文字）
        if success:
            filename = os.path.basename(file_path)
            mw = self.window()
            if getattr(mw, "tray_icon", None) is not None:
                mw.tray_icon.showMessage(
                    "下载完成",
                    f"{filename} 已下载完成",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )

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

    def _remove_download(self, entry):
        """移除下载条目：点击时按 UserRole 现查行号。

        不能用按钮构建时的行号快照——删除任一行后其余按钮的行号全部漂移，
        removeRow 会删错行/越界静默失败，UI 与队列数据错乱。
        """
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == (entry.source, entry.file_path):
                self.queue_table.removeRow(row)
                break
        self.download_manager.remove_download(entry)

    def _restore_queue(self):
        for entry in self.download_manager.pending_downloads():
            self._add_queue_row(entry)
