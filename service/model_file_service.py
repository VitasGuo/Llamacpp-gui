"""本地模型文件管理服务：配对视觉投影、删除 .gguf 文件。

"本地模型"视图的删除流程走这里（service 层，不依赖 Qt）。删除**永久生效**
（不进回收站），故确认与拦截都在 UI 层做前置把关；本模块只负责"逐项删、
不中断、如实回报"。
"""
import os

from utils.path_utils import normalize_path


def pair_mmproj(path, models):
    """在已扫描的模型列表里找 `path` 同目录的配对视觉投影（mmproj）路径。

    `models` 为 `model_scanner.list_local_models` 的结果（避免重复 walk 磁盘）。
    与 `script_builder.find_mmproj` 同规则：同目录、文件名含 mmproj、按名取首个。
    找不到（或自身就是 mmproj）返回空串。
    """
    if not path:
        return ""
    target_dir = _dir_of(path)
    candidates = []
    for m in models or []:
        p = m.get("path", "")
        if not p or p == path:
            continue
        if not m.get("is_mmproj"):
            continue
        if _dir_of(p) == target_dir:
            candidates.append(p)
    return sorted(candidates, key=lambda p: os.path.basename(p).lower())[0] if candidates else ""


def delete_model_files(paths):
    """逐个删除模型文件；单个失败不中断，返回执行结果。

    返回 {"freed": 释放字节数, "deleted": [成功路径], "errors": ["<路径>: 原因"]}。
    文件本就不存在视为成功（幂等，不计入 freed）；被占用（Windows 下 llama-server
    以 mmap 打开时）会抛 PermissionError，错误文案里点明"可能正在运行"。
    """
    freed = 0
    deleted = []
    errors = []
    for p in paths or []:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        try:
            os.remove(p)
        except FileNotFoundError:
            deleted.append(p)  # 已不存在：目标已达成，幂等
            continue
        except PermissionError as e:
            errors.append(f"{p}: 文件被占用（可能该模型正在运行，请先在运行控制结束）"
                          f" [{e}]")
            continue
        except OSError as e:
            errors.append(f"{p}: {e}")
            continue
        freed += size
        deleted.append(p)
    return {"freed": freed, "deleted": deleted, "errors": errors}


def _dir_of(path):
    """取父目录（统一正斜线、小写），用于同目录判定。"""
    return normalize_path(os.path.dirname(path) or "").lower()