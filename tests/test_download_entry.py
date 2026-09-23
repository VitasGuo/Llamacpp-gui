"""DownloadEntry.progress 百分比 clamp 测试（进度条失真兜底）。"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from model.download_entry import DownloadEntry, DownloadQueue


class TestProgress(unittest.TestCase):
    def test_normal_percent(self):
        e = DownloadEntry(model_id="m", file_path="f", file_size=1000, downloaded=500)
        self.assertEqual(e.progress, 50)

    def test_zero_size_returns_zero(self):
        e = DownloadEntry(model_id="m", file_path="f", file_size=0, downloaded=500)
        self.assertEqual(e.progress, 0)

    def test_clamp_over_100(self):
        # 元数据 file_size 失真（实际 5GB / 元数据 744MB）时百分比不得超 100
        e = DownloadEntry(model_id="m", file_path="f", file_size=744039392, downloaded=5039006688)
        self.assertEqual(e.progress, 100)

    def test_clamp_below_zero(self):
        e = DownloadEntry(model_id="m", file_path="f", file_size=100, downloaded=-5)
        self.assertEqual(e.progress, 0)


class TestQueueLoadRobust(unittest.TestCase):
    """queue.json 结构异常（合法 JSON 但非预期结构）时不崩溃（审查修复）。"""

    def _load_with(self, data):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "queue.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with mock.patch("model.download_entry.DOWNLOAD_QUEUE_FILE", path):
            return DownloadQueue()

    def test_top_level_list_does_not_crash(self):
        self.assertEqual(self._load_with([]).entries, [])

    def test_non_dict_entry_skipped(self):
        q = self._load_with({"queue": [{"file_path": "x", "model_id": "m"}, "garbage"]})
        self.assertEqual(len(q.entries), 1)


if __name__ == "__main__":
    unittest.main()