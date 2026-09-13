"""llama.cpp 预编译版本管理：检测更新、下载安装、版本切换。

职责边界（AGENTS.md）：
- 纯业务逻辑，不导入 PyQt6.QtWidgets；网络/子进程调用均设计为可后台线程执行
- 版本化安装：{install_root}/{tag}-{variant}/ 每版本独立目录，切换 = 改配置 + 批量替换 .bat 路径
- 缓存复用 data/update_cache.json（新 key "llamacpp_releases"，TTL 1 小时；
  不触碰 update_workers.py 已有的 llamacpp/app 条目）
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta

from config import UPDATE_CACHE_FILE
from config.config import Settings
from service.script_service import ScriptService
from utils.atomic_io import atomic_write_json
from utils.logger import error
from utils.path_utils import normalize_path

# 无控制台启动（pythonw / PyInstaller -w）时子进程必须带此标志，否则弹黑窗（traps #5）
NO_WINDOW = 0x08000000

RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
RELEASES_DOWNLOAD = "https://github.com/ggml-org/llama.cpp/releases/download"
_HEADERS = {"User-Agent": "llamacpp-gui/1.0", "Accept": "application/vnd.github+json"}

# releases 列表缓存有效期（llama.cpp 发布频繁，1 小时足够新鲜）
RELEASES_CACHE_TTL = timedelta(hours=1)

VARIANT_LABELS = {
    "cpu": "CPU",
    "vulkan": "Vulkan",
    "sycl": "SYCL (Intel)",
    "hip-radeon": "HIP (AMD)",
}


def variant_label(variant):
    """变体 key → 展示名。已知映射优先；命名随上游演进
    （cuda-13.3 / openvino-2026.3.1 / rocm-10.0 ...），按前缀规则生成。"""
    if variant in VARIANT_LABELS:
        return VARIANT_LABELS[variant]
    if variant.startswith("cuda-"):
        return f"CUDA {variant[5:]}"
    if variant.startswith("openvino"):
        sub = variant[len("openvino"):].lstrip("-")
        return f"OpenVINO {sub}" if sub else "OpenVINO"
    if variant.startswith("rocm"):
        sub = variant[len("rocm"):].lstrip("-")
        return f"ROCm {sub}" if sub else "ROCm"
    if variant.startswith("hip"):
        return "HIP (AMD)"
    return variant

# 安装目录内的元数据文件名（list_installed 据此识别合法版本目录）
META_FILENAME = "llamacpp-manager.json"

# Windows 驱动 → CUDA 兼容门槛：
# CUDA 12.x 应用自带 runtime DLL，minor version compatibility 要求驱动 >= 528；
# CUDA 13.x 要求驱动 >= 580.65
_CUDA12_MIN_DRIVER = (528, 0)
_CUDA13_MIN_DRIVER = (580, 65)

# 主 zip：llama-b10615-bin-win-cuda-12.4-x64.zip / llama-b5432-bin-win-cpu-x64.zip ...
_ASSET_RE = re.compile(r"^llama-(b\d+)-bin-win-([\w.\-]+)-x64\.zip$")
# cudart zip：cudart-llama-bin-win-cuda-12.4-x64.zip
_CUDART_RE = re.compile(r"^cudart-llama-bin-win-([\w.\-]+)-x64\.zip$")
# --version 输出：优先 "(build 10615" / "build: 8571"，回退旧格式 "version: 5432 ("
_BUILD_RE = re.compile(r"build[=:\s]+(\d{4,6})")
_VERSION_RE = re.compile(r"version:\s*(\d{4,6})\s*\(")


# ── 版本输出 / 资产名解析（纯函数）─────────────────────────────

def parse_version_output(output):
    """从 llama-server --version 的 stdout/stderr 合并输出解析 build 号。

    兼容两种格式：
    - 旧：`version: 5432 (c747294)`
    - 新（2026）：`version: 0.2.0-dev (build 10615, commit f280b2698)`
    解析不到返回 0。
    """
    m = _BUILD_RE.search(output or "")
    if m:
        return int(m.group(1))
    m = _VERSION_RE.search(output or "")
    if m:
        return int(m.group(1))
    return 0


def parse_asset_name(name):
    """解析 release 资产文件名。

    返回 (kind, variant)：主包 ("llama", "cuda-12.4")、CUDA 运行库
    ("cudart", "cuda-12.4")；非 Windows x64 包返回 (None, None)。
    """
    m = _ASSET_RE.match(name or "")
    if m:
        return "llama", m.group(2)
    m = _CUDART_RE.match(name or "")
    if m:
        return "cudart", m.group(1)
    return None, None


def tag_to_build(tag):
    """release tag（如 b10615）→ build 号 int；不匹配返回 0。"""
    m = re.match(r"^b(\d+)$", (tag or "").strip())
    return int(m.group(1)) if m else 0


def download_url(tag, asset_name, mirror_prefix=""):
    """构建下载 URL。镜像前缀非空时拼在 GitHub 完整 URL 之前
    （如 https://ghfast.top/https://github.com/...）；前缀末尾斜杠自动兼容。"""
    url = f"{RELEASES_DOWNLOAD}/{tag}/{asset_name}"
    prefix = (mirror_prefix or "").strip()
    if prefix:
        if not prefix.endswith("/"):
            prefix += "/"
        return f"{prefix}{url}"
    return url


def driver_tuple(driver_version):
    """驱动版本串（'566.36'）→ 可比较元组 (566, 36)；异常输入返回 (0, 0)。"""
    try:
        return tuple(int(p) for p in (driver_version or "").split(".")[:2])
    except ValueError:
        return (0, 0)


def recommend_variant(gpu_info, available_variants):
    """按 GPU/驱动信息推荐变体；available 为该 release 实际存在的变体列表。

    变体命名随上游演进（cuda-12.4 → cuda-13.3 ...），故按 cuda-* 前缀系列处理：
    降序尝试所有 CUDA 变体——13.x 需驱动 >= 580，12.x 需 >= 528，
    驱动不满足则逐级降级到更低的 CUDA 系列，最终回退 cpu。
    """
    variants = set(available_variants or [])
    if not variants:
        return "cpu"
    if gpu_info.get("nvidia"):
        dt = driver_tuple(gpu_info.get("driver", ""))
        for v in sorted((x for x in variants if x.startswith("cuda-")), reverse=True):
            major = v.split("-")[1].split(".")[0]
            need = _CUDA13_MIN_DRIVER if int(major) >= 13 else _CUDA12_MIN_DRIVER
            if dt >= need:
                return v
        # 有 N 卡但驱动过旧（<528）或无 CUDA 资产 → CPU
        return "cpu" if "cpu" in variants else next(iter(variants))
    return "cpu" if "cpu" in variants else next(iter(variants))


def replace_bat_dir(content, old_dir, new_dir):
    """把 .bat 内容中的 `cd /d "{old_dir}"` 替换为新目录。

    兼容历史脚本中正/反斜杠两种写法（app.py 生成时 exe_dir 直接取自
    settings 路径，正反斜杠都出现过）；new_dir 统一 normpath。
    """
    if not content or not old_dir:
        return content
    # 统一为正斜线 /（项目路径规范，见 utils/path_utils.py）
    new_norm = normalize_path(os.path.normpath(new_dir))
    candidates = {old_dir, old_dir.replace("/", "\\"), old_dir.replace("\\", "/")}
    for old in candidates:
        if not old:
            continue
        content = content.replace(f'cd /d "{old}"', f'cd /d "{new_norm}"')
    return content


# ── GitHub releases 列表（带缓存）──────────────────────────────

def _read_cache_file():
    try:
        with open(UPDATE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache_file(cache):
    try:
        os.makedirs(os.path.dirname(UPDATE_CACHE_FILE) or ".", exist_ok=True)
        with open(UPDATE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 缓存写失败不影响主流程


def _release_from_api(item):
    """GitHub API release 条目 → 精简 dict（只留 Windows x64 资产）。"""
    tag = item.get("tag_name", "")
    published = item.get("published_at", "")
    assets = {}
    for a in item.get("assets", []) or []:
        if not isinstance(a, dict):
            continue
        kind, variant = parse_asset_name(a.get("name", ""))
        if kind == "llama":
            assets[variant] = {
                "name": a.get("name", ""),
                "size": a.get("size", 0),
                "digest": a.get("digest", ""),
            }
        elif kind == "cudart":
            assets[f"cudart-{variant}"] = {
                "name": a.get("name", ""),
                "size": a.get("size", 0),
                "digest": a.get("digest", ""),
            }
    if not assets:
        return None
    # 只有 cudart 无主包 = release 资产仍在逐步上传的不完整快照 → 丢弃
    # （否则最新版本显示为"只有 cudart"，自动下载的 variants 恒为空被静默跳过）
    if not _release_has_main_asset({"assets": assets}):
        return None
    date_str = ""
    try:
        date_str = datetime.fromisoformat(published.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        pass
    return {
        "tag": tag,
        "build": tag_to_build(tag),
        "published_at": published,
        "date_str": date_str,
        "assets": assets,
    }


def fetch_releases(per_page=15, force=False):
    """拉取最近 N 个 release（带 1h 缓存 + 错误回退缓存）。

    返回 (releases, error_msg)。releases 按 API 顺序（新→旧）。
    """
    cache = _read_cache_file()
    entry = cache.get("llamacpp_releases")
    if not force and isinstance(entry, dict):
        try:
            checked = datetime.fromisoformat(entry.get("checked_at", ""))
            if datetime.now() - checked <= RELEASES_CACHE_TTL:
                rels = entry.get("releases", [])
                # 最新 release 只有 cudart（发布中不完整快照）→ 缓存视为过期，
                # 强制重抓，避免"有新版本却不自动下载"（traps #18）
                if rels and _release_has_main_asset(rels[0]):
                    return rels, ""
        except ValueError:
            pass

    req = urllib.request.Request(
        f"{RELEASES_API}?per_page={per_page}", headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        releases = []
        for item in data if isinstance(data, list) else []:
            r = _release_from_api(item)
            if r:
                releases.append(r)
        if not releases:
            raise ValueError("未解析到任何 Windows 版本资产")
    except urllib.error.HTTPError as e:
        if isinstance(entry, dict) and entry.get("releases"):
            return entry["releases"], ""
        if e.code in (403, 429):
            return [], "GitHub API 请求受限（HTTP %d），请稍后重试" % e.code
        return [], "请求失败（HTTP %d），请稍后重试" % e.code
    except (urllib.error.URLError, TimeoutError) as e:
        if isinstance(entry, dict) and entry.get("releases"):
            return entry["releases"], ""
        reason = getattr(e, "reason", None) or str(e)
        return [], f"网络连接失败（{reason}），请检查网络连接"
    except ValueError as e:
        if isinstance(entry, dict) and entry.get("releases"):
            return entry["releases"], ""
        return [], str(e)

    cache["llamacpp_releases"] = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "releases": releases,
    }
    _write_cache_file(cache)
    return releases, ""


# ── 本机检测 ────────────────────────────────────────────────────

def get_local_version(exe_path):
    """后台线程运行 `llama-server --version` 解析 build 号。

    CUDA 版本会先初始化 GPU（1~3s），调用方必须在后台线程执行。
    返回 (build号或0, 原始输出用于显示)。
    """
    if not exe_path or not os.path.isfile(exe_path):
        return 0, ""
    try:
        r = subprocess.run(
            [exe_path, "--version"], capture_output=True, text=True,
            timeout=15, creationflags=NO_WINDOW)
        output = (r.stdout or "") + "\n" + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        error(f"llama-server --version 执行失败: {e}")
        return 0, str(e)
    return parse_version_output(output), output.strip()


def detect_gpu():
    """NVML 读取 NVIDIA 驱动与显卡信息（本机 API，毫秒级）。

    返回 {"nvidia": True, "driver": "566.36", "name": "RTX 4070",
    "cuda_13_ok": False, "cuda_12_ok": True}；无 N 卡/初始化失败 → {"nvidia": False}。
    不调用 nvmlShutdown：monitor_service 持有 NVML 句柄，避免干扰其生命周期。
    """
    info = {"nvidia": False}
    try:
        from pynvml import (
            nvmlInit, nvmlDeviceGetCount, nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetName, nvmlSystemGetDriverVersion,
        )
        nvmlInit()
        if nvmlDeviceGetCount() > 0:
            handle = nvmlDeviceGetHandleByIndex(0)
            name = nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            driver = nvmlSystemGetDriverVersion()
            if isinstance(driver, bytes):
                driver = driver.decode("utf-8", "replace")
            dt = driver_tuple(driver)
            info = {
                "nvidia": True,
                "driver": driver,
                "name": name,
                "cuda_12_ok": dt >= _CUDA12_MIN_DRIVER,
                "cuda_13_ok": dt >= _CUDA13_MIN_DRIVER,
            }
    except Exception as e:  # pynvml 未装 / 无 N 卡 / NVML 服务不可用
        error(f"版本管理 GPU 检测失败（NVML）: {e}")
    return info


def _cuda_major_from_imports(dll_path, read_limit=32 * 1024 * 1024):
    """从 ggml-cuda.dll 导入表推断 CUDA 大版本（导入的 cublas64_XX.dll）。

    有些发行目录不带 cudart/cublas DLL（运行时从系统 PATH 的 toolkit 加载），
    按目录文件名嗅探会失效；导入表里的 cublas64_13.dll 是编译期写死的，最可靠。
    """
    try:
        majors = []
        with open(dll_path, "rb") as f:
            remaining = read_limit
            while remaining > 0:
                chunk = f.read(min(8 * 1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                low = chunk.lower()
                for m in range(10, 20):
                    if f"cublas64_{m}.dll".encode() in low:
                        majors.append(m)
        return max(majors) if majors else None
    except OSError:
        return None


def local_variant_hint(exe_dir):
    """推断当前 llama.cpp 变体（通道级，如 "cuda-13"）。

    管理安装的目录优先读元数据（精确变体）；手动解压的目录依次尝试：
    目录内 cudart/cublas DLL 文件名 → ggml-cuda.dll 导入表 → 保守猜测。
    CUDA 通道只精确到大版本（12/13）：上游小版本号随 release 演进
    （13.1 → 13.3），规划层 plan_auto_download 按系列匹配最新资产。
    """
    if not exe_dir or not os.path.isdir(exe_dir):
        return ""
    meta_path = os.path.join(exe_dir, META_FILENAME)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            variant = meta.get("variant", "")
            if variant:
                return variant
        except (OSError, ValueError):
            pass
    try:
        names = set(os.listdir(exe_dir))
    except OSError:
        return ""
    if "ggml-cuda.dll" in names:
        dlls = find_cuda_dlls([exe_dir])
        if dlls:
            major = max(int(os.path.basename(p)[:-4].rsplit("_", 1)[1]) for p in dlls)
            return f"cuda-{major}"
        major = _cuda_major_from_imports(os.path.join(exe_dir, "ggml-cuda.dll"))
        if major:
            return f"cuda-{major}"
        return "cuda-12"
    if "ggml-vulkan.dll" in names:
        return "vulkan"
    if "ggml-sycl.dll" in names:
        return "sycl"
    return "cpu"


# CUDA 运行库 DLL 文件前缀（cudart 包内的文件；主 zip 不含这些）
_CUDA_DLL_PREFIXES = ("cudart64_", "cublas64_", "cublaslt64_", "nvrtc64_")


def find_cuda_dlls(search_dirs, cuda_major=None):
    """在目录列表中查找 CUDA 运行库 DLL，返回去重后的完整路径列表。

    cuda_major 指定时只匹配对应大版本（如 "13" 只认 cudart64_13.dll），
    避免 cuda-13.x 的新装目录误复制 12.x 的 DLL。同名 DLL 先出现的目录优先。
    """
    found = {}
    for d in search_dirs or []:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            low = f.lower()
            if not (low.endswith(".dll") and low.startswith(_CUDA_DLL_PREFIXES)):
                continue
            if cuda_major and not low[:-4].endswith(f"_{cuda_major}"):
                continue
            found.setdefault(low, os.path.join(d, f))
    return list(found.values())


def copy_cuda_dlls(search_dirs, dest_dir, cuda_major=None):
    """把找到的 CUDA 运行库 DLL 复制进 dest_dir，返回复制的文件数。"""
    copied = 0
    os.makedirs(dest_dir, exist_ok=True)
    for src in find_cuda_dlls(search_dirs, cuda_major):
        try:
            shutil.copy2(src, os.path.join(dest_dir, os.path.basename(src)))
            copied += 1
        except OSError:
            continue
    return copied


def cudart_plan(release, variant, copy_sources, toolkit_has=None):
    """决定 CUDA 运行库获取方式：'copy' / 'download' / 'none'。

    背景：Windows CUDA 主 zip 不含 cudart/cublas DLL；每个版本是全新目录，
    "当前目录有 DLL" ≠ "新版本目录有 DLL"，必须按目标目录的实际需求决策。
    优先级：现有目录按大版本匹配复制（免下载 380MB）→ 系统已装匹配版本
    CUDA toolkit → 下载 cudart 包。
    """
    if not variant.startswith("cuda-"):
        return "none"
    major = variant.split("-")[1].split(".")[0]
    if find_cuda_dlls(copy_sources, major):
        return "copy"
    if toolkit_has is None:
        toolkit_has = has_cudart("", major)
    if toolkit_has:
        return "none"
    if release and f"cudart-{variant}" in (release.get("assets") or {}):
        return "download"
    return "none"


def _cuda_series(variant):
    """'cuda-13.3' / 'cuda-13' → 'cuda-13'（通道 = CUDA 大版本系列）；非 cuda 原样返回。"""
    if variant and variant.startswith("cuda-"):
        return "cuda-" + variant.split("-")[1].split(".")[0]
    return variant


def _release_has_main_asset(release):
    """release 是否含主包资产（只有 cudart 视为发布中不完整快照）。"""
    assets = (release or {}).get("assets") or {}
    return any(not k.startswith("cudart-") for k in assets)


def plan_auto_download(latest, local_build, local_variant, gpu_info, installed_keys):
    """后台静默下载计划：取「可下载的最新 release」的当前通道 + 推荐变体。

    latest 可以是单个 release dict 或 release 列表（从最新开始）：
    - 列表时自动跳过无主资产/发布中快照（上游资产逐步上传，b10933 可能
      只有 cudart），取第一个有可下载内容的 release —— "可下载的最新推荐版本"；
    - 单 dict 时保持历史行为（兼容既有单测）。

    返回待安装的 [(tag, variant)]：
    - 去重（当前通道与推荐相同时只装一次）
    - 跳过已安装的 {tag}-{variant}
    - 当前通道仅在新于本地 build 时更新；推荐变体未安装过即下载（供用户一键切换）
    """
    releases = latest if isinstance(latest, list) else ([latest] if latest else [])
    for release in releases:
        if not release or not release.get("build") or not release.get("assets"):
            continue
        tag, build = release["tag"], release["build"]
        variants = [v for v in release["assets"] if not v.startswith("cudart-")]
        if not variants:
            continue  # 只有 cudart = 发布中不完整快照 → 跳过，看下一个版本
        channel = local_variant
        if channel and channel not in variants and _cuda_series(channel).startswith("cuda-"):
            series = _cuda_series(channel)
            same = sorted((v for v in variants if _cuda_series(v) == series), reverse=True)
            channel = same[0] if same else ""  # 通道已停产 → 只装推荐
        recommended = recommend_variant(gpu_info, variants)
        targets = []
        if channel and channel in variants:
            targets.append(channel)
        if recommended and recommended in variants and recommended not in targets:
            targets.append(recommended)
        installed = set(installed_keys or [])
        plans = []
        for v in targets:
            if f"{tag}-{v}" in installed:
                continue
            if v == channel and local_build and build <= local_build:
                continue
            plans.append((tag, v))
        if plans:
            return plans
        # 该 release 完整（有主资产）但无待下载项（已装齐/通道已最新）→ 就此停止，
        # 不再降级去下载更旧的版本
        return []
    return []


def _find_cudart_in(paths, cuda_major=None):
    for base in paths:
        try:
            if not os.path.isdir(base):
                continue
            for f in os.listdir(base):
                low = f.lower()
                if low.startswith(("cublas64_", "cudart64_")) and low.endswith(".dll"):
                    if cuda_major and not low[:-4].endswith(f"_{cuda_major}"):
                        continue
                    return True
        except OSError:
            continue
    return False


def has_cudart(exe_dir="", cuda_major=None):
    """系统是否已有 CUDA 运行库（决定是否需要下载 cudart 包，约 380MB）。

    检查顺序：exe 同目录（升级场景旧 DLL 会带过去）→ 系统 CUDA toolkit 安装路径。
    cuda_major 指定时只认对应大版本（如 "12" 只认 cublas64_12.dll）。
    """
    candidates = []
    if exe_dir:
        candidates.append(exe_dir)
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    toolkit = os.path.join(prog_files, "NVIDIA GPU Computing Toolkit", "CUDA")
    if os.path.isdir(toolkit):
        try:
            for v in os.listdir(toolkit):
                # toolkit 的 CUDA DLL 在 bin 或 bin\x64（取决于安装器版本）
                candidates.append(os.path.join(toolkit, v, "bin"))
                candidates.append(os.path.join(toolkit, v, "bin", "x64"))
        except OSError:
            pass
    return _find_cudart_in(candidates, cuda_major)


# ── 安装 ────────────────────────────────────────────────────────

def sha256_of(path, chunk=1024 * 1024):
    """计算文件 SHA256（安装前 digest 校验用）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _safe_extract_zip(zip_path, dest_dir):
    """解压 zip 到 dest_dir，逐条目防 zip-slip 路径穿越（commonpath 严格判定）。"""
    dest_abs = os.path.normcase(os.path.abspath(dest_dir))
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            target = os.path.abspath(os.path.join(dest_dir, info.filename))
            if os.path.normcase(os.path.commonpath([dest_abs, target])) != dest_abs:
                raise ValueError(f"压缩包内含非法路径: {info.filename}")
        zf.extractall(dest_dir)


def extract_zip_into(zip_path, dest_dir):
    """把 zip 内容解压进已存在的目录（cudart 包合并进版本目录用，防穿越）。"""
    os.makedirs(dest_dir, exist_ok=True)
    _safe_extract_zip(zip_path, dest_dir)


def install_from_zip(zip_path, dest_root, tag, variant):
    """把 zip 安装为版本化目录 {dest_root}/{tag}-{variant}/。

    流程：解压到 .tmp 临时目录（防穿越）→ 校验 llama-server.exe →
    写元数据 → 原子改名为最终目录（已存在则先删除）。
    返回新 llama-server.exe 完整路径；失败抛异常（含友好信息）。
    """
    if not os.path.isfile(zip_path):
        raise ValueError("zip 文件不存在")
    os.makedirs(dest_root, exist_ok=True)
    final_dir = os.path.join(dest_root, f"{tag}-{variant}")
    tmp_dir = os.path.join(dest_root, f".tmp-{tag}-{variant}")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        _safe_extract_zip(zip_path, tmp_dir)
        if not os.path.isfile(os.path.join(tmp_dir, "llama-server.exe")):
            raise ValueError("压缩包内未找到 llama-server.exe（不是有效的 llama.cpp 发行包）")
        atomic_write_json(os.path.join(tmp_dir, META_FILENAME), {
            "tag": tag,
            "variant": variant,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
        }, indent=2, ensure_ascii=False)
        if os.path.exists(final_dir):
            shutil.rmtree(final_dir)  # 旧目录若被运行中服务占用会抛错，提示用户
        os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)  # 解压/校验失败清理现场
        raise
    return os.path.join(final_dir, "llama-server.exe")


def list_installed(dest_root):
    """扫描安装根目录下所有合法版本目录（含元数据文件的子目录）。

    返回 [{dir, exe, tag, variant, date, is_current}]，新→旧排序。
    is_current 由调用方传入的 current_dir 标记（settings.llamacpp_path 的父目录）。
    """
    result = []
    if not dest_root or not os.path.isdir(dest_root):
        return result
    for name in os.listdir(dest_root):
        if name.startswith(".") or name == "zips":
            continue
        sub = os.path.join(dest_root, name)
        meta_path = os.path.join(sub, META_FILENAME)
        exe_path = os.path.join(sub, "llama-server.exe")
        if not os.path.isfile(meta_path) or not os.path.isfile(exe_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        result.append({
            "dir": sub,
            "exe": exe_path,
            "tag": meta.get("tag", ""),
            "variant": meta.get("variant", ""),
            "date": meta.get("installed_at", "")[:16].replace("T", " "),
        })
    # 按 build 号排序（tag 字符串排序在跨位数时错乱：b9999 > b10615）
    result.sort(key=lambda r: tag_to_build(r.get("tag", "")), reverse=True)
    return result


# ── 版本切换（联动 settings + .bat 脚本）────────────────────────

def switch_version(new_exe_path, settings=None, script_service=None):
    """切换当前 llama.cpp：改配置 + 批量替换所有 .bat 中的 exe 目录。

    返回更新的脚本数；新旧目录相同返回 0（仍会更新 settings 指向）。
    """
    settings = settings or Settings.get_instance()
    service = script_service or ScriptService()

    old_exe = settings.llamacpp_path
    new_dir = os.path.dirname(os.path.abspath(new_exe_path))
    old_dir = os.path.dirname(os.path.abspath(old_exe)) if old_exe else ""

    settings.llamacpp_path = normalize_path(os.path.normpath(new_exe_path))
    settings.save()

    if not old_dir or os.path.normcase(old_dir) == os.path.normcase(new_dir):
        return 0

    updated = 0
    for entry in service.load_scripts():
        new_content = replace_bat_dir(entry.content, old_dir, new_dir)
        if new_content != entry.content:
            entry.content = new_content
            service.save_script(entry)
            updated += 1
    return updated
