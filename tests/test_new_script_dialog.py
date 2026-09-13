"""NewScriptDialog.get_config 测试（traps #9 的回归测试）。

复现路径：--host 是 QComboBox，get_config 若对下拉控件调 .text() 会
AttributeError 崩溃 → 新建脚本功能完全不可用。
必须以 offscreen 平台运行（无真实显示环境的服务器/CI 同样可跑）。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.dialogs.new_script_dialog import NewScriptDialog

_app = QApplication.instance() or QApplication(sys.argv)


class TestGetConfig(unittest.TestCase):
    def setUp(self):
        self.dialog = NewScriptDialog()

    def test_get_config_no_crash(self):
        """勾选全部参数组后 get_config 不崩溃且能取到 --host 下拉值。"""
        for cb in self.dialog.checkboxes.values():
            cb.setChecked(True)
        config = self.dialog.get_config()  # v1.5.0 在此 AttributeError
        # host 组默认勾选且值为三选项之一（不因控件类型而丢失）
        self.assertIn(config.get("host"), ("127.0.0.1", "0.0.0.0"))

    def test_host_combo_value_not_widget_text(self):
        """下拉值应为 currentData（实际 IP），而非控件显示文本。"""
        for cb in self.dialog.checkboxes.values():
            cb.setChecked(True)
        widget = self.dialog.value_inputs["host"]
        widget.setCurrentIndex(0)  # 仅本机 (127.0.0.1)
        config = self.dialog.get_config()
        self.assertEqual(config.get("host"), "127.0.0.1")

    def test_unchecked_excluded(self):
        for cb in self.dialog.checkboxes.values():
            cb.setChecked(False)
        config = self.dialog.get_config()
        self.assertEqual(config, {})


class TestGetName(unittest.TestCase):
    """脚本名称行：默认名来自所选模型（自动命名），可修改（v1.9.1）。"""

    def test_default_name_prefilled(self):
        dialog = NewScriptDialog(default_name="MiniCPM5-2B")
        self.assertEqual(dialog.get_name(), "MiniCPM5-2B")

    def test_name_editable(self):
        dialog = NewScriptDialog(default_name="MiniCPM5-2B")
        dialog.name_edit.setText("我的模型")
        self.assertEqual(dialog.get_name(), "我的模型")

    def test_empty_name(self):
        dialog = NewScriptDialog(default_name="MiniCPM5-2B")
        dialog.name_edit.setText("   ")
        self.assertEqual(dialog.get_name(), "")


if __name__ == "__main__":
    unittest.main()
