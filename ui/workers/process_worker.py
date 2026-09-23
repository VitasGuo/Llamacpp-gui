"""进程清理后台工作线程。

stop_by_name 逐关键字跑 tasklist + 逐 PID taskkill（多实例时秒级），
不能在 GUI 线程执行；结果经信号回传 UI 层统一重置状态。
"""
from PyQt6.QtCore import QThread, pyqtSignal


class KillAllLlamaWorker(QThread):
    """后台结束本机全部 llama-server.exe / main.exe 进程。

    killed_signal 发射被结束进程列表 [{name, pid}]。
    """

    killed_signal = pyqtSignal(list)

    def __init__(self, process_service, parent=None):
        super().__init__(parent)
        self._process_service = process_service

    def run(self):
        killed = self._process_service.stop_by_name()
        self.killed_signal.emit(killed or [])
