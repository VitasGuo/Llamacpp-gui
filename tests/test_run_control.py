"""「运行控制」顶部"全部结束"的目标聚合回归测试（纯函数，无需 Qt）。

背景（v1.19.1）：每行已有各自的"结束"按钮后，顶部大按钮改为"全部结束"；
目标不能只看运行中清单——跨会话恢复的服务可能还没进清单，只按清单结束
会漏掉它、残留占用端口。
"""
import unittest

from ui.app import LEGACY_NAME, stop_all_targets


class TestStopAllTargets(unittest.TestCase):
    def test_rows_only(self):
        names, legacy = stop_all_targets({"A": None, "B": None}, {}, False)
        self.assertEqual(names, ["A", "B"])
        self.assertFalse(legacy)

    def test_runtime_only(self):
        # 清单为空但 pids.json 有记录（跨会话恢复尚未进清单）→ 仍要结束
        names, legacy = stop_all_targets({}, {"A": {"pid": 1}}, False)
        self.assertEqual(names, ["A"])
        self.assertFalse(legacy)

    def test_union_dedup_keeps_row_order(self):
        names, legacy = stop_all_targets(
            {"A": None, "B": None}, {"B": {"pid": 2}, "C": {"pid": 3}}, False)
        self.assertEqual(names, ["A", "B", "C"])
        self.assertFalse(legacy)

    def test_legacy_from_state_or_row(self):
        # 旧实例：由 _legacy_running 或清单里的"旧实例"行标识，不混入名字列表
        names, legacy = stop_all_targets({}, {}, True)
        self.assertEqual(names, [])
        self.assertTrue(legacy)
        names, legacy = stop_all_targets({LEGACY_NAME: None}, {}, False)
        self.assertEqual(names, [])
        self.assertTrue(legacy)

    def test_empty_everything(self):
        self.assertEqual(stop_all_targets({}, {}, False), ([], False))

    def test_ignores_blank_names(self):
        names, _ = stop_all_targets({"": None, "A": None}, {"": {"pid": 1}}, False)
        self.assertEqual(names, ["A"])


if __name__ == "__main__":
    unittest.main()