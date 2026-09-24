"""本地模型文件服务测试：列表（大小/mmproj 标记）、配对视觉投影、删除（逐项容错）。"""
import os
import shutil
import tempfile
import unittest

from service.model_file_service import delete_model_files, pair_mmproj
from service.model_scanner import list_local_models


def _write(path, size):
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    return path


class TestListLocalModels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_invalid_dir_returns_empty(self):
        self.assertEqual(list_local_models(""), [])
        self.assertEqual(list_local_models(os.path.join(self._tmp, "nope")), [])

    def test_empty_dir(self):
        self.assertEqual(list_local_models(self._tmp), [])

    def test_fields_and_mmproj_flag(self):
        _write(os.path.join(self._tmp, "B-Model.gguf"), 2048)
        _write(os.path.join(self._tmp, "A-mmproj-Q8_0.gguf"), 512)

        models = list_local_models(self._tmp)

        # 按文件名排序（不区分大小写）
        self.assertEqual([m["name"] for m in models],
                         ["A-mmproj-Q8_0.gguf", "B-Model.gguf"])
        self.assertTrue(models[0]["is_mmproj"])
        self.assertFalse(models[1]["is_mmproj"])
        self.assertEqual(models[1]["size"], 2048)
        self.assertIsNotNone(models[1]["mtime"])
        # 路径统一正斜线（traps #42：路径比对必须同一规范形式）
        self.assertNotIn("\\", models[1]["path"])

    def test_recursive_subdir_found(self):
        sub = os.path.join(self._tmp, "models", "org", "repo")
        os.makedirs(sub)
        _write(os.path.join(sub, "m.gguf"), 16)
        self.assertEqual([m["name"] for m in list_local_models(self._tmp)], ["m.gguf"])


class TestPairMmproj(unittest.TestCase):
    def _models(self, *paths):
        return [{"path": p, "name": os.path.basename(p),
                 "is_mmproj": "mmproj" in os.path.basename(p).lower()} for p in paths]

    def test_pairs_same_dir(self):
        main = "C:/m/Mini.gguf"
        mm = "C:/m/Mini-mmproj-F16.gguf"
        self.assertEqual(pair_mmproj(main, self._models(main, mm)), mm)

    def test_ignores_other_dirs(self):
        main = "C:/m/Mini.gguf"
        mm = "C:/other/Mini-mmproj-F16.gguf"
        self.assertEqual(pair_mmproj(main, self._models(main, mm)), "")

    def test_no_mmproj_returns_empty(self):
        main = "C:/m/Mini.gguf"
        self.assertEqual(pair_mmproj(main, self._models(main)), "")
        self.assertEqual(pair_mmproj("", self._models(main)), "")

    def test_mmproj_input_has_no_pair(self):
        # 视觉投影自身不配对（避免把别的 mmproj 当成它的配对）
        mm = "C:/m/a-mmproj.gguf"
        self.assertEqual(pair_mmproj(mm, self._models(mm)), "")

    def test_picks_first_by_name_when_multiple(self):
        main = "C:/m/Mini.gguf"
        a = "C:/m/A-mmproj.gguf"
        b = "C:/m/B-mmproj.gguf"
        self.assertEqual(pair_mmproj(main, self._models(main, b, a)), a)


class TestDeleteModelFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_deletes_and_reports_freed(self):
        a = _write(os.path.join(self._tmp, "a.gguf"), 1024)
        b = _write(os.path.join(self._tmp, "b.gguf"), 2048)

        result = delete_model_files([a, b])

        self.assertEqual(result["errors"], [])
        self.assertEqual(sorted(result["deleted"]), sorted([a, b]))
        self.assertEqual(result["freed"], 3072)
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))

    def test_idempotent_for_missing_file(self):
        a = _write(os.path.join(self._tmp, "a.gguf"), 1024)
        delete_model_files([a])

        result = delete_model_files([a])

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["deleted"], [a])
        self.assertEqual(result["freed"], 0)  # 已不存在：不计释放量

    def test_single_failure_does_not_stop_others(self):
        keep = _write(os.path.join(self._tmp, "ok.gguf"), 512)
        blocked = os.path.join(self._tmp, "adir.gguf")
        os.makedirs(blocked)  # 目录无法 os.remove → 收集错误但不中断

        result = delete_model_files([blocked, keep])

        self.assertEqual(result["deleted"], [keep])
        self.assertEqual(result["freed"], 512)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("adir.gguf", result["errors"][0])
        self.assertFalse(os.path.exists(keep))

    def test_empty_input(self):
        result = delete_model_files([])
        self.assertEqual(result, {"freed": 0, "deleted": [], "errors": []})


if __name__ == "__main__":
    unittest.main()