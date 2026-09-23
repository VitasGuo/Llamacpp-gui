"""模型更新追踪的后台检查工作线程。

对关注列表逐个查询 ModelScope 远端更新时间并对比基线，结果经信号回传
UI 线程。网络请求耗时（14 个模型 × 每次 ~1s），必须后台执行避免卡 GUI。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from service import watchlist_service
from utils.logger import error


class WatchCheckWorker(QThread):
    finished_signal = pyqtSignal(list)  # check_updates 结果列表

    def __init__(self, watchlist=None, parent=None):
        super().__init__(parent)
        # watchlist 参数仅为调用兼容保留：检查以磁盘为准（见 run）

    def run(self):
        try:
            # 磁盘重读（不信任 UI 快照）：检查耗时 15s+，期间搜索页"追踪"
            # 等入口可能已写盘，用旧快照检查并写盘会把它们冲掉（traps #36）
            results = watchlist_service.check_updates(
                watchlist_service.load_watchlist())
        except Exception as e:
            error(f"模型更新检查异常: {e}")
            results = []
        self.finished_signal.emit(results)


class WatchMergeWorker(QThread):
    """后台并入本地模型（merge_local_models 全盘 os.walk 模型目录，
    大模型库/网络盘时秒级耗时，不能在 GUI 线程执行）。"""

    merged_signal = pyqtSignal(list)

    def __init__(self, model_dir, current=None, parent=None):
        super().__init__(parent)
        self._model_dir = model_dir
        self._current = current

    def run(self):
        try:
            # 磁盘重读（不信任 UI 快照）：扫描耗时期间其他入口的写入
            # 不能被旧快照覆盖（traps #36）
            merged = watchlist_service.merge_local_models(self._model_dir)
        except Exception as e:
            error(f"本地模型并入关注列表异常: {e}")
            merged = watchlist_service.load_watchlist() or list(self._current or [])
        self.merged_signal.emit(merged)