"""脚本存活状态轮询工作线程。

process_service.alive_pids() 会 spawn 一次 tasklist 全进程枚举，在系统繁忙
（如 llama-server 加载大模型时）可能耗时 1~3s。若在主线程同步执行，每 2s 的
轮询会让 UI 主线程反复阻塞 → 标题栏"未响应"。故放到 QThread 后台执行，
结果经信号回传 UI 线程刷新。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from utils.logger import error


class StatusPollWorker(QThread):
    alive_signal = pyqtSignal(list)  # 存活 pid 列表（回传 UI 线程）

    def __init__(self, pids, process_service):
        super().__init__()
        self._pids = list(pids)
        self._process_service = process_service

    def run(self):
        try:
            alive = self._process_service.alive_pids(self._pids)
        except Exception as e:
            error(f"状态轮询 alive_pids 失败: {e}")
            alive = set()
        self.alive_signal.emit(list(alive))
