"""聊天页自动填充地址 _pick_llm_url 的回归测试（纯函数，无需 Qt）。

背景（traps #34）：脚本绑定模型后用户常切到其他模型调参，_current_llm_url
两层来源都只查"当前选中脚本"，选中名不在运行记录中时整体返回空——
聊天页拿不到正在运行模型的地址。
"""
import unittest

from ui.app import _pick_llm_url


class TestPickLlmUrl(unittest.TestCase):
    def test_selected_ready_url_first(self):
        # 选中脚本就绪 URL 优先（host 0.0.0.0 解析为探测缓存/127.0.0.1）
        urls = {"A": "http://0.0.0.0:8080"}
        runtime = {"A": {"port": 8080, "host": "127.0.0.1"}}
        self.assertEqual(_pick_llm_url("A", urls, runtime, "", ""), "http://127.0.0.1:8080")

    def test_fallback_to_other_ready_url(self):
        # 回归：选中名不在就绪表 → 回退其他就绪 URL（此前返回空）
        urls = {"A": "http://100.1.2.3:8080"}
        self.assertEqual(_pick_llm_url("B", urls, {}, "", ""), "http://100.1.2.3:8080")

    def test_runtime_when_no_ready_url(self):
        # 无就绪 URL（跨会话恢复）→ pids.json 记录
        runtime = {"A": {"port": 8080, "host": "0.0.0.0"}}
        self.assertEqual(_pick_llm_url("A", {}, runtime, "", "100.5.5.5"),
                         "http://100.5.5.5:8080")

    def test_fallback_to_other_runtime(self):
        # 回归：选中名不在 runtime → 回退任意运行中记录（此前返回空）
        runtime = {"A": {"port": 8080, "host": "127.0.0.1"}}
        self.assertEqual(_pick_llm_url("B", {}, runtime, "", ""), "http://127.0.0.1:8080")

    def test_manual_override_beats_cache(self):
        # 手动指定 Tailscale IP（须为 CGNAT 段）优先于自动探测缓存
        runtime = {"A": {"port": 8080, "host": "0.0.0.0"}}
        self.assertEqual(_pick_llm_url("A", {}, runtime, "100.100.100.100", "100.64.1.1"),
                         "http://100.100.100.100:8080")

    def test_explicit_host_preserved(self):
        # 显式绑定 host（非 0.0.0.0）原样保留
        runtime = {"A": {"port": 8080, "host": "100.1.2.3"}}
        self.assertEqual(_pick_llm_url("A", {}, runtime, "100.9.9.9", ""),
                         "http://100.1.2.3:8080")

    def test_empty_when_nothing_running(self):
        self.assertEqual(_pick_llm_url("A", {}, {}, "", ""), "")


if __name__ == "__main__":
    unittest.main()
