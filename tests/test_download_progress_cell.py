"""下载队列"进度"单元格回归测试（offscreen 可跑）。

背景（traps #2 复发）：QProgressBar 的条内建文本（默认格式 %p%）在本环境
会渲染成乱码——看着像中文字符、数字完全认不出。monitor_tab 早已按
setTextVisible(False) + 旁侧 QLabel 处理，下载队列漏了，故补上并加锁。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar

from ui.model_tab import _make_progress_cell, _set_progress_cell

_app = QApplication.instance() or QApplication(sys.argv)


class TestProgressCell(unittest.TestCase):
    def test_bar_text_hidden(self):
        # 条内建文本必须关闭，否则乱码（traps #2）
        cell = _make_progress_cell(45)  # 必须持有 cell 引用：容器被 GC 会连带删掉子控件
        bar = cell.findChild(QProgressBar)
        self.assertIsNotNone(bar)
        self.assertFalse(bar.isTextVisible())

    def test_label_shows_ascii_digits(self):
        cell = _make_progress_cell(45)
        label = cell.findChild(QLabel)
        self.assertIsNotNone(label)
        self.assertEqual(label.text(), "45%")
        # 只含 ASCII 数字与 %（防中文数字/乱码字形回归）
        self.assertTrue(all(ord(c) < 128 for c in label.text()), label.text())
        self.assertTrue(label.text().rstrip("%").isdigit(), label.text())

    def test_bar_value_matches(self):
        for v in (0, 1, 100):
            cell = _make_progress_cell(v)
            bar = cell.findChild(QProgressBar)
            self.assertEqual(bar.value(), v)
            self.assertEqual(bar.maximum(), 100)

    def test_set_progress_cell_updates_both(self):
        cell = _make_progress_cell(0)
        _set_progress_cell(cell, 73)
        self.assertEqual(cell.findChild(QProgressBar).value(), 73)
        self.assertEqual(cell.findChild(QLabel).text(), "73%")

    def test_set_progress_cell_tolerates_none(self):
        _set_progress_cell(None, 50)  # 行被移除后回调不应崩


if __name__ == "__main__":
    unittest.main()