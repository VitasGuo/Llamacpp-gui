"""模型更新追踪 service 的判定逻辑测试（mock 网络，不碰真实文件/网络）。"""
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from service import watchlist_service as ws


def _make_local_model(root, org, repo):
    d = os.path.join(root, "models", org, repo)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "m.gguf"), "w").close()
    return f"{org}/{repo}"


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDiscoverLocalModels(unittest.TestCase):
    def test_extracts_org_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "models", "lmstudio-community", "Qwen3-1.7B-GGUF")
            os.makedirs(d)
            open(os.path.join(d, "m.gguf"), "w").close()
            # 顶层直属 gguf 无法推归属，应被忽略
            open(os.path.join(tmp, "Qwen3.8-27B-Q4_0.gguf"), "w").close()
            found = ws.discover_local_models(tmp)
            self.assertEqual(found, {"lmstudio-community/Qwen3-1.7B-GGUF"})


class TestCheckUpdates(unittest.TestCase):
    """has_update 判定：首次只填基线不误报；远端更大才报更新；单模型失败不中断。"""

    def setUp(self):
        fd, self.cfg = tempfile.mkstemp()
        os.close(fd)

    def _patch(self):
        return mock.patch.object(ws, "WATCHLIST_FILE", self.cfg)

    def test_first_check_sets_baseline_no_update(self):
        watchlist = [{"model_id": "a/b", "last_updated": None, "added_by": "local"}]
        with self._patch(), mock.patch.object(ws, "fetch_last_updated", return_value=100):
            results = ws.check_updates(watchlist)
        self.assertFalse(results[0]["has_update"])
        self.assertEqual(results[0]["last_updated"], 100)
        # 基线已落盘
        with self._patch():
            saved = ws._load_watchlist()
        self.assertEqual(saved[0]["last_updated"], 100)

    def test_newer_remote_marks_update(self):
        watchlist = [{"model_id": "a/b", "last_updated": 100}]
        with self._patch(), mock.patch.object(ws, "fetch_last_updated", return_value=200):
            results = ws.check_updates(watchlist)
        self.assertTrue(results[0]["has_update"])

    def test_same_or_older_no_update(self):
        watchlist = [{"model_id": "a/b", "last_updated": 200}]
        with self._patch(), mock.patch.object(ws, "fetch_last_updated", return_value=200):
            results = ws.check_updates(watchlist)
        self.assertFalse(results[0]["has_update"])

    def test_single_error_not_breaking(self):
        watchlist = [
            {"model_id": "a/b", "last_updated": 100},
            {"model_id": "c/d", "last_updated": 100},
        ]

        def fake(mid):
            if mid == "a/b":
                raise OSError("net down")
            return 300  # c/d 远端 300 > 基线 100 → 有更新

        with self._patch(), mock.patch.object(ws, "fetch_last_updated", side_effect=fake):
            results = ws.check_updates(watchlist)
        self.assertTrue(results[0]["error"])  # 失败项带 error，不中断
        self.assertTrue(results[1]["has_update"])  # 成功项照常判定


class TestIgnoreRemove(unittest.TestCase):
    """移除后本地模型不再被 merge 自动加回（修复"移除没效果"）。"""

    def setUp(self):
        fd, self.cfg = tempfile.mkstemp()
        os.close(fd)
        self.ign_cfg = self.cfg + ".ign"

    def _patch(self):
        """返回可作 with 上下文使用的补丁栈（EnterExitStack）。"""
        stack = ExitStack()
        stack.enter_context(mock.patch.object(ws, "WATCHLIST_FILE", self.cfg))
        stack.enter_context(mock.patch.object(ws, "WATCHLIST_IGNORED_FILE", self.ign_cfg))
        return stack

    def test_remove_then_merge_does_not_readd(self):
        with tempfile.TemporaryDirectory() as tmp:
            mid = _make_local_model(tmp, "org", "repo")
            with self._patch():
                wl = ws.merge_local_models(tmp)
                self.assertEqual(len(wl), 1)
                wl = ws.remove_model(mid, wl)
                self.assertEqual(wl, [])
                # 再次 merge（如同再次进入追踪页）：被忽略，不再加回
                wl = ws.merge_local_models(tmp)
                self.assertEqual(wl, [])

    def test_add_manual_unignores(self):
        with tempfile.TemporaryDirectory() as tmp:
            mid = _make_local_model(tmp, "org", "repo")
            with self._patch():
                ws.merge_local_models(tmp)
                ws.remove_model(mid)
                # 手动重新追踪：解除忽略并重新入库
                wl = ws.add_manual_model(mid)
                self.assertEqual([w["model_id"] for w in wl], [mid])
                # 再 merge 不会再跳过它（已解除忽略）
                wl = ws.merge_local_models(tmp, wl)
                self.assertEqual(sum(1 for w in wl if w["model_id"] == mid), 1)


if __name__ == "__main__":
    unittest.main()