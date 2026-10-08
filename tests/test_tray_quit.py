"""托盘退出/重启的 closeEvent 回归测试（traps #44）。

背景：窗口隐藏在托盘时（常态），`close()` 被接受后 Qt 不发 lastWindowClosed
（该信号只在"可见窗口被关闭"时触发），app.exec() 永不返回——表现为
"托盘点退出没反应 / 点重启旧实例不退"。修复：closeEvent 清理完显式
`QApplication.instance().quit()`。

不实例化完整 MainWindow（重、有真实副作用），而是打桩依赖后直接调用
真实的 `MainWindow.closeEvent`，用"exec() 是否被请求退出"断言行为：
- 强制退出（托盘退出/重启）：closeEvent 必须请求事件循环退出（exec 返回 0）
- 托盘最小化（点 X 关闭按钮）：closeEvent 必须 ignore 且不退出事件循环
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMainWindow

from ui.app import MainWindow

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GUARD_EXIT_CODE = 99  # 兜底退出码：事件循环未被 quit() 时由守卫定时器触发
GUARD_MS = 2000


class _StubPoller:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def wait(self, ms):
        pass


class _StubMonitor:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _ProbeWindow(MainWindow):
    """跳过 MainWindow 重型 __init__（服务/桥/托盘等真实副作用），
    只为 closeEvent 提供 isinstance 保障与打桩依赖。"""

    def __init__(self):
        QMainWindow.__init__(self)
        self.tray_icon = None
        self._force_quit = False
        self._status_poller = _StubPoller()
        self._ts_probe_worker = None
        self.log_workers = []
        self._bridge_server = None
        self.monitor_service = _StubMonitor()


def _make_stub_window():
    app = QApplication.instance() or QApplication([])
    return app, _ProbeWindow()


def _exec_with_guard(app):
    """跑事件循环：被 quit() 则返回 0；否则 GUARD_MS 后守卫退出返回 99。"""
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(lambda: app.exit(GUARD_EXIT_CODE))
    guard.start(GUARD_MS)
    return app.exec()


class TestTrayQuitCloseEvent(unittest.TestCase):
    def test_force_quit_exits_event_loop(self):
        """托盘"退出/重启"路径：窗口隐藏（从未 show），closeEvent 必须请求退出。

        修复前：隐藏窗口 close() 后无 lastWindowClosed，exec() 永不返回。
        注意 quit() 必须在事件循环内调用（exec 前 exit 无效），故用
        singleShot(0) 驱动 close()，与真实托盘点击的执行时机一致。
        """
        app, win = _make_stub_window()
        win._force_quit = True  # _quit_app / _restart_app 先置位再 close()
        QTimer.singleShot(0, win.close)  # 在事件循环内执行（真实点击时机）
        # 关键断言：事件循环被请求退出（快速返回 0 而非守卫兜底 99）
        self.assertEqual(_exec_with_guard(app), 0)
        # 清理路径确实执行
        self.assertTrue(win._status_poller.stopped)
        self.assertTrue(win.monitor_service.stopped)

    def test_tray_minimize_does_not_exit(self):
        """点窗口 X（非强制退出且有托盘）：隐藏+ignore，事件循环不得退出。"""
        app, win = _make_stub_window()
        win.tray_icon = object()  # 托盘存在的哨兵（closeEvent 只判 None）
        ev = QCloseEvent()
        MainWindow.closeEvent(win, ev)
        self.assertFalse(ev.isAccepted())
        self.assertFalse(win.isVisible())
        # 未请求退出：exec 由守卫兜底退出（返回 99）
        self.assertEqual(_exec_with_guard(app), GUARD_EXIT_CODE)


if __name__ == "__main__":
    unittest.main()
