"""service.script_builder 纯函数测试。"""
import os
import unittest

from service.script_builder import (
    extract_port,
    extract_host,
    find_mmproj,
    build_bat_content,
)


class TestExtractPort(unittest.TestCase):
    def test_space_form(self):
        self.assertEqual(extract_port("--port 8087", 8080), 8087)

    def test_equals_form(self):
        self.assertEqual(extract_port("--port=9930", 8080), 9930)

    def test_missing_returns_default(self):
        self.assertEqual(extract_port("llama-server.exe -m x.gguf", 8080), 8080)

    def test_not_first_arg(self):
        self.assertEqual(extract_port("--host 1.2.3.4 --port 1234", 8080), 1234)


class TestExtractHost(unittest.TestCase):
    def test_space_form(self):
        self.assertEqual(extract_host("--host 100.96.74.91", "127.0.0.1"), "100.96.74.91")

    def test_equals_form(self):
        self.assertEqual(extract_host("--host=0.0.0.0", "127.0.0.1"), "0.0.0.0")

    def test_missing_returns_default(self):
        self.assertEqual(extract_host("--port 8080", "127.0.0.1"), "127.0.0.1")


class TestBuildBat(unittest.TestCase):
    def test_basic_content(self):
        content = build_bat_content(
            exe_dir="C:\\llama", model_path="C:\\models\\qwen.gguf",
            config={"port": "8080", "host": "127.0.0.1"},
        )
        self.assertIn("llama-server.exe", content)
        self.assertIn("qwen.gguf", content)
        self.assertIn("--port 8080", content)
        self.assertIn("--host 127.0.0.1", content)

    def test_quoted_paths(self):
        content = build_bat_content(
            exe_dir="C:\\a b", model_path="C:\\path with space\\m.gguf",
            config={},
        )
        self.assertIn('"C:\\path with space\\m.gguf"', content)


class TestFindMmproj(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()

    def test_none_when_absent(self):
        self.assertEqual(find_mmproj(os.path.join(self._tmp, "nope.gguf")), "")

    def test_finds_adjacent_mmproj(self):
        model = os.path.join(self._tmp, "model.gguf")
        open(model, "w").close()
        mm = os.path.join(self._tmp, "mmproj-model.gguf")
        open(mm, "w").close()
        self.assertEqual(find_mmproj(model), mm)


if __name__ == "__main__":
    unittest.main()
