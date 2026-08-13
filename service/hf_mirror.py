"""Hugging Face 镜像源客户端（hf-mirror.com）。"""
import json
import urllib.request
from urllib.parse import quote

API_BASE = "https://hf-mirror.com"
REVISION = "main"


def _map_model(item: dict) -> dict:
    return {
        "id": item.get("id") or item.get("modelId", ""),
        "params": 0,
        "pipeline_tag": item.get("pipeline_tag", ""),
        "library_name": item.get("library_name", ""),
        "downloads": item.get("downloads", 0),
        "last_modified": item.get("lastModified") or item.get("createdAt", ""),
    }


def search_models(keyword: str, page: int = 1, page_size: int = 20) -> dict:
    limit = page_size + 1
    offset = (page - 1) * page_size
    url = (
        f"{API_BASE}/api/models?search={quote(keyword)}"
        f"&limit={limit}&offset={offset}&full=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "llamacpp-gui/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                has_next = len(data) > page_size
                models = data[:page_size]
                return {
                    "models": [_map_model(m) for m in models],
                    "total_count": 0,
                    "page": page,
                    "page_size": page_size,
                    "has_next": has_next,
                }
    except Exception as e:
        return {
            "models": [],
            "total_count": 0,
            "page": page,
            "page_size": page_size,
            "has_next": False,
            "error": str(e),
        }
    return {
        "models": [],
        "total_count": 0,
        "page": page,
        "page_size": page_size,
        "has_next": False,
    }


def list_model_files(model_id: str, revision: str = REVISION) -> list[dict]:
    quoted_id = quote(model_id, safe="/")
    quoted_rev = quote(revision, safe="")
    url = f"{API_BASE}/api/models/{quoted_id}/tree/{quoted_rev}?recursive=true"
    req = urllib.request.Request(url, headers={"User-Agent": "llamacpp-gui/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return [
                    {
                        "Path": item.get("path", ""),
                        "Size": item.get("size", 0),
                    }
                    for item in data
                    if item.get("type") == "file"
                    and item.get("path", "").lower().endswith(".gguf")
                ]
    except Exception:
        return []
    return []


def get_download_url(model_id: str, file_path: str, revision: str = REVISION) -> str:
    quoted_id = quote(model_id, safe="/")
    quoted_path = quote(file_path, safe="/")
    quoted_rev = quote(revision, safe="")
    return f"{API_BASE}/{quoted_id}/resolve/{quoted_rev}/{quoted_path}"
