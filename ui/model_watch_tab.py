"""模型更新追踪视图（嵌入 ModelTab 的可复用 QWidget）。

作为"模型搜索与下载"标签页的默认视图：未搜索时显示已关注的模型列表；
搜索时由宿主 ModelTab 切换到搜索结果页。自动关注本地已装的 ModelScope
模型系列 + 搜索结果可手动追踪；后台比对远端 LastUpdatedTime 基线，
有新版整行标黄；每行提供"查看文件"进入该模型的 gguf 文件列表并下载。
"""
from datetime import datetime

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtGui import QColor

from config.config import Settings
from service import watchlist_service
from ui.workers.watch_worker import WatchCheckWorker, WatchMergeWorker


def _format_date_ts(ts):
    """Unix 秒时间戳 → 'YYYY-MM-DD HH:MM'；异常/空返回原始值。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return str(ts)


class ModelWatchView(QWidget):
    """关注模型列表视图（嵌入 ModelTab 视图栈）。"""

    # 行内"查看文件"→ 宿主 ModelTab 复用现有文件浏览+下载流程
    view_model_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.settings = Settings.get_instance()
        self._watchlist = []
        self._watch_worker = None  # 后台检查线程（保引用防 GC）
        self._merge_worker = None  # 后台本地模型扫描线程（os.walk 大模型库较慢）
        self._pending_check = False  # merge 完成后是否需要触发网络检查
        self._build_ui()
        # 首次仅并入本地模型并渲染（后台扫描）；网络检查由 refresh_watch() 触发
        self._request_merge()

    # ── 本地模型并入（后台）─────────────────────────────────────

    def _request_merge(self, then_check=False):
        """后台执行 merge_local_models（全盘扫描模型目录，不能卡 GUI 线程）。

        then_check：merge 完成后是否顺带触发网络检查（refresh 场景）。
        上一轮扫描未结束时仅记录待检查意图，不叠加 worker。
        """
        if self._merge_worker and self._merge_worker.isRunning():
            self._pending_check = self._pending_check or then_check
            return
        self._pending_check = then_check
        self._merge_worker = WatchMergeWorker(
            self.settings.model_dir, self._watchlist, parent=self)
        self._merge_worker.merged_signal.connect(self._on_merged)
        self._merge_worker.start()

    def _on_merged(self, merged):
        """扫描完成：内存列表总是更新（磁盘可能含其他入口加入的条目）；
        长度变化才重渲染（避免已有高亮被"待检查"覆盖，随后检查会重渲染）。"""
        length_changed = len(merged) != len(self._watchlist)
        self._watchlist = merged
        if length_changed:
            self._refresh_watch_table()
        if self._pending_check:
            self._pending_check = False
            self._start_watch_check()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hint = QLabel(
            "未搜索时显示已关注的模型；远端有新版本会标黄提示。\n"
            "搜索结果页可手动点\"追踪\"添加更多模型；追踪列表每行可\"查看文件\"下载。")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        bar = QHBoxLayout()
        self.check_btn = QPushButton("检查更新")
        self.check_btn.clicked.connect(self._start_watch_check)
        bar.addWidget(self.check_btn)
        bar.addStretch()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        bar.addWidget(self.status_label)
        layout.addLayout(bar)

        self.watch_table = QTableWidget(0, 6)
        self.watch_table.setHorizontalHeaderLabels(
            ["状态", "模型 ID", "最后更新", "上次检查", "查看文件", "操作"])
        header = self.watch_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.watch_table.setColumnWidth(0, 110)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.watch_table.setColumnWidth(2, 110)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.watch_table.setColumnWidth(3, 130)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.watch_table.setColumnWidth(4, 80)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.watch_table.setColumnWidth(5, 60)
        self.watch_table.verticalHeader().setVisible(False)
        self.watch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.watch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.watch_table)

    def refresh(self):
        """并入其他来源加入的关注、新下载的本地模型后重渲染；随后自动检查。"""
        self._request_merge(then_check=True)

    def refresh_watch(self):
        """外部（主窗口切到宿主标签页）提示刷新：合并新本地模型 + 若空闲自动检查。"""
        self._request_merge(then_check=bool(self._watchlist))

    def _refresh_watch_table(self):
        self.watch_table.setRowCount(0)
        for row, w in enumerate(self._watchlist):
            self.watch_table.insertRow(row)
            self._render_watch_row(row, w)

    def _render_watch_row(self, row, w):
        """渲染关注列表的一行（供全量重渲染与 notify_added 单行插入复用）。"""
        source_mark = "本地" if w.get("added_by") == "local" else "关注"
        mid_item = QTableWidgetItem(f"{w.get('model_id', '')}  （{source_mark}）")
        self.watch_table.setItem(row, 1, mid_item)

        status_item = QTableWidgetItem("待检查")
        status_item.setForeground(QColor("#909090"))
        lu = w.get("last_updated")
        self.watch_table.setItem(row, 2, QTableWidgetItem(
            _format_date_ts(lu) if lu else "-"))
        lc = w.get("last_checked") or "-"
        self.watch_table.setItem(row, 3, QTableWidgetItem(
            lc[:16].replace("T", " ") if lc != "-" else "-"))
        self.watch_table.setItem(row, 0, status_item)

        mid = w.get("model_id", "")
        view_btn = QPushButton("查看文件")
        view_btn.clicked.connect(
            lambda checked, x=mid: self.view_model_requested.emit(x))
        self.watch_table.setCellWidget(row, 4, view_btn)

        rm = QPushButton("移除")
        rm.clicked.connect(lambda _, mid=mid: self._remove_watch(mid))
        self.watch_table.setCellWidget(row, 5, rm)

    def notify_added(self, model_id):
        """搜索页"追踪"成功后轻量插入一行（不整表重渲染，保留已有高亮）。"""
        if any(w["model_id"] == model_id for w in self._watchlist):
            return
        self._watchlist.append({
            "model_id": model_id,
            "source": "modelscope",
            "added_by": "manual",
            "last_updated": None,
            "last_checked": "",
        })
        row = self.watch_table.rowCount()
        self.watch_table.insertRow(row)
        self._render_watch_row(row, self._watchlist[-1])

    def _start_watch_check(self):
        if self._watch_worker and self._watch_worker.isRunning():
            return  # 上一轮未结束则跳过
        if not self._watchlist:
            self.status_label.setText("关注列表为空")
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("检查中...")
        self.status_label.setText(f"正在检查 {len(self._watchlist)} 个模型...")
        self._watch_worker = WatchCheckWorker(self._watchlist, parent=self)
        self._watch_worker.finished_signal.connect(self._on_watch_check_done)
        self._watch_worker.start()

    def _on_watch_check_done(self, results):
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")
        update_count = sum(1 for r in results if r.get("has_update"))
        self.status_label.setText(
            f"共 {len(results)} 个模型，{update_count} 个可更新" if results
            else "检查完成（无结果）")
        # 更新基线/时间后重取并重渲染（含高亮）
        self._watchlist = watchlist_service.load_watchlist()
        self._refresh_watch_table()
        self._apply_watch_highlights(results)
        # 检查完成后本地可能又下载了模型，再次并入（后台，不重复触发检查）
        self._request_merge(then_check=False)

    def _apply_watch_highlights(self, results):
        by_id = {r["model_id"]: r for r in results}
        for row in range(self.watch_table.rowCount()):
            mid = self.watch_table.item(row, 1).text().split("  （")[0]
            r = by_id.get(mid)
            if not r:
                continue
            if r.get("has_update"):
                for col in range(self.watch_table.columnCount()):
                    it = self.watch_table.item(row, col)
                    if it:
                        it.setBackground(QColor("#fff3cd"))
                status = self.watch_table.item(row, 0)
                status.setText("● 有更新")
                status.setForeground(QColor("#e67e22"))
            elif r.get("error"):
                self.watch_table.item(row, 0).setText("查询失败")
                self.watch_table.item(row, 0).setForeground(QColor("#e74c3c"))
            else:
                self.watch_table.item(row, 0).setText("已是最新")
                self.watch_table.item(row, 0).setForeground(QColor("#27ae60"))

    def _remove_watch(self, model_id):
        self._watchlist = watchlist_service.remove_model(model_id, self._watchlist)
        self._refresh_watch_table()
