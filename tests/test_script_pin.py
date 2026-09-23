"""脚本置顶（pinned）字段的持久化测试。"""
import os
import shutil
import tempfile
import unittest

from model.script import ScriptEntry
from service.script_service import ScriptService


class TestPinField(unittest.TestCase):
    def test_default_not_pinned(self):
        self.assertFalse(ScriptEntry(name="x").pinned)

    def test_to_from_dict_roundtrip(self):
        e = ScriptEntry(name="m", content="c", model_path="p", pinned=True)
        self.assertTrue(ScriptEntry.from_dict(e.to_dict()).pinned)

    def test_from_dict_missing_pinned_false(self):
        self.assertFalse(ScriptEntry.from_dict({"name": "m"}).pinned)


class TestPinPersist(unittest.TestCase):
    """ScriptService 保存/加载脚本时 pinned 不丢失（scripts.json upsert）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _service(self):
        svc = ScriptService()
        svc.scripts_dir = self._tmp
        svc.config_file = os.path.join(self._tmp, "scripts.json")
        return svc

    def test_save_then_load_keeps_pinned(self):
        svc = self._service()
        svc.save_script(ScriptEntry(name="m", content="x", pinned=True))
        loaded = svc.load_scripts()
        self.assertEqual(len(loaded), 1)
        self.assertTrue(loaded[0].pinned)

    def test_legacy_json_without_pinned_loads_false(self):
        # 存量 scripts.json 无 pinned 字段 → 默认不置顶（不崩溃）
        import json
        svc = self._service()
        with open(os.path.join(self._tmp, "scripts.json"), "w", encoding="utf-8") as f:
            json.dump({"scripts": [{"name": "old", "content": "@echo off\n", "model_path": ""}]}, f)
        with open(os.path.join(self._tmp, "old.bat"), "w", encoding="utf-8") as f:
            f.write("@echo off\n")
        loaded = svc.load_scripts()
        self.assertEqual(len(loaded), 1)
        self.assertFalse(loaded[0].pinned)


if __name__ == "__main__":
    unittest.main()