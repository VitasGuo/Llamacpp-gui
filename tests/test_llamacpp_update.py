"""service.llamacpp_update_service 纯函数 + 安装流程测试（不碰网络/真实子进程）。"""
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
import zipfile

from service import llamacpp_update_service as svc


class TestParseVersionOutput(unittest.TestCase):
    def test_old_format(self):
        self.assertEqual(svc.parse_version_output("version: 5432 (c747294)"), 5432)

    def test_old_format_spaced_paren(self):
        self.assertEqual(svc.parse_version_output("version: 8151 ( c747294 )"), 8151)

    def test_new_format_with_build(self):
        self.assertEqual(
            svc.parse_version_output("version: 0.2.0-dev (build 10615, commit f280b2698)"),
            10615)

    def test_build_colon_form(self):
        self.assertEqual(
            svc.parse_version_output("build: 8571 (e397d38) with Clang 19.1.5 for Windows x86_64"),
            8571)

    def test_cuda_noise_prefix(self):
        output = ("ggml_cuda_init: found 1 CUDA devices:\n"
                  "Device 0: NVIDIA GeForce RTX 4070, compute capability 8.9\n"
                  "version: 6264 (043fb27)\n"
                  "built with GNU 13.3.0 for Linux x86_64")
        self.assertEqual(svc.parse_version_output(output), 6264)

    def test_empty(self):
        self.assertEqual(svc.parse_version_output(""), 0)
        self.assertEqual(svc.parse_version_output("garbage output"), 0)


class TestParseAssetName(unittest.TestCase):
    def test_main_cuda(self):
        self.assertEqual(
            svc.parse_asset_name("llama-b6264-bin-win-cuda-12.4-x64.zip"),
            ("llama", "cuda-12.4"))

    def test_main_cuda_13(self):
        self.assertEqual(
            svc.parse_asset_name("llama-b9004-bin-win-cuda-13.1-x64.zip"),
            ("llama", "cuda-13.1"))

    def test_main_cpu_vulkan(self):
        self.assertEqual(svc.parse_asset_name("llama-b6264-bin-win-cpu-x64.zip"), ("llama", "cpu"))
        self.assertEqual(
            svc.parse_asset_name("llama-b6264-bin-win-vulkan-x64.zip"), ("llama", "vulkan"))

    def test_cudart(self):
        self.assertEqual(
            svc.parse_asset_name("cudart-llama-bin-win-cuda-12.4-x64.zip"),
            ("cudart", "cuda-12.4"))

    def test_non_windows_rejected(self):
        self.assertEqual(svc.parse_asset_name("llama-b6264-bin-macos-arm64.zip"), (None, None))
        self.assertEqual(svc.parse_asset_name("llama-b6264-bin-ubuntu-x64.zip"), (None, None))
        self.assertEqual(svc.parse_asset_name("llama-b6264-xcframework.zip"), (None, None))
        self.assertEqual(svc.parse_asset_name("Source code(zip)"), (None, None))


class TestDownloadUrl(unittest.TestCase):
    def test_direct(self):
        self.assertEqual(
            svc.download_url("b1", "a.zip"),
            "https://github.com/ggml-org/llama.cpp/releases/download/b1/a.zip")

    def test_mirror_with_trailing_slash(self):
        self.assertEqual(
            svc.download_url("b1", "a.zip", "https://ghfast.top/"),
            "https://ghfast.top/https://github.com/ggml-org/llama.cpp/releases/download/b1/a.zip")

    def test_mirror_without_trailing_slash(self):
        self.assertEqual(
            svc.download_url("b1", "a.zip", "https://ghfast.top"),
            "https://ghfast.top/https://github.com/ggml-org/llama.cpp/releases/download/b1/a.zip")


class TestRecommendVariant(unittest.TestCase):
    def test_cuda124_for_mid_driver(self):
        gpu = {"nvidia": True, "driver": "566.36"}
        self.assertEqual(svc.recommend_variant(gpu, ["cpu", "cuda-12.4"]), "cuda-12.4")

    def test_cuda131_for_new_driver(self):
        gpu = {"nvidia": True, "driver": "582.33"}
        self.assertEqual(
            svc.recommend_variant(gpu, ["cpu", "cuda-12.4", "cuda-13.1"]), "cuda-13.1")

    def test_cuda133_for_new_driver(self):
        # 2026-09 资产命名演进为 cuda-13.3，仍应按 13 系列推荐
        gpu = {"nvidia": True, "driver": "582.33"}
        self.assertEqual(
            svc.recommend_variant(gpu, ["cpu", "cuda-12.4", "cuda-13.3"]), "cuda-13.3")

    def test_old_driver_falls_back_to_cuda12(self):
        # 驱动 566 不满足 13 系列门槛（>=580），降级到 cuda-12.4
        gpu = {"nvidia": True, "driver": "566.36"}
        self.assertEqual(
            svc.recommend_variant(gpu, ["cpu", "cuda-12.4", "cuda-13.3"]), "cuda-12.4")

    def test_very_old_driver_falls_back_to_cpu(self):
        gpu = {"nvidia": True, "driver": "500.00"}
        self.assertEqual(
            svc.recommend_variant(gpu, ["cpu", "cuda-12.4", "cuda-13.3"]), "cpu")

    def test_no_nvidia(self):
        self.assertEqual(svc.recommend_variant({"nvidia": False}, ["cpu", "cuda-12.4"]), "cpu")

    def test_fallback_when_no_cuda_asset(self):
        gpu = {"nvidia": True, "driver": "582.33"}
        self.assertEqual(svc.recommend_variant(gpu, ["cpu", "vulkan"]), "cpu")

    def test_variant_label(self):
        self.assertEqual(svc.variant_label("cuda-13.3"), "CUDA 13.3")
        self.assertEqual(svc.variant_label("cuda-12.4"), "CUDA 12.4")
        self.assertEqual(svc.variant_label("openvino-2026.3.1"), "OpenVINO 2026.3.1")
        self.assertEqual(svc.variant_label("rocm-10.0"), "ROCm 10.0")
        self.assertEqual(svc.variant_label("cpu"), "CPU")
        self.assertEqual(svc.variant_label("vulkan"), "Vulkan")


class TestTagAndDriver(unittest.TestCase):
    def test_tag_to_build(self):
        self.assertEqual(svc.tag_to_build("b10615"), 10615)
        self.assertEqual(svc.tag_to_build("b5432"), 5432)
        self.assertEqual(svc.tag_to_build("v1.0"), 0)
        self.assertEqual(svc.tag_to_build(""), 0)

    def test_driver_tuple(self):
        self.assertEqual(svc.driver_tuple("566.36"), (566, 36))
        self.assertEqual(svc.driver_tuple("580.65"), (580, 65))
        self.assertEqual(svc.driver_tuple("bad"), (0, 0))


class TestReplaceBatDir(unittest.TestCase):
    def test_forward_slash(self):
        content = '@echo off\ncd /d "C:/llama-cpp"\nllama-server.exe ^'
        result = svc.replace_bat_dir(content, "C:/llama-cpp", "D:/new/dir")
        # v1.9.0 路径规范：new_dir 统一正斜线 /
        self.assertIn('cd /d "D:/new/dir"', result)

    def test_backward_slash(self):
        content = '@echo off\ncd /d "C:\\llama-cpp"\nllama-server.exe ^'
        result = svc.replace_bat_dir(content, "C:/llama-cpp", "D:/new/dir")
        # 旧写法（反斜线）同样被替换，且结果统一正斜线 /
        self.assertIn('cd /d "D:/new/dir"', result)


class _StubSettings:
    def __init__(self, path):
        self.llamacpp_path = path
        self.saved = False

    def save(self):
        self.saved = True


class _StubScriptService:
    def __init__(self, entries):
        self.entries = entries
        self.saved = []

    def load_scripts(self):
        return self.entries

    def save_script(self, entry):
        self.saved.append(entry)


class TestSwitchVersion(unittest.TestCase):
    def test_switch_updates_settings_and_scripts(self):
        settings = _StubSettings("C:/llama-cpp/llama-server.exe")

        class Entry:
            name = "t"
            content = '@echo off\ncd /d "C:/llama-cpp"\nllama-server.exe ^ -m "x.gguf"'

        entry = Entry()
        service = _StubScriptService([entry])
        updated = svc.switch_version(
            "D:/llamacpp/b9999-cpu/llama-server.exe",
            settings=settings, script_service=service)
        self.assertEqual(updated, 1)
        self.assertIn("D:/llamacpp/b9999-cpu", entry.content)
        self.assertNotIn("C:/llama-cpp", entry.content)
        self.assertEqual(len(service.saved), 1)
        self.assertTrue(settings.saved)
        self.assertEqual(settings.llamacpp_path, "D:/llamacpp/b9999-cpu/llama-server.exe")

    def test_same_dir_no_script_update(self):
        settings = _StubSettings("C:/llama-cpp/llama-server.exe")
        service = _StubScriptService([])
        updated = svc.switch_version(
            "C:/llama-cpp/llama-server.exe",
            settings=settings, script_service=service)
        self.assertEqual(updated, 0)


class TestHasCudart(unittest.TestCase):
    def test_dir_with_cublas(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "cublas64_12.dll"), "w").close()
            self.assertTrue(svc.has_cudart(d))

    def test_empty_dir(self):
        # mock ProgramFiles 隔离真实机器上的 CUDA toolkit，保证测试密闭
        with tempfile.TemporaryDirectory() as fake_pf, \
                tempfile.TemporaryDirectory() as d, \
                unittest.mock.patch.dict(os.environ, {"ProgramFiles": fake_pf}):
            self.assertFalse(svc.has_cudart(d))

    def test_major_filter(self):
        # mock ProgramFiles 隔离真实机器上的 CUDA toolkit，保证测试密闭
        with tempfile.TemporaryDirectory() as fake_pf, \
                tempfile.TemporaryDirectory() as d, \
                unittest.mock.patch.dict(os.environ, {"ProgramFiles": fake_pf}):
            open(os.path.join(d, "cublas64_12.dll"), "w").close()
            self.assertTrue(svc.has_cudart(d, "12"))
            self.assertFalse(svc.has_cudart(d, "13"))


class TestFindCopyCudaDlls(unittest.TestCase):
    def test_find_with_major_filter(self):
        with tempfile.TemporaryDirectory() as d:
            for f in ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll",
                      "cudart64_13.dll", "ggml-cuda.dll", "readme.txt"):
                open(os.path.join(d, f), "w").close()
            found_12 = svc.find_cuda_dlls([d], "12")
            self.assertEqual(len(found_12), 3)
            found_13 = svc.find_cuda_dlls([d], "13")
            self.assertEqual(len(found_13), 1)
            self.assertTrue(found_13[0].endswith("cudart64_13.dll"))
            # 不限大版本 → 4 个 CUDA DLL
            self.assertEqual(len(svc.find_cuda_dlls([d])), 4)

    def test_find_dedup_prefers_first_dir(self):
        with tempfile.TemporaryDirectory() as root:
            d1, d2 = os.path.join(root, "a"), os.path.join(root, "b")
            os.makedirs(d1), os.makedirs(d2)
            open(os.path.join(d1, "cudart64_12.dll"), "w").close()
            open(os.path.join(d2, "cudart64_12.dll"), "w").close()
            found = svc.find_cuda_dlls([d1, d2], "12")
            self.assertEqual(len(found), 1)
            self.assertEqual(os.path.dirname(found[0]), d1)

    def test_copy_cuda_dlls(self):
        with tempfile.TemporaryDirectory() as root:
            src, dest = os.path.join(root, "src"), os.path.join(root, "dest")
            os.makedirs(src)
            for f in ("cudart64_12.dll", "cublas64_12.dll", "cudart64_13.dll"):
                open(os.path.join(src, f), "w").close()
            copied = svc.copy_cuda_dlls([src], dest, "12")
            self.assertEqual(copied, 2)
            self.assertTrue(os.path.isfile(os.path.join(dest, "cudart64_12.dll")))
            self.assertFalse(os.path.exists(os.path.join(dest, "cudart64_13.dll")))


_LATEST = {
    "tag": "b10793", "build": 10793, "date_str": "2026-09-03",
    "assets": {
        "cpu": {"name": "llama-b10793-bin-win-cpu-x64.zip", "size": 15_000_000, "digest": ""},
        "cuda-12.4": {"name": "llama-b10793-bin-win-cuda-12.4-x64.zip", "size": 200_000_000, "digest": ""},
        "cuda-13.3": {"name": "llama-b10793-bin-win-cuda-13.3-x64.zip", "size": 210_000_000, "digest": ""},
        "cudart-cuda-12.4": {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "size": 373_000_000, "digest": ""},
        "cudart-cuda-13.3": {"name": "cudart-llama-bin-win-cuda-13.3-x64.zip", "size": 380_000_000, "digest": ""},
    },
}


class TestCudartPlan(unittest.TestCase):
    def test_non_cuda_variant(self):
        self.assertEqual(svc.cudart_plan(_LATEST, "cpu", [], toolkit_has=False), "none")

    def test_copy_when_sources_match_major(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "cudart64_12.dll"), "w").close()
            self.assertEqual(
                svc.cudart_plan(_LATEST, "cuda-12.4", [d], toolkit_has=False), "copy")
            # 大版本不匹配（13 vs 目录里只有 12 的 DLL）→ 不复制
            self.assertEqual(
                svc.cudart_plan(_LATEST, "cuda-13.3", [d], toolkit_has=False), "download")

    def test_download_when_nothing_available(self):
        self.assertEqual(
            svc.cudart_plan(_LATEST, "cuda-13.3", [], toolkit_has=False), "download")

    def test_none_when_toolkit_installed(self):
        self.assertEqual(
            svc.cudart_plan(_LATEST, "cuda-12.4", [], toolkit_has=True), "none")

    def test_none_when_no_cudart_asset(self):
        release = {"assets": {"cuda-12.4": {"name": "x", "size": 1}}}
        self.assertEqual(
            svc.cudart_plan(release, "cuda-12.4", [], toolkit_has=False), "none")


class TestPlanAutoDownload(unittest.TestCase):
    GPU_NEW = {"nvidia": True, "driver": "616.56"}

    def test_current_channel_and_recommended(self):
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-12.4", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-12.4"), ("b10793", "cuda-13.3")])

    def test_dedupe_when_recommended_is_current_channel(self):
        # 驱动只支持 12 系时推荐 = 当前通道 → 只装一次
        gpu = {"nvidia": True, "driver": "566.36"}
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-12.4", gpu, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-12.4")])

    def test_skip_installed(self):
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-12.4", self.GPU_NEW,
            installed_keys=["b10793-cuda-13.3"])
        self.assertEqual(plans, [("b10793", "cuda-12.4")])

    def test_current_channel_not_newer_skipped(self):
        # 本地已是最新 build 的 cuda-12.4 → 当前通道跳过，推荐变体仍下载
        plans = svc.plan_auto_download(
            _LATEST, 10793, "cuda-12.4", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-13.3")])

    def test_all_skipped_when_nothing_to_do(self):
        plans = svc.plan_auto_download(
            _LATEST, 10793, "cuda-13.3", self.GPU_NEW,
            installed_keys=["b10793-cuda-12.4"])
        self.assertEqual(plans, [])

    def test_no_nvidia_recommends_cpu(self):
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-12.4", {"nvidia": False}, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-12.4"), ("b10793", "cpu")])

    def test_invalid_latest(self):
        self.assertEqual(svc.plan_auto_download(None, 100, "cpu", self.GPU_NEW, []), [])
        self.assertEqual(svc.plan_auto_download({}, 100, "cpu", self.GPU_NEW, []), [])

    def test_local_variant_not_in_assets(self):
        # 当前通道已停产（资产里没有 11 系）→ 只下载推荐变体
        plans = svc.plan_auto_download(
            _LATEST, 100, "cuda-11.7", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-13.3")])

    def test_series_match_local_channel(self):
        # 本地 "cuda-13"（通道级嗅探）→ 匹配同系列最新资产 "cuda-13.3"，
        # 与推荐相同 → 去重后只装一次
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-13", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-13.3")])

    def test_series_match_full_variant_key(self):
        # 本地 "cuda-13.1"（完整 key 但资产里只有 13.3）→ 系列匹配到 13.3
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-13.1", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-13.3")])

    def test_series_channel_with_cpu_recommended(self):
        # 无 N 卡：当前通道 cuda-13 仍按系列更新，推荐 cpu
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-13", {"nvidia": False}, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-13.3"), ("b10793", "cpu")])

    def test_list_skips_incomplete_latest(self):
        """releases 列表：最新 release 只有 cudart（发布中快照）→ 自动落到下一个可下载版本。"""
        incomplete = {
            "tag": "b99999", "build": 99999,
            "assets": {"cudart-cuda-13.3": {"name": "c", "size": 1}},
        }
        plans = svc.plan_auto_download(
            [incomplete, _LATEST], 10453, "cuda-12.4", self.GPU_NEW, installed_keys=[])
        # 跳过 b99999，落到 b10793
        self.assertEqual(plans, [("b10793", "cuda-12.4"), ("b10793", "cuda-13.3")])

    def test_list_single_dict_backward_compat(self):
        """单 dict 传参保持历史行为（不因列表化而破坏既有调用）。"""
        plans = svc.plan_auto_download(
            _LATEST, 10453, "cuda-12.4", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [("b10793", "cuda-12.4"), ("b10793", "cuda-13.3")])

    def test_list_all_incomplete_returns_empty(self):
        plans = svc.plan_auto_download(
            [{"tag": "b1", "build": 1,
              "assets": {"cudart-cuda-13.3": {"name": "c", "size": 1}}}],
            100, "cpu", self.GPU_NEW, installed_keys=[])
        self.assertEqual(plans, [])

    def test_release_has_main_asset(self):
        self.assertFalse(svc._release_has_main_asset(
            {"assets": {"cudart-cuda-13.3": {"name": "c", "size": 1}}}))
        self.assertTrue(svc._release_has_main_asset(
            {"assets": {"cuda-13.3": {"name": "m", "size": 1}}}))
        self.assertFalse(svc._release_has_main_asset(None))
        self.assertFalse(svc._release_has_main_asset({}))


class TestListInstalledSort(unittest.TestCase):
    def test_sorted_by_build_number_not_tag_string(self):
        """b9999 与 b10615 共存时按 build 号排序（字符串排序 '9'>'1' 会错乱）。"""
        import json
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for tag in ("b9999", "b10615"):
            d = os.path.join(tmp, f"{tag}-cpu")
            os.makedirs(d)
            with open(os.path.join(d, svc.META_FILENAME), "w", encoding="utf-8") as f:
                json.dump({"tag": tag, "variant": "cpu",
                           "installed_at": "2026-09-01T00:00:00"}, f)
            open(os.path.join(d, "llama-server.exe"), "w").close()
        items = svc.list_installed(tmp)
        self.assertEqual([i["tag"] for i in items], ["b10615", "b9999"])


def _make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def _install_version(root, tag, variant):
    """构造模拟版本目录（meta + exe）供清理规则测试。"""
    d = os.path.join(root, f"{tag}-{variant}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, svc.META_FILENAME), "w", encoding="utf-8") as f:
        json.dump({"tag": tag, "variant": variant,
                   "installed_at": "2026-09-01T00:00:00"}, f)
    open(os.path.join(d, "llama-server.exe"), "w").close()
    return d


class TestCleanup(unittest.TestCase):
    """旧版本清理规则（保留当前 + 每变体系列最新 2 个）。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_each_variant_keeps_2_except_current(self):
        # cuda-13.4 四个（含当前）、cuda-13.3 两个
        cur = _install_version(self.tmp, "b11093", "cuda-13.4")
        _install_version(self.tmp, "b11065", "cuda-13.4")
        _install_version(self.tmp, "b11011", "cuda-13.4")
        _install_version(self.tmp, "b11005", "cuda-13.4")
        _install_version(self.tmp, "b10936", "cuda-13.3")
        _install_version(self.tmp, "b10934", "cuda-13.3")
        cur_exe = os.path.join(cur, "llama-server.exe")
        del_items = svc.plan_version_cleanup(self.tmp, cur_exe, keep_series=2)
        tags = {i["tag"] for i in del_items}
        # 当前 b11093 恒保留；13.4 保留最新 2（b11065/b11011）→ 删 b11005；13.3 未超 → 全保留
        self.assertEqual(tags, {"b11005"})

    def test_keep_all_when_within_threshold(self):
        _install_version(self.tmp, "b11093", "cuda-13.4")
        _install_version(self.tmp, "b11065", "cuda-13.4")
        _install_version(self.tmp, "b10936", "cuda-13.3")
        del_items = svc.plan_version_cleanup(self.tmp, None, keep_series=2)
        self.assertEqual(del_items, [])

    def test_zip_cleanup_only_for_installed(self):
        # 已装版本对应的 zip 可清；未装版本、cudart zip 保留
        _install_version(self.tmp, "b10936", "cuda-13.3")
        zips = os.path.join(self.tmp, "zips")
        os.makedirs(zips, exist_ok=True)
        installed_zip = os.path.join(zips, "llama-b10936-bin-win-cuda-13.3-x64.zip")
        _make_zip(installed_zip, {"x": b"1"})
        not_installed = os.path.join(zips, "llama-b99999-bin-win-cuda-13.3-x64.zip")
        _make_zip(not_installed, {"x": b"2"})
        cudart_zip = os.path.join(zips, "cudart-llama-bin-win-cuda-13.3-x64.zip")
        _make_zip(cudart_zip, {"x": b"3"})
        del_zips = [os.path.basename(z) for z in svc.plan_zip_cleanup(self.tmp)]
        self.assertEqual(del_zips, ["llama-b10936-bin-win-cuda-13.3-x64.zip"])

    def test_delete_version_dir_removes_and_returns_size(self):
        d = _install_version(self.tmp, "b10936", "cuda-13.3")
        size = svc.delete_version_dir(d)
        self.assertGreater(size, 0)
        self.assertFalse(os.path.exists(d))


class TestInstallFromZip(unittest.TestCase):
    def test_install_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "pkg.zip")
            _make_zip(zip_path, {"llama-server.exe": "bin", "ggml-cpu.dll": "dll"})
            exe = svc.install_from_zip(zip_path, tmp, "b9999", "cpu")
            self.assertTrue(os.path.isfile(exe))
            self.assertTrue(exe.endswith(os.path.join("b9999-cpu", "llama-server.exe")))
            installed = svc.list_installed(tmp)
            self.assertEqual(len(installed), 1)
            self.assertEqual(installed[0]["tag"], "b9999")
            self.assertEqual(installed[0]["variant"], "cpu")

    def test_reinstall_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "pkg.zip")
            _make_zip(zip_path, {"llama-server.exe": "bin"})
            svc.install_from_zip(zip_path, tmp, "b9999", "cpu")
            svc.install_from_zip(zip_path, tmp, "b9999", "cpu")  # 不抛错即通过
            self.assertEqual(len(svc.list_installed(tmp)), 1)

    def test_missing_server_exe_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "bad.zip")
            _make_zip(zip_path, {"readme.txt": "hi"})
            with self.assertRaises(ValueError):
                svc.install_from_zip(zip_path, tmp, "b9999", "cpu")
            # 失败后不残留临时目录
            self.assertFalse(os.path.exists(os.path.join(tmp, ".tmp-b9999-cpu")))

    def test_zip_slip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "evil.zip")
            _make_zip(zip_path, {"llama-server.exe": "bin", "../evil.txt": "boom"})
            with self.assertRaises(ValueError):
                svc.install_from_zip(zip_path, tmp, "b9999", "cpu")
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.txt")))
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(tmp), "evil.txt")))

    def test_extract_zip_into_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_zip = os.path.join(tmp, "main.zip")
            cudart_zip = os.path.join(tmp, "cudart.zip")
            _make_zip(main_zip, {"llama-server.exe": "bin"})
            _make_zip(cudart_zip, {"cudart64_12.dll": "rt", "sub/cublas64_12.dll": "rt2"})
            exe = svc.install_from_zip(main_zip, tmp, "b9999", "cuda-12.4")
            dest = os.path.dirname(exe)
            svc.extract_zip_into(cudart_zip, dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "cudart64_12.dll")))
            self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "cublas64_12.dll")))


if __name__ == "__main__":
    unittest.main()
