"""service.script_builder 纯函数测试。"""
import os
import shutil
import struct
import tempfile
import unittest

from service.script_builder import (
    extract_port,
    extract_host,
    find_mmproj,
    build_bat_content,
    auto_generate_config,
    parse_bat_params,
    parse_model_path_from_bat,
    read_gguf_context_length,
    ctx_options_for,
)
from utils.path_utils import normalize_path


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
        # 路径统一正斜线 /（v1.9.0 路径规范，见 utils/path_utils.py）
        content = build_bat_content(
            exe_dir="C:\\a b", model_path="C:\\path with space\\m.gguf",
            config={},
        )
        self.assertIn('"C:/path with space/m.gguf"', content)


class TestFindMmproj(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        # 用完即删：本文件会造 1.5G/8G 的假模型，不清理会持续吃盘（traps #39）
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_none_when_absent(self):
        self.assertEqual(find_mmproj(os.path.join(self._tmp, "nope.gguf")), "")

    def test_finds_adjacent_mmproj(self):
        model = os.path.join(self._tmp, "model.gguf")
        open(model, "w").close()
        mm = os.path.join(self._tmp, "mmproj-model.gguf")
        open(mm, "w").close()
        # find_mmproj 返回值统一正斜线 /（v1.9.0 路径规范）
        self.assertEqual(find_mmproj(model), normalize_path(mm))


class TestAutoGenerateAndParse(unittest.TestCase):
    """一键生成的 auto_generate_config + 反向解析 roundtrip。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        # 用完即删：本文件会造 1.5G/8G 的假模型，不清理会持续吃盘（traps #39）
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _make_model(self, name="MiniCPM5-2B-F16.gguf", size=5 * 1024 * 1024):
        p = os.path.join(self._tmp, name)
        with open(p, "wb") as f:
            f.seek(size - 1)
            f.write(b"\0")
        return p

    @staticmethod
    def _make_gguf(path, context_length):
        """构造最小合法 GGUF：magic+version3+kv_count1+general.context_length(uint32)。"""
        key = b"general.context_length"
        with open(path, "wb") as f:
            f.write(b"GGUF")
            f.write(struct.pack("<I", 3))
            f.write(struct.pack("<Q", 0))            # tensor_count
            f.write(struct.pack("<Q", 1))            # kv_count
            f.write(struct.pack("<Q", len(key)))     # key 长度
            f.write(key)
            f.write(struct.pack("<I", 4))            # UINT32
            f.write(struct.pack("<I", context_length))

    def test_auto_generate_has_essentials(self):
        from_service = auto_generate_config(self._make_model())
        self.assertEqual(from_service["alias"], "MiniCPM5-2B-F16")
        self.assertEqual(from_service["port"], "8080")
        self.assertIn("ctx_size", from_service)
        self.assertEqual(from_service["cache_type_k"], "q8_0")

    def test_ctx_tier_by_size(self):
        # 无 GGUF 元数据（全零文件）→ 回退文件大小启发：<2.5G → 65536
        small = auto_generate_config(self._make_model("a.gguf", size=int(1.5 * 1024 * 1024 * 1024)))
        self.assertEqual(small["ctx_size"], "65536")
        # >=6G → 16384
        big = auto_generate_config(self._make_model("b.gguf", size=int(8 * 1024 * 1024 * 1024)))
        self.assertEqual(big["ctx_size"], "16384")

    def test_build_then_parse_roundtrip(self):
        cfg = auto_generate_config(self._make_model())
        bat = build_bat_content(r"C:/llama", self._make_model(), cfg)
        parsed = parse_bat_params(bat)
        for k in ("alias", "port", "ctx_size", "cache_type_k", "cache_type_v", "host"):
            self.assertIn(k, parsed)
            self.assertEqual(parsed[k], cfg[k])
        self.assertEqual(parse_model_path_from_bat(bat), normalize_path(self._make_model()))

    def test_parse_switch_presence(self):
        # mmap 开关已按 traps #15 移除（新版 llama.cpp 不再支持 --mmap），用仍保留的开关验证
        bat = build_bat_content(r"C:/llama", self._make_model(), {"port": "8080", "no_mmap_fallback": "on"})
        self.assertEqual(parse_bat_params(bat).get("no_mmap_fallback"), "on")


class TestGgufContextLength(unittest.TestCase):
    """GGUF 元数据解析（ctx 挡位的事实源）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # 用完即删：本文件会造 1.5G/8G 的假模型，不清理会持续吃盘（traps #39）
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _gguf(self, name, ctx):
        p = os.path.join(self._tmp, name)
        TestAutoGenerateAndParse._make_gguf(p, ctx)
        return p

    def test_read_context_length(self):
        self.assertEqual(read_gguf_context_length(self._gguf("m.gguf", 131072)), 131072)
        self.assertEqual(read_gguf_context_length(self._gguf("n.gguf", 4096)), 4096)

    def test_non_gguf_returns_none(self):
        p = os.path.join(self._tmp, "zero.gguf")
        with open(p, "wb") as f:
            f.write(b"\0" * 1024)
        self.assertIsNone(read_gguf_context_length(p))

    def test_missing_file_returns_none(self):
        self.assertIsNone(read_gguf_context_length(os.path.join(self._tmp, "nope.gguf")))


class TestCtxOptions(unittest.TestCase):
    """ctx 挡位规则：上限<128K 默认取最大值，≥128K 默认 128K。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # 用完即删：本文件会造 1.5G/8G 的假模型，不清理会持续吃盘（traps #39）
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _gguf(self, name, ctx):
        p = os.path.join(self._tmp, name)
        TestAutoGenerateAndParse._make_gguf(p, ctx)
        return p

    def test_small_cap_defaults_to_max(self):
        # 上限 65536（<128K）：默认=上限，挡位 8K~64K
        tiers, default = ctx_options_for(self._gguf("small.gguf", 65536))
        self.assertEqual(default, 65536)
        self.assertEqual(tiers, [8192, 16384, 32768, 65536])

    def test_non_power_cap_included_in_tiers(self):
        # 上限 100000（非整挡）：挡位追加 100000 本身，默认=100000
        tiers, default = ctx_options_for(self._gguf("odd.gguf", 100000))
        self.assertEqual(default, 100000)
        self.assertEqual(tiers, [8192, 16384, 32768, 65536, 100000])

    def test_large_cap_defaults_128k(self):
        # 上限 1M（≥128K）：默认=128K，挡位含 200K/256K/512K/1M
        tiers, default = ctx_options_for(self._gguf("large.gguf", 1048576))
        self.assertEqual(default, 131072)
        self.assertEqual(
            tiers,
            [8192, 16384, 32768, 65536, 131072, 204800, 262144, 524288, 1048576],
        )

    def test_tiny_cap_single_tier(self):
        # 上限 4096（低于全部基础挡）：仅一个挡位=上限
        tiers, default = ctx_options_for(self._gguf("tiny.gguf", 4096))
        self.assertEqual(default, 4096)
        self.assertEqual(tiers, [4096])

    def test_fallback_by_file_size(self):
        # 读不到元数据 → 回退文件大小启发（<2.5G → 65536）
        p = os.path.join(self._tmp, "plain.bin")
        with open(p, "wb") as f:
            f.write(b"\0" * 1024)
        tiers, default = ctx_options_for(p)
        self.assertEqual(default, 65536)
        self.assertEqual(tiers[-1], 65536)

    def test_auto_generate_uses_gguf_cap(self):
        # 一键生成 ctx 默认值走 GGUF 上限（≥128K → 128K）
        cfg = auto_generate_config(self._gguf("auto.gguf", 262144))
        self.assertEqual(cfg["ctx_size"], "131072")


if __name__ == "__main__":
    unittest.main()
