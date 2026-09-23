"""Tailscale 手动指定 IP 覆盖自动检测 测试（v1.15.0 新增）。"""
import unittest
from unittest import mock

from service import tailscale


class _FakeSettings:
    def __init__(self, tailscale_ip=""):
        self.tailscale_ip = tailscale_ip


class TestTailscaleOverride(unittest.TestCase):
    @mock.patch.object(tailscale, "_detect_cached", return_value="100.64.0.1")
    def test_manual_ip_wins_over_detect(self, _):
        instance = _FakeSettings(tailscale_ip="100.101.1.5")
        with mock.patch.object(tailscale.Settings, "get_instance", return_value=instance):
            self.assertEqual(tailscale.get_tailscale_ipv4(), "100.101.1.5")

    @mock.patch.object(tailscale, "_detect_cached", return_value="100.64.0.1")
    def test_invalid_manual_ip_falls_back_to_detect(self, _):
        instance = _FakeSettings(tailscale_ip="192.168.1.10")  # 非 Tailscale 段
        with mock.patch.object(tailscale.Settings, "get_instance", return_value=instance):
            self.assertEqual(tailscale.get_tailscale_ipv4(), "100.64.0.1")

    @mock.patch.object(tailscale, "_detect_cached", return_value="100.64.0.1")
    def test_empty_manual_ip_falls_back_to_detect(self, _):
        instance = _FakeSettings(tailscale_ip="")
        with mock.patch.object(tailscale.Settings, "get_instance", return_value=instance):
            self.assertEqual(tailscale.get_tailscale_ipv4(), "100.64.0.1")


if __name__ == "__main__":
    unittest.main()