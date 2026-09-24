"""LogWorker 退出前残余输出读取回归测试（纯桩，无需真实子进程）。

背景（traps #40）：LogWorker 主循环每轮只读一行、读到就检查进程存活；
llama-server 加载失败会在退出前一刻集中打印错误行（如 "invalid ggml type"），
此时循环已因进程死亡 break，错误行全部丢失——界面只剩"进程已结束"，
用户看不到任何失败原因。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.workers.log_worker import LogWorker

_app = QApplication.instance() or QApplication(sys.argv)


class _StubProcessService:
    """按脚本吐行、并在指定时刻宣告进程死亡的桩。"""

    def __init__(self, lines, alive_first_n=1):
        self._lines = list(lines)
        self._alive_calls = 0
        self._alive_first_n = alive_first_n
        self.read_count = 0

    def start_script(self, bat_path, script_name="", port=None, host=None):
        return {"success": True, "pid": 1, "process": object()}

    def read_output(self, process=None):
        if self._lines:
            self.read_count += 1
            return self._lines.pop(0)
        return None

    def is_process_alive(self, process=None):
        self._alive_calls += 1
        return self._alive_calls <= self._alive_first_n


def _run_worker(lines, alive_first_n=1):
    svc = _StubProcessService(lines, alive_first_n)
    worker = LogWorker("x.bat", svc, script_name="S")
    emitted = []
    worker.log_signal.connect(emitted.append)
    worker.msleep = lambda _ms: None  # 跳过主循环的 200ms 节流，保持测试瞬时
    worker.run()
    return emitted


class TestLogWorkerDrain(unittest.TestCase):
    def test_final_error_lines_are_not_lost(self):
        lines = ["I init", "W cors", "E invalid ggml type 142", "E exiting"]
        emitted = _run_worker(lines)

        self.assertEqual(emitted, ["进程已启动，PID: 1"] + lines + ["进程已结束"])

    def test_error_line_is_last_before_finished(self):
        emitted = _run_worker(["E boom"])
        self.assertEqual(emitted[-2:], ["E boom", "进程已结束"])

    def test_drain_skips_while_process_alive(self):
        # 进程仍存活时不做阻塞读（否则对健康服务的收尾会卡住线程）
        svc = _StubProcessService(["leftover"], alive_first_n=99)
        worker = LogWorker("x.bat", svc, script_name="S")
        worker._process = object()
        emitted = []
        worker.log_signal.connect(emitted.append)

        worker._drain_remaining()

        self.assertEqual(emitted, [])
        self.assertEqual(svc.read_count, 0)


if __name__ == "__main__":
    unittest.main()