"""脚本绑定模型（核心改动）测试：derive_name 自动命名 + get_script_for_model 按模型查脚本
+ 模型下拉与当前模型路径的一致性（combo_index_for）。"""
import json
import os
import shutil
import tempfile
import unittest

from model.script import ScriptEntry
from service.script_service import ScriptService, name_model_score
from ui.app import combo_index_for


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
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

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


class TestNameModelScore(unittest.TestCase):
    """脚本名与模型的契合度：用于同模型多绑定时挑出"不是别的模型"的名字。"""

    def test_full_match(self):
        self.assertEqual(
            name_model_score("Mini CPM5-2B", "C:/modelscope/MiniCPM5-2B-F16.gguf"), 1.0)

    def test_zero_for_foreign_name(self):
        self.assertEqual(
            name_model_score("gemma-4-E4B", "C:/modelscope/MiniCPM5-2B-F16.gguf"), 0.0)

    def test_hash_suffix_dilutes_score(self):
        score = name_model_score(
            "MiniCPM5-2B-F16_1edbd53f", "C:/modelscope/MiniCPM5-2B-F16.gguf")
        self.assertTrue(0.0 < score < 1.0)

    def test_empty_inputs(self):
        self.assertEqual(name_model_score("", "C:/m/a.gguf"), 0.0)
        self.assertEqual(name_model_score("a", ""), 0.0)


class TestMigrateBindings(unittest.TestCase):
    """启动清理：同模型多绑定只留最契合的一条；model_path 以 .bat 的 -m 为准。"""

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

    def _write(self, svc, name, model_path, saved_at="2026-01-01T00:00:00", entry_path=None):
        """同时落 .bat（-m 为 entry_path 或 model_path）与 scripts.json 条目。"""
        mp = entry_path if entry_path is not None else model_path
        bat = svc.get_script_path(name)
        with open(bat, "w", encoding="utf-8") as f:
            f.write(f'@echo off\nllama-server.exe ^\n-m "{mp}" ^\n--port 8080')
        data = {"scripts": svc._load_config_data()["scripts"]}
        data["scripts"].append({
            "name": name, "content": "", "saved_at": saved_at,
            "model_path": model_path, "pinned": False,
        })
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _names(self, svc):
        data = svc._load_config_data()
        return [d.get("name") for d in data["scripts"]]

    def test_prunes_duplicates_keeping_best_named(self):
        """MiniCPM5 三方混战：保留名字契合的，淘汰错位/冗余的并备份 .bat。"""
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        self._write(svc, "Mini CPM5-2B", mp, saved_at="2026-01-01T00:00:01")
        self._write(svc, "gemma-4-E4B", mp, saved_at="2026-01-01T00:00:02")
        self._write(svc, "MiniCPM5-2B-F16_1edbd53f", mp, saved_at="2026-01-01T00:00:03")

        report = svc.migrate_bindings()

        self.assertEqual(self._names(svc), ["Mini CPM5-2B"])
        self.assertEqual(report["removed"], 2)
        self.assertEqual(report["remap"], {"gemma-4-E4B": "Mini CPM5-2B",
                                           "MiniCPM5-2B-F16_1edbd53f": "Mini CPM5-2B"})
        # 淘汰的 .bat 进备份目录，不移除磁盘内容
        self.assertTrue(os.path.exists(os.path.join(self._replaced, "gemma-4-E4B.bat")))
        self.assertFalse(os.path.exists(svc.get_script_path("gemma-4-E4B")))
        self.assertTrue(os.path.exists(svc.get_script_path("Mini CPM5-2B")))

    def test_corrects_model_path_from_bat(self):
        """json 的 model_path 与实际 .bat 的 -m 脱节时，以 .bat 为准。"""
        svc = self._service()
        self._write(svc, "Qwen3.8-27B", "C:/wrong/gemma.gguf",
                    entry_path="C:/right/Qwen3.8-27B-Q4_0.gguf")

        report = svc.migrate_bindings()

        self.assertEqual(report["corrected"], 1)
        self.assertEqual(self._names(svc), ["Qwen3.8-27B"])
        self.assertEqual(svc._load_config_data()["scripts"][0]["model_path"],
                         "C:/right/Qwen3.8-27B-Q4_0.gguf")

    def test_pinned_wins_when_score_ties(self):
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        self._write(svc, "aaaa", mp, saved_at="2026-01-01T00:00:09")
        self._write(svc, "bbbb", mp, saved_at="2026-01-01T00:00:01")
        data = svc._load_config_data()
        data["scripts"][1]["pinned"] = True
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        svc.migrate_bindings()

        self.assertEqual(self._names(svc), ["bbbb"])

    def test_idempotent_and_noop_when_clean(self):
        svc = self._service()
        self._write(svc, "Mini CPM5-2B", "C:/modelscope/MiniCPM5-2B-F16.gguf")
        self._write(svc, "Other", "C:/modelscope/other.gguf")

        report = svc.migrate_bindings()

        self.assertEqual(report["removed"], 0)
        self.assertEqual(report["corrected"], 0)
        self.assertEqual(sorted(self._names(svc)), ["Mini CPM5-2B", "Other"])

    def test_drops_entry_without_bat(self):
        """json 有条目但 .bat 已不存在：顺手清掉（load_scripts 本就忽略）。"""
        svc = self._service()
        self._write(svc, "Keep", "C:/modelscope/keep.gguf")
        data = svc._load_config_data()
        data["scripts"].append({"name": "Ghost", "content": "", "saved_at": "",
                                "model_path": "C:/modelscope/ghost.gguf", "pinned": False})
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        svc.migrate_bindings()

        self.assertEqual(self._names(svc), ["Keep"])


class TestBindingUniqueness(unittest.TestCase):
    """写入端不变量：同一模型路径只允许一条绑定（否则读端会取到别的脚本名）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _service(self):
        svc = ScriptService()
        svc.scripts_dir = self._tmp
        svc.config_file = os.path.join(self._tmp, "scripts.json")
        svc.replaced_dir = os.path.join(self._tmp, "replaced")
        return svc

    def test_same_model_different_names_collapses_to_one(self):
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        svc.save_script(ScriptEntry(name="old name", content="a", model_path=mp))
        svc.save_script(ScriptEntry(name="new name", content="b", model_path=mp))

        names = [d["name"] for d in svc._load_config_data()["scripts"]]
        self.assertEqual(names, ["new name"])
        # 改名后旧 .bat 应清掉，避免变成孤儿脚本又冒出来
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "old_name.bat")))

    def test_different_models_keep_separate_entries(self):
        svc = self._service()
        svc.save_script(ScriptEntry(name="a", content="x", model_path="C:/m/a.gguf"))
        svc.save_script(ScriptEntry(name="b", content="y", model_path="C:/m/b.gguf"))

        self.assertEqual(len(svc._load_config_data()["scripts"]), 2)

    def test_get_prefers_entry_whose_name_matches_model(self):
        """残留多条绑定时（迁移未覆盖的极端情况），读端也应挑对名字。"""
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        svc.save_script(ScriptEntry(name="gemma-4-E4B", content="x", model_path=mp))
        data = svc._load_config_data()
        data["scripts"].append({"name": "Mini CPM5-2B", "content": "y", "saved_at": "",
                                "model_path": mp, "pinned": False})
        with open(svc.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        with open(os.path.join(self._tmp, "Mini_CPM5-2B.bat"), "w", encoding="utf-8") as f:
            f.write(f'-m "{mp}"')

        got = svc.get_script_for_model(mp)

        self.assertEqual(got.name, "Mini CPM5-2B")


class TestResetScript(unittest.TestCase):
    """「重置参数」：把该模型已保存的脚本整版换回默认参数版（仍只一条绑定）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _service(self):
        svc = ScriptService()
        svc.scripts_dir = self._tmp
        svc.config_file = os.path.join(self._tmp, "scripts.json")
        svc.replaced_dir = os.path.join(self._tmp, "replaced")
        return svc

    def test_keeps_bound_name_and_replaces_content(self):
        """已绑定时沿用原名字（改名会让运行中清单/pids.json 错位）。"""
        svc = self._service()
        mp = "C:/modelscope/MiniCPM5-2B-F16.gguf"
        svc.save_script(ScriptEntry(name="Mini CPM5-2B", content="旧参数",
                                    model_path=mp))

        name = svc.reset_script(mp, "默认参数")

        self.assertEqual(name, "Mini CPM5-2B")
        self.assertEqual(svc.get_script_for_model(mp).name, "Mini CPM5-2B")
        self.assertEqual(svc.load_script_content("Mini CPM5-2B"), "默认参数")
        self.assertEqual(len(svc._load_config_data()["scripts"]), 1)

    def test_unbound_uses_derived_name(self):
        svc = self._service()
        mp = "C:/modelscope/whatever.gguf"

        name = svc.reset_script(mp, "默认参数")

        self.assertEqual(name, ScriptEntry.derive_name(mp))
        self.assertEqual(len(svc._load_config_data()["scripts"]), 1)

    def test_no_extra_entry_for_repeated_reset(self):
        svc = self._service()
        mp = "C:/modelscope/whatever.gguf"
        svc.reset_script(mp, "第一版")
        svc.reset_script(mp, "第二版")

        self.assertEqual(len(svc._load_config_data()["scripts"]), 1)
        self.assertEqual(svc.load_script_content(svc.get_script_for_model(mp).name),
                         "第二版")


class TestComboIndexFor(unittest.TestCase):
    """模型下拉定位：重启后下拉必须落在当前模型上（traps #42）。"""

    def test_backslash_scan_matches_forward_slash_config(self):
        # 真因：扫描结果是 os.path.join 的反斜线，配置存的是正斜线
        scan = ["C:\\modelscope\\a.gguf", "C:\\modelscope\\b.gguf"]
        self.assertEqual(combo_index_for(scan, "C:/modelscope/b.gguf"), 1)

    def test_case_insensitive(self):
        self.assertEqual(combo_index_for(["C:/Models/A.gguf"], "c:/models/a.gguf"), 0)

    def test_not_in_list_returns_minus_one(self):
        self.assertEqual(combo_index_for(["C:/m/a.gguf"], "C:/m/other.gguf"), -1)

    def test_blank_targets(self):
        self.assertEqual(combo_index_for(["C:/m/a.gguf"], ""), -1)
        self.assertEqual(combo_index_for(["C:/m/a.gguf"], None), -1)
        self.assertEqual(combo_index_for([], "C:/m/a.gguf"), -1)


if __name__ == "__main__":
    unittest.main()