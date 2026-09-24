"""「本地模型」视图的行渲染纯函数测试（无需起窗口）。

行显示逻辑抽成模块级 `_local_model_row` / `_format_mtime`，便于回归：
大小与时间必须可读、（视觉投影）标记不得漏，避免"看不出哪个是 mmproj"。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.model_tab import _format_mtime, _local_model_row


class TestLocalModelRow(unittest.TestCase):
    def test_main_model_has_no_mark(self):
        row = _local_model_row(
            {"name": "MiniCPM5-2B-F16.gguf", "size": 5 * 10 ** 9,
             "mtime": 1758000000.0, "is_mmproj": False})
        self.assertEqual(row["display_name"], "MiniCPM5-2B-F16.gguf")
        self.assertEqual(row["size"], "5.0GB")
        self.assertTrue(row["mtime"].startswith("2025-"))

    def test_mmproj_is_marked(self):
        row = _local_model_row(
            {"name": "MiniCPM5-2B-mmproj-Q8_0.gguf", "size": 600 * 10 ** 6,
             "mtime": 1758000000.0, "is_mmproj": True})
        self.assertTrue(row["display_name"].endswith("（视觉投影）"))
        self.assertEqual(row["size"], "600.0MB")

    def test_missing_fields_fall_back(self):
        row = _local_model_row({})
        self.assertEqual(row["display_name"], "")
        self.assertEqual(row["size"], "-")
        self.assertEqual(row["mtime"], "-")

    def test_size_tiers(self):
        for size, text in ((0, "-"), (999, "1.0KB"), (10 ** 6, "1.0MB"),
                           (10 ** 9, "1.0GB"), (10 ** 12, "1.0TB")):
            self.assertEqual(_local_model_row({"size": size})["size"], text)


class TestFormatMtime(unittest.TestCase):
    def test_none_and_bad_values(self):
        self.assertEqual(_format_mtime(None), "-")
        self.assertEqual(_format_mtime(0), "-")
        self.assertEqual(_format_mtime("oops"), "-")

    def test_format(self):
        self.assertRegex(_format_mtime(1758000000.0),
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()