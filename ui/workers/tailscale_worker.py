"""Tailscale IP 后台探测线程。

service.tailscale 的探测缓存过期后会同步执行 tailscale 子进程
（timeout 3s × 2 个候选路径，最坏 ~6s），在 GUI 线程调用会冻结界面。
本 worker 把探测挪到后台，结果经信号回传；GUI 侧只读缓存值、
绝不同步探测（配合主窗口 _ts_ip_fast 使用）。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from service.tailscale import get_tailscale_ipv4


class TailscaleProbeWorker(QThread):
    """后台探测本机 Tailscale IPv4；ip_signal 发射结果（空串=未检测到）。"""

    ip_signal = pyqtSignal(str)

    def run(self):
        self.ip_signal.emit(get_tailscale_ipv4())
