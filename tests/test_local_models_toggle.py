"""验证「本地模型管理」按钮在视图切换时文字正确变化（toggle 行为）。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import shutil
import tempfile

from PyQt6.QtWidgets import QApplication

from config.config import Settings
from ui.model_tab import ModelTab


# 模块级别共享一个 QApplication（整个 test session 一个）
_app = QApplication.instance() or QApplication([])


class TestLocalModelsToggle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        # 单例重置：避免污染（通过构造 + 直接覆盖模块单例字段）
        Settings._instance = None
        Settings._instance_file = os.path.join(self._tmp, "app_config.json")
        self.settings = Settings.get_instance()
        self.settings.model_dir = self._tmp
        self.tab = ModelTab()
        # Stub：避免 _show_local_models 启动后台扫描 worker（测试只关心按钮文字）
        self.tab._load_local_models = lambda: None
        # 不持有 worker 引用，避免跨测试干扰
        self.addCleanup(self._cleanup_tab)

    def _cleanup_tab(self):
        try:
            self.tab.deleteLater()
        except Exception:
            pass

    def _btn_text(self):
        return self.tab.local_models_btn.text()

    def test_default_text_is_local_models_manage(self):
        self.assertEqual(self._btn_text(), "本地模型管理")

    def test_text_flips_when_entering_local_view(self):
        self.tab._show_local_models()  # 进入视图 3
        self.assertEqual(self.tab.stack.currentIndex(), 3)
        self.assertEqual(self._btn_text(), "\u2190 返回")

    def test_text_flips_back_when_toggled(self):
        self.tab._show_local_models()  # 进入
        self.tab._show_local_models()  # 返回
        self.assertEqual(self.tab.stack.currentIndex(), 0)
        self.assertEqual(self._btn_text(), "本地模型管理")

    def test_text_syncs_when_switching_to_other_views(self):
        # 进入本地视图
        self.tab._show_local_models()
        self.assertEqual(self._btn_text(), "\u2190 返回")
        # 通过 _clear_search 切到追踪视图
        self.tab._clear_search()
        self.assertEqual(self.tab.stack.currentIndex(), 0)
        self.assertEqual(self._btn_text(), "本地模型管理")


if __name__ == "__main__":
    unittest.main()
