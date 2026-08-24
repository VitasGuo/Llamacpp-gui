"""服务日志读取工作线程。"""
import re

from PyQt6.QtCore import QThread, pyqtSignal


class LogWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    server_ready_signal = pyqtSignal(str)
    tps_signal = pyqtSignal(float)

    def __init__(self, bat_path, process_service, script_name="", port=None):
        super().__init__()
        self.bat_path = bat_path
        self.process_service = process_service
        self.script_name = script_name
        self.port = port
        self._running = False
        self._process = None
        self._url_emitted = False
        self._url_pattern = re.compile(r"https?://\d+\.\d+\.\d+\.\d+:\d+")
        self._tps_pattern = re.compile(r"([\d.]+)\s+tokens?\s+per\s+second")

    def run(self):
        self._running = True
        result = self.process_service.start_script(
            self.bat_path, self.script_name, self.port
        )
        if result["success"]:
            self._process = result.get("process")
            self.log_signal.emit(f"进程已启动，PID: {result['pid']}")
        else:
            self.log_signal.emit(f"启动失败: {result.get('error', '未知错误')}")
            # 启动失败直接结束：否则下方循环会以 current_process（可能属于
            # 其他仍在运行的脚本）为回退目标，串读并重复转发其输出
            return

        while self._running:
            # 读自己的进程输出（多服务器并发时不与其他脚本的 worker 互相串扰）
            line = self.process_service.read_output(self._process)
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
            if not self.process_service.is_process_alive(self._process):
                break
            self.msleep(200)

        self.log_signal.emit("进程已结束")
        self.finished_signal.emit()
