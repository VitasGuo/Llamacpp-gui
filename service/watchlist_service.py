"""模型更新追踪服务：关注 ModelScope 模型仓库，检测远端是否有新版本。

范围：
- 本地模型目录（settings.model_dir 下 models/{org}/{repo}/）自动发现并加入关注；
- 搜索结果可手动点"追踪"添加；
- 检查更新：对每个关注模型调 ModelScope legacy 详情接口取 LastUpdatedTime
  （Unix 秒），与上次记录对比，大于则视为"有更新"。
- 持久化关注列表到 data/model_watchlist.json（含基线，避免首次加入即误报更新）。
"""
import json
import os
import urllib.request
from datetime import datetime

from config import WATCHLIST_FILE, WATCHLIST_IGNORED_FILE
from service.model_scanner import scan_gguf_files
from utils.atomic_io import atomic_write_json
from utils.logger import error

_LEGACY_MODEL_URL = "https://modelscope.cn/api/v1/models/{quoted_id}"
_TIMEOUT = 15


def _load_watchlist() -> list[dict]:
    """读取关注列表；文件缺失/损坏/结构异常时返回空列表。"""
    try:
        if not os.path.exists(WATCHLIST_FILE):
            return []
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("model_id")]
    except (OSError, ValueError):
        pass
    return []


def load_watchlist() -> list[dict]:
    """公开读取关注列表（UI 层用）。"""
    return _load_watchlist()


def _save_watchlist(watchlist: list[dict]):
    os.makedirs(os.path.dirname(WATCHLIST_FILE) or ".", exist_ok=True)
    atomic_write_json(WATCHLIST_FILE, watchlist, ensure_ascii=False, indent=2)


def _load_ignored() -> set[str]:
    """读取用户显式移除（不再自动追踪）的模型 id 集合。"""
    try:
        if not os.path.exists(WATCHLIST_IGNORED_FILE):
            return set()
        with open(WATCHLIST_IGNORED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {d for d in data if isinstance(d, str) and d}
    except (OSError, ValueError):
        pass
    return set()


def _save_ignored(ignored: set[str]):
    os.makedirs(os.path.dirname(WATCHLIST_IGNORED_FILE) or ".", exist_ok=True)
    atomic_write_json(WATCHLIST_IGNORED_FILE, sorted(ignored), ensure_ascii=False, indent=2)


def discover_local_models(model_dir: str) -> set[str]:
    """扫描本地模型目录，反推 ModelScope 仓库 id。

    期望目录结构 {model_dir}/models/{org}/{repo}/xxx.gguf → {org}/{repo}。
    不在 models/ 层级下的（如根目录直放的 gguf）无法反推归属，忽略。
    """
    found = set()
    if not model_dir:
        return found
    for path in scan_gguf_files(model_dir):
        segments = path.replace("\\", "/").split("/")
        # 定位 "models" 之后的 org/repo 两段
        for i, seg in enumerate(segments):
            if seg == "models" and i + 2 < len(segments):
                org, repo = segments[i + 1], segments[i + 2]
                if org and repo:
                    found.add(f"{org}/{repo}")
                break
    return found


def merge_local_models(model_dir: str, watchlist: list[dict] | None = None) -> list[dict]:
    """把本地已发现的模型仓库并入关注列表（已存在、被用户忽略的跳过），不动基线。

    用户显式移除过的本地模型（ignored）不会被自动重新加入，保证"移除"真正生效。
    """
    watchlist = watchlist if watchlist is not None else _load_watchlist()
    existing = {w["model_id"] for w in watchlist}
    ignored = _load_ignored()
    for mid in sorted(discover_local_models(model_dir)):
        if mid in existing or mid in ignored:
            continue
        watchlist.append({
            "model_id": mid,
            "source": "modelscope",
            "added_by": "local",
            "last_updated": None,  # 待首次检查填充基线
            "last_checked": "",
        })
    _save_watchlist(watchlist)
    return watchlist


def add_manual_model(model_id: str, watchlist: list[dict] | None = None) -> list[dict]:
    """手动追踪一个模型（搜索结果点"追踪"）。已存在则跳过；同时解除其忽略标记。"""
    watchlist = watchlist if watchlist is not None else _load_watchlist()
    if any(w["model_id"] == model_id for w in watchlist):
        return watchlist
    ignored = _load_ignored()
    if model_id in ignored:
        ignored.discard(model_id)
        _save_ignored(ignored)
    watchlist.append({
        "model_id": model_id,
        "source": "modelscope",
        "added_by": "manual",
        "last_updated": None,
        "last_checked": "",
    })
    _save_watchlist(watchlist)
    return watchlist


def remove_model(model_id: str, watchlist: list[dict] | None = None):
    """从关注列表移除，并标记为忽略（本地模型不再被自动加回）。"""
    watchlist = watchlist if watchlist is not None else _load_watchlist()
    watchlist = [w for w in watchlist if w["model_id"] != model_id]
    _save_watchlist(watchlist)
    ignored = _load_ignored()
    ignored.add(model_id)
    _save_ignored(ignored)
    return watchlist


def fetch_last_updated(model_id: str) -> int:
    """查询 ModelScope 仓库最近更新时间（Unix 秒）。失败抛异常（由调用方按行处理）。"""
    quoted = urllib.request.quote(model_id, safe="")
    req = urllib.request.Request(
        _LEGACY_MODEL_URL.format(quoted_id=quoted),
        headers={"User-Agent": "llamacpp-gui/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    d = data.get("Data") or {}
    t = d.get("LastUpdatedTime")
    try:
        return int(t)
    except (TypeError, ValueError):
        raise ValueError(f"模型 {model_id} 返回异常更新时间: {t!r}")


def check_updates(watchlist: list[dict] | None = None) -> list[dict]:
    """逐个检查关注模型是否有更新，就地更新基线并存盘。

    返回结果列表 [{model_id, has_update, last_updated, error}]：
    - 首次记录（last_updated 为空）→ 仅填基线，has_update=False（不误报）；
    - 远端 LastUpdatedTime > 上次记录 → has_update=True 并刷新基线；
    - 单个模型网络失败 → error 设置，不影响其他模型。
    """
    watchlist = watchlist if watchlist is not None else _load_watchlist()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    results = []
    for w in watchlist:
        mid = w["model_id"]
        try:
            t = fetch_last_updated(mid)
        except Exception as e:
            error(f"检查模型更新失败 {mid}: {e}")
            results.append({"model_id": mid, "has_update": False,
                            "last_updated": w.get("last_updated"), "error": str(e)})
            continue
        prev = w.get("last_updated")
        has_update = bool(prev) and t > prev
        w["last_updated"] = t
        w["last_checked"] = now
        results.append({"model_id": mid, "has_update": has_update,
                        "last_updated": t, "error": ""})
    _save_watchlist(watchlist)
    return results