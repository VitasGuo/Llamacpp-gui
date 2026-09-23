"""脚本绑定模型（核心改动）测试：derive_name 自动命名 + get_script_for_model 按模型查脚本。"""
import os
import tempfile
import unittest

from model.script import ScriptEntry
from service.script_service import ScriptService


class TestDeriveName(unittest.TestCase):
    def test_uses_model_filename_no_ext(self):
        self.assertTrue(ScriptEntry.derive_name("/m/m.gguf").startswith("m_"))

    def test_sanitizes_spaces_in_model_filename(self):
        self.assertTrue(ScriptEntry.derive_name("/m/My Model.gguf").startswith("My_Model_"))

    def test_stable_for_same_path(self):
        self.assertEqual(
            ScriptEntry.derive_name("C:/models/a.gguf"),
            ScriptEntry.derive_name("C:/models/a.gguf"),
        )

    def test_differs_for_same_basename_diff_dir(self):
        a = ScriptEntry.derive_name("C:/x/a.gguf")
        b = ScriptEntry.derive_name("C:/y/a.gguf")
        self.assertNotEqual(a, b)

    def test_auto_name_from_model_path_on_init(self):
        e = ScriptEntry(model_path="C:/m/b.gguf")
        self.assertEqual(e.name, ScriptEntry.derive_name("C:/m/b.gguf"))


class TestGetScriptForModel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def _service(self):
        svc = ScriptService()
        svc.scripts_dir = self._tmp
        svc.config_file = os.path.join(self._tmp, "scripts.json")
        return svc

    def test_get_by_model_after_save(self):
        svc = self._service()
        svc.save_script(ScriptEntry(name="derived", content="x", model_path="C:/m/a.gguf"))
        got = svc.get_script_for_model("C:/m/a.gguf")
        self.assertIsNotNone(got)
        self.assertEqual(got.model_path, "C:/m/a.gguf")

    def test_get_is_slash_agnostic(self):
        svc = self._service()
        svc.save_script(ScriptEntry(name="derived", content="x", model_path="C:/m/a.gguf"))
        # 反斜线形式也能命中（归一到正斜线比较）
        self.assertIsNotNone(svc.get_script_for_model("C:\\m\\a.gguf"))

    def test_get_is_case_insensitive(self):
        """Windows 路径不区分大小写：不同大小写选择命中同一绑定。"""
        svc = self._service()
        svc.save_script(ScriptEntry(name="derived", content="x", model_path="C:/Models/Mini.gguf"))
        self.assertIsNotNone(svc.get_script_for_model("c:/models/MINI.gguf"))

    def test_get_none_for_unbound_model(self):
        svc = self._service()
        svc.save_script(ScriptEntry(name="derived", content="x", model_path="C:/m/a.gguf"))
        self.assertIsNone(svc.get_script_for_model("C:/m/other.gguf"))


if __name__ == "__main__":
    unittest.main()