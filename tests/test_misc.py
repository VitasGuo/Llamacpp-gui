"""版本比较与静态文件防穿越测试。"""
import unittest

from ui.workers.update_workers import compare_semver


class TestCompareSemver(unittest.TestCase):
    def test_basic_ordering(self):
        self.assertEqual(compare_semver("1.5.0", "1.4.2"), 1)
        self.assertEqual(compare_semver("1.4.2", "1.5.0"), -1)
        self.assertEqual(compare_semver("1.5.0", "1.5.0"), 0)

    def test_multi_digit(self):
        self.assertEqual(compare_semver("1.10.0", "1.9.9"), 1)

    def test_prerelease_lower(self):
        self.assertEqual(compare_semver("1.5.0", "1.5.0-beta"), 1)
        self.assertEqual(compare_semver("1.5.0-beta", "1.5.0"), -1)


class TestStaticPathTraversal(unittest.TestCase):
    """_serve_static 的路径判定逻辑（join + normpath + commonpath）。

    安全属性：无论请求路径带多少 ../ 或 ..\\，解析结果必须始终落在
    webui 目录内（不得逃逸）。URL 路径以 / 开头，normpath 会把 .. 收敛
    到盘根；commonpath 再做一次严格兜底。
    """

    def _resolve(self, webui_dir, request_path):
        import os
        safe = os.path.normpath(request_path).strip("/\\")
        filepath = os.path.normpath(os.path.join(webui_dir, safe))
        webui_root = os.path.normpath(webui_dir)
        try:
            return os.path.commonpath([filepath, webui_root]) == webui_root
        except ValueError:
            return False

    def test_normal_file_allowed(self):
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/chat.html"))
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/css/chat.css"))

    def test_dotdot_never_escapes(self):
        # 无论多少层 ../ 或 ..\\，结果都被收敛进 webui（或其内不存在的文件 → 404）
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/../../secret.txt"))
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/a/b/../../../Windows/win.ini"))
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/..\\..\\..\\Windows\\win.ini"))

    def test_subdir_allowed(self):
        self.assertTrue(self._resolve(r"C:\app\ui\chat_webui", "/js/app.js"))


if __name__ == "__main__":
    unittest.main()
