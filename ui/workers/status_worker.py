"""脚本运行时状态轮询工作线程。

把 tasklist 存活探测从 GUI 线程移出：GUI 线程每 2s 跑一次 tasklist 会阻塞约
0.5~0.7s，导致界面卡顿。本线程在后台做批量存活探测，结果通过信号回传到
GUI 线程（信号是跨线程队列投递，永不在 GUI 线程执行 tasklist）。
"""
import threading

from PyQt6.QtCore import QThread, pyqtSignal


class StatusPoller(QThread):
    # (runtime: dict, alive_set: set)：dict/set 用 object 传递避免类型约束
    status_refreshed = pyqtSignal(object, object)

    def __init__(self, process_service, interval=2.0, parent=None):
        super().__init__(parent)
        self.process_service = process_service
        self.interval = interval
        self._stop = threading.Event()
        self._poll_now = threading.Event()

    def run(self):
        while True:
            if self._stop.is_set():
                return
            # 到点（interval）或被 request_poll() 唤醒时执行一轮探测
            self._poll_now.wait(self.interval)
            self._poll_now.clear()
            if self._stop.is_set():
                return
            try:
                runtime = self.process_service.load_runtime()
                pids = {
                    e.get("pid") for e in runtime.values()
                    if isinstance(e.get("pid"), int) and e.get("pid") > 0
                }
                alive = self.process_service.alive_pids(pids)
                self.status_refreshed.emit(runtime, alive)
            except Exception:
                # 单次失败下轮重试；轮询线程不得因单次异常而崩溃
                pass

    def request_poll(self):
        """请求一次立即刷新（唤醒休眠中的 wait，不阻塞调用线程）。"""
        self._poll_now.set()

    def stop(self):
        self._stop.set()
        # 唤醒阻塞中的 _poll_now.wait，避免线程停在 wait 上无法退出
        self._poll_now.set()