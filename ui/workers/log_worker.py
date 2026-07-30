"""服务日志读取工作线程。"""
import re

from PyQt6.QtCore import QThread, pyqtSignal


class LogWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    server_ready_signal = pyqtSignal(str)
    tps_signal = pyqtSignal(float)

    def __init__(self, bat_path, process_service):
        super().__init__()
        self.bat_path = bat_path
        self.process_service = process_service
        self._running = False
        self._url_emitted = False
        self._url_pattern = re.compile(r"https?://\d+\.\d+\.\d+\.\d+:\d+")
        self._tps_pattern = re.compile(r"([\d.]+)\s+tokens?\s+per\s+second")

    def run(self):
        self._running = True
        result = self.process_service.start_script(self.bat_path)
        if result["success"]:
            self.log_signal.emit(f"进程已启动，PID: {result['pid']}")
        else:
            self.log_signal.emit(f"启动失败: {result.get('error', '未知错误')}")

        while self._running:
            line = self.process_service.read_output()
            if line:
                if not self._url_emitted:
                    m = self._url_pattern.search(line)
                    if m:
                        self._url_emitted = True
                        self.server_ready_signal.emit(m.group())
                tps_m = self._tps_pattern.search(line)
                if tps_m:
                    try:
                        self.tps_signal.emit(float(tps_m.group(1)))
                    except ValueError:
                        pass
                self.log_signal.emit(line)
            if not self.process_service.is_process_alive():
                break
            self.msleep(200)

        self.log_signal.emit("进程已结束")
        self.finished_signal.emit()
