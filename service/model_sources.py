"""模型下载来源注册表。"""
from service import hf_mirror, modelscope

SOURCE_MODELSCOPE = "modelscope"
SOURCE_HF_MIRROR = "hf_mirror"

SOURCES = {
    SOURCE_MODELSCOPE: {
        "label": "ModelScope",
        "short_label": "ModelScope",
        "search": modelscope.search_models,
        "files": modelscope.list_model_files,
        "url": modelscope.get_download_url,
    },
    SOURCE_HF_MIRROR: {
        "label": "Hugging Face (hf-mirror.com)",
        "short_label": "HF 镜像",
        "search": hf_mirror.search_models,
        "files": hf_mirror.list_model_files,
        "url": hf_mirror.get_download_url,
    },
}


def get_label(source: str) -> str:
    entry = SOURCES.get(source)
    return entry["label"] if entry else source


def get_short_label(source: str) -> str:
    entry = SOURCES.get(source)
    return entry["short_label"] if entry else source


def search_models(source: str, keyword: str, page: int = 1, page_size: int = 20) -> dict:
    return SOURCES[source]["search"](keyword, page, page_size)


def list_model_files(source: str, model_id: str) -> list[dict]:
    return SOURCES[source]["files"](model_id)


def get_download_url(source: str, model_id: str, file_path: str) -> str:
    return SOURCES[source]["url"](model_id, file_path)
