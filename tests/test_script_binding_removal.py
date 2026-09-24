"""删除本地模型时的绑定清理测试（remove_binding_for_model）。

场景：本地 .gguf 被移除后，指向它的启动脚本（.bat + scripts.json 条目）必须
一起清掉，否则留下"脚本还在、模型没了"的死绑定。.bat 不物理删除，走
data/scripts_replaced 备份（与 migrate_bindings 的淘汰策略一致）。
"""
import json
import os
import shutil
import tempfile
import unittest

from model.script import ScriptEntry
from service.script_service import ScriptService


class TestRemoveBindingForModel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._scripts = os.path.join(self._tmp, "scripts")
        self._replaced = os.path.join(self._tmp, "replaced")
        os.makedirs(self._scripts)

    def _service(self):
        svc = ScriptService()
        svc.scripts_dir = self._scripts
        svc.config_file = os.path.join(self._tmp, "scripts.json")
        svc.replaced_dir = self._replaced
        return svc

    def _names(self, svc):
        return [d.get("name") for d in svc._load_config_data()["scripts"]]

    def _write_orphan_bat(self, svc, name, model_path):
        with open(svc.get_script_path(name), "w", encoding="utf-8") as f:
            f.write(f'@echo off\nllama-server.exe ^\n-m "{model_path}"')
        return svc.get_script_path(name)

    def test_removes_json_entry_and_backs_up_bat(self):
        svc = self._service()
        mp = "C:/modelscope/Mini.gguf"
        svc.save_script(ScriptEntry(name="Mini", content="x", model_path=mp))

        result = svc.remove_binding_for_model(mp)

        self.assertEqual(result["names"], ["Mini"])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self._names(svc), [])
        self.assertFalse(os.path.exists(svc.get_script_path("Mini")))
        self.assertTrue(os.path.exists(os.path.join(self._replaced, "Mini.bat")))
        self.assertIsNone(svc.get_script_for_model(mp))

    def test_removes_orphan_bat_not_in_json(self):
        """目录里有 .bat 但 scripts.json 无条目（旧版本遗留）也要清。"""
        svc = self._service()
        mp = "C:/modelscope/Orphan.gguf"
        bat = self._write_orphan_bat(svc, "Orphan", mp)
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump({"scripts": []}, f, ensure_ascii=False)

        result = svc.remove_binding_for_model(mp)

        self.assertEqual(result["names"], ["Orphan"])
        self.assertEqual(result["removed"], 0)  # json 本来就没有条目
        self.assertFalse(os.path.exists(bat))
        self.assertTrue(os.path.exists(os.path.join(self._replaced, "Orphan.bat")))

    def test_removes_all_entries_of_same_model(self):
        """同一模型的脏数据多条绑定（历史遗留）应一次清干净。"""
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        svc.save_script(ScriptEntry(name="Mini CPM5-2B", content="a", model_path=mp))
        data = svc._load_config_data()
        data["scripts"].append({"name": "gemma-4-E4B", "content": "b", "saved_at": "",
                                "model_path": mp, "pinned": False})
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        result = svc.remove_binding_for_model(mp)

        self.assertEqual(sorted(result["names"]), ["Mini CPM5-2B", "gemma-4-E4B"])
        self.assertEqual(self._names(svc), [])

    def test_unbound_model_is_noop_and_keeps_json(self):
        svc = self._service()
        other = "C:/modelscope/Other.gguf"
        svc.save_script(ScriptEntry(name="Other", content="x", model_path=other))
        before = svc._load_config_data()

        result = svc.remove_binding_for_model("C:/modelscope/NotBound.gguf")

        self.assertEqual(result, {"names": [], "removed": 0, "backup_dir": ""})
        self.assertEqual(self._names(svc), ["Other"])
        self.assertEqual(svc._load_config_data()["scripts"], before["scripts"])

    def test_matches_by_derived_name_when_model_path_stale(self):
        """json 的 model_path 是旧值（脱节）时，按 derive_name 兜底命中。"""
        svc = self._service()
        mp = "C:/modelscope/Fresh.gguf"
        name = ScriptEntry.derive_name(mp)
        svc.save_script(ScriptEntry(name=name, content="x",
                                    model_path="C:/old/Stale.gguf"))

        result = svc.remove_binding_for_model(mp)

        self.assertEqual(result["names"], [name])
        self.assertEqual(self._names(svc), [])

    def test_repeated_removal_is_idempotent(self):
        svc = self._service()
        mp = "C:/modelscope/Mini.gguf"
        svc.save_script(ScriptEntry(name="Mini", content="x", model_path=mp))
        svc.remove_binding_for_model(mp)

        result = svc.remove_binding_for_model(mp)

        self.assertEqual(result["names"], [])
        # 备份目录里的 .bat 保留（供人工找回），不因重复调用被删
        self.assertTrue(os.path.exists(os.path.join(self._replaced, "Mini.bat")))


if __name__ == "__main__":
    unittest.main()