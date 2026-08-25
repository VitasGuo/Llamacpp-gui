"""t/s 历史数据后台加载工作线程。

7 天历史数据量很大（约 12 万行 JSONL/服务），在 UI 线程解析+降采样会明显
卡顿（性能 #3），故放到 QThread 后台执行；完成后经信号把 [{ts, server, tps}]
回传给 UI 线程绘点。
"""

from PyQt6.QtCore import QThread, pyqtSignal

from utils.logger import error


class HistoryLoadWorker(QThread):
    """后台加载历史 t/s 数据：run() 调 monitor_service.load_history(seconds)，
    完成后 emit result_signal(points)。

    run() 内任何异常都记日志后吞掉、保证线程正常结束（QThread 内置
    finished 信号必然发出）——UI 层据此兜底恢复"刷新"按钮；
    异常时 result_signal 不发出，历史图保留旧数据（t18）。
    """

    result_signal = pyqtSignal(list)

    def __init__(self, monitor_service, seconds):
        super().__init__()
        self.monitor_service = monitor_service
        self.seconds = seconds

    def run(self):
        try:
            points = self.monitor_service.load_history(self.seconds)
        except Exception as e:
            error(f"加载 t/s 历史失败（范围={self.seconds}s）: {e}")
            return  # 不向外抛：让 finished 正常发出，UI 层兜底恢复按钮
        self.result_signal.emit(points)
