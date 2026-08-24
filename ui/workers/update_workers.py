"""llama.cpp 版本检查和软件更新检查工作线程。

两个 worker 共用同一套检查逻辑（_run_update_check）：
- 语义化版本比较（compare_semver）：去 v 前缀、按数字段比较、预发布低于正式版
- 本地缓存 data/update_cache.json（release 信息 + checked_at 时间戳）：
  6 小时内直接读缓存不发网络请求；403/429/超时/网络错误时回退缓存
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal

from config import UPDATE_CACHE_FILE

# 缓存有效期：6 小时内直接读缓存，不发网络请求
CACHE_TTL = timedelta(hours=6)

# 预发布标识（按字典序 alpha < beta < rc）
_PRERELEASE_TAGS = ("alpha", "beta", "rc")

_LLMACPP_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
_APP_URL = "https://api.github.com/repos/kkblank/Llamacpp-gui/releases/latest"
_HEADERS = {"User-Agent": "llamacpp-gui/1.0", "Accept": "application/vnd.github+json"}


# ── 语义化版本比较 ──────────────────────────────────────────────

def _parse_version(version):
    """拆分版本串为 (数字段元组, 预发布串或 None)。

    - 去 v/V 前缀
    - 预发布标识 alpha/beta/rc（可带序号，如 rc1、beta.2）
    - 数字部分按 '.' 分段取前导数字，非数字段记 0
    """
    v = version.strip().lower()
    if v.startswith("v"):
        v = v[1:]
    pre = None
    for tag in _PRERELEASE_TAGS:
        idx = v.find(tag)
        if idx > 0:
            rest = v[idx + len(tag):]
            if rest == "" or rest[0].isdigit() or rest[0] in ".-+_":
                pre = v[idx:]
                v = v[:idx]
                break
    segments = []
    for part in v.split("."):
        m = re.match(r"\d+", part)
        segments.append(int(m.group(0)) if m else 0)
    return tuple(segments), pre


def _prerelease_key(pre):
    """预发布排序键：(标识, 序号)，如 alpha < beta < rc、rc1 < rc2。"""
    m = re.match(r"([a-z]+)[.-]?(\d+)?", pre)
    if not m:
        return (pre, 0)
    return (m.group(1), int(m.group(2) or 0))


def compare_semver(a, b):
    """比较两个版本串，返回 -1/0/1（a 相对 b）。

    规则：忽略 v/V 前缀；按 '.' 分段的数字逐段比较（段数不足按 0 补齐）；
    数字段相同时，预发布（rc/beta/alpha）低于正式版；同为预发布时
    比较标识与序号（alpha < beta < rc，rc1 < rc2）。
    """
    seg_a, pre_a = _parse_version(a)
    seg_b, pre_b = _parse_version(b)
    n = max(len(seg_a), len(seg_b))
    seg_a += (0,) * (n - len(seg_a))
    seg_b += (0,) * (n - len(seg_b))
    if seg_a != seg_b:
        return -1 if seg_a < seg_b else 1
    if (pre_a is None) == (pre_b is None):
        if pre_a is None:
            return 0
        key_a, key_b = _prerelease_key(pre_a), _prerelease_key(pre_b)
        return -1 if key_a < key_b else (1 if key_a > key_b else 0)
    return -1 if pre_a is not None else 1


# ── 本地缓存 ────────────────────────────────────────────────────

def _read_cache():
    """读取缓存文件；不存在或损坏时返回空 dict（不影响主流程）。"""
    try:
        with open(UPDATE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(cache):
    try:
        os.makedirs(os.path.dirname(UPDATE_CACHE_FILE) or ".", exist_ok=True)
        with open(UPDATE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 缓存写失败不影响本次检查结果


def _cache_entry(cache, key):
    """取某仓库的缓存条目并解析为 {date, checked_at_hm, tag, fresh}；无效返回 None。"""
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    published = entry.get("published_at", "")
    checked = entry.get("checked_at", "")
    if not published or not checked:
        return None
    try:
        published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        checked_dt = datetime.fromisoformat(checked)
    except ValueError:
        return None
    return {
        "date": published_dt.strftime("%Y-%m-%d"),
        "checked_at_hm": checked_dt.strftime("%H:%M"),
        "tag": entry.get("tag", ""),
        "fresh": datetime.now() - checked_dt <= CACHE_TTL,
    }


def _cached_result(entry):
    """缓存回退/命中的 UI 结果：日期 + 缓存时间标注。"""
    return f"{entry['date']}（缓存于 {entry['checked_at_hm']}）", ""


# ── 检查逻辑（两个 worker 共用）────────────────────────────────

def _fetch_latest_release(url):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    published = data.get("published_at", "")
    if not published:
        raise ValueError("未能获取到版本信息")
    return data.get("tag_name", ""), published


def _run_update_check(repo_url, cache_key):
    """执行一次更新检查，返回 (date_str, error_msg) —— 与 result_signal 契约一致。

    流程：缓存 6h 内有效 → 直接返回（标注缓存时间）；否则发请求：
    成功 → 用 compare_semver 防御（API 返回版本低于缓存版本时保留缓存）
    并更新缓存；403/429/超时/网络错误 → 回退缓存（如有，标注缓存时间），
    无缓存则返回友好错误提示。
    """
    entry = _cache_entry(_read_cache(), cache_key)
    if entry and entry["fresh"]:
        return _cached_result(entry)

    try:
        tag, published = _fetch_latest_release(repo_url)
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
    except urllib.error.HTTPError as e:
        if entry:
            return _cached_result(entry)
        if e.code in (403, 429):
            return "", f"GitHub API 请求受限（HTTP {e.code}），请稍后重试"
        return "", f"请求失败（HTTP {e.code}），请稍后重试"
    except urllib.error.URLError as e:
        if entry:
            return _cached_result(entry)
        reason = getattr(e, "reason", None) or str(e)
        if "timed out" in str(reason):
            return "", "请求超时，请检查网络连接"
        return "", f"网络连接失败（{reason}），请检查网络连接"
    except ValueError as e:
        if entry:
            return _cached_result(entry)
        if isinstance(e, json.JSONDecodeError):
            return "", "版本信息解析失败"
        return "", str(e)
    except TimeoutError:
        if entry:
            return _cached_result(entry)
        return "", "请求超时，请检查网络连接"

    # semver 防御：API 返回的版本低于缓存版本（异常状态）时保留缓存
    if entry and entry["tag"] and tag and compare_semver(tag, entry["tag"]) < 0:
        return _cached_result(entry)

    cache = _read_cache()
    cache[cache_key] = {
        "tag": tag,
        "published_at": published,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_cache(cache)
    return date_str, ""


class CheckUpdateWorker(QThread):
    """检查 llama.cpp 最新版本（GitHub releases/latest，带 6h 本地缓存）。"""
    result_signal = pyqtSignal(str, str)

    def run(self):
        date_str, error_msg = _run_update_check(_LLMACPP_URL, "llamacpp")
        self.result_signal.emit(date_str, error_msg)


class CheckAppUpdateWorker(QThread):
    """检查本应用最新版本（GitHub releases/latest，带 6h 本地缓存）。"""
    result_signal = pyqtSignal(str, str)

    def run(self):
        date_str, error_msg = _run_update_check(_APP_URL, "app")
        self.result_signal.emit(date_str, error_msg)
