"""CompactMonitor（主控制页压缩系统监控）冒烟测试。

覆盖：CPU/RAM 更新、servers 推送 t/s、GPU 行动态增删与占位符恢复、
日志回退 t/s 通道、format_uptime 时长格式化。offscreen 平台可跑。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui.monitor_tab import CompactMonitor, format_uptime

_app = QApplication.instance() or QApplication(sys.argv)


class _StubService(QObject):
    metrics_updated = pyqtSignal(dict)

    def __init__(self, gpu=False):
        super().__init__()
        self.gpu = gpu
        self.recorded_tps = []  # update_tps 历史落盘通道的采样记录

    def is_gpu_available(self):
        return self.gpu

    def record_tps(self, name, tps):
        self.recorded_tps.append((name, tps))


def _metrics(cpu=0.0, ram=0.0, gpus=None, servers=None):
    return {
        "cpu": cpu, "ram_percent": ram,
        "ram_used": 8e9, "ram_total": 16e9,
        "gpus": gpus or [], "servers": servers or [],
    }


class TestCompactMonitor(unittest.TestCase):
    def setUp(self):
        self.service = _StubService()
        self.m = CompactMonitor(self.service)

    def test_metrics_updates_cpu_ram(self):
        self.service.metrics_updated.emit(_metrics(cpu=55.0, ram=70.0))
        self.assertEqual(self.m._cpu_label.text(), "55%")
        self.assertIn("70%", self.m._ram_label.text())

    def test_tps_from_servers_push(self):
        self.service.metrics_updated.emit(
            _metrics(servers=[{"name": "m", "ok": True, "tps": 12.3}])
        )
        self.assertIn("12.3", self.m._tps_label.text())

    def test_log_fallback_tps(self):
        self.m.set_focus_script("m")
        self.m.update_tps("m", 5.5)
        self.assertIn("5.5", self.m._tps_label.text())
        # 日志回退通道的采样点同步进历史落盘缓冲
        self.assertEqual(self.service.recorded_tps, [("m", 5.5)])

    def test_focus_filter(self):
        """聚焦脚本优先：非聚焦服务的 t/s 不覆盖标签。"""
        self.m.set_focus_script("m")
        self.m.update_tps("other", 99.0)
        self.assertNotIn("99.0", self.m._tps_label.text())
        self.m.update_tps("m", 3.3)
        self.assertIn("3.3", self.m._tps_label.text())

    def test_gpu_rows_dynamic(self):
        self.service.gpu = True
        self.service.metrics_updated.emit(
            _metrics(gpus=[{"name": "x", "util": 88.0, "mem_used": 3e9,
                            "mem_total": 8e9, "temp": 70.0}])
        )
        self.assertEqual(len(self.m._gpu_rows), 1)
        self.assertEqual(self.m._gpu_rows[0].util_label.text(), "88%")
        self.assertIn("70", self.m._gpu_rows[0].mem_label.text())

    def test_gpu_placeholder_restored(self):
        """GPU 消失→恢复→再消失：占位符能再次恢复（防 regress 占位符丢失）。"""
        self.service.gpu = True
        self.service.metrics_updated.emit(
            _metrics(gpus=[{"name": "x", "util": 1.0, "mem_used": 1,
                            "mem_total": 1, "temp": 40.0}])
        )
        self.service.metrics_updated.emit(_metrics())  # GPU 消失
        self.assertFalse(self.m._gpu_rows)
        self.assertFalse(self.m._gpu_placeholder.isHidden())

    def test_format_uptime(self):
        """运行时长格式化：秒 → HH:MM:SS，非法/负值回退 "--"。"""
        self.assertEqual(format_uptime(0), "00:00:00")
        self.assertEqual(format_uptime(59), "00:00:59")
        self.assertEqual(format_uptime(3600), "01:00:00")
        self.assertEqual(format_uptime(3725), "01:02:05")
        self.assertEqual(format_uptime(90061), "25:01:01")
        self.assertEqual(format_uptime(-3), "--")
        self.assertEqual(format_uptime(None), "--")
        self.assertEqual(format_uptime("abc"), "--")


if __name__ == "__main__":
    unittest.main()