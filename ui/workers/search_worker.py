"""模型搜索/文件列表的后台工作线程。

搜索与文件列表是网络请求（超时可达 15s），绝不能在 GUI 线程同步执行——
否则整个窗口冻结（与主控制页 tasklist 阻塞同类问题，见 traps #5）。
结果通过 pyqtSignal 回传 UI 层。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from service import model_sources
from utils.logger import error


class SearchWorker(QThread):
    """后台执行模型搜索；result_signal 发射搜索结果 dict（失败时含 error）。"""

    result_signal = pyqtSignal(object)

    def __init__(self, source, keyword, page, page_size, parent=None):
        super().__init__(parent)
        self._source = source
        self._keyword = keyword
        self._page = page
        self._page_size = page_size

    def run(self):
        try:
            result = model_sources.search_models(
                self._source, self._keyword, self._page, self._page_size
            )
        except Exception as e:  # 网络/解析异常不应杀死线程，回传给 UI 展示
            result = {"error": str(e)}
        self.result_signal.emit(result)


class FileListWorker(QThread):
    """后台执行模型文件列表请求；result_signal 发射 (model_id, files or None)。"""

    result_signal = pyqtSignal(str, object)

    def __init__(self, source, model_id, parent=None):
        super().__init__(parent)
        self._source = source
        self._model_id = model_id

    def run(self):
        try:
            files = model_sources.list_model_files(self._source, self._model_id)
        except Exception as e:
            files = None
            error(f"FileListWorker 请求失败 {self._model_id}: {e}")
        self.result_signal.emit(self._model_id, files)
