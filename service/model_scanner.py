"""本地模型扫描服务：递归检索目录下的 .gguf 模型文件。"""
import os

from utils.path_utils import normalize_path


def scan_gguf_files(directory, recursive=True):
    """返回目录下（可选递归）所有 .gguf 文件的绝对路径（按路径排序）。

    递归时跳过隐藏目录（以 . 开头的目录）。返回空列表表示无结果或目录无效。
    """
    results = []
    if not directory or not os.path.isdir(directory):
        return results
    for root_dir, dirs, files in os.walk(directory):
        if not recursive:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".gguf"):
                results.append(os.path.join(root_dir, f))
    return sorted(results)


def list_local_models(directory):
    """列出本地模型目录下的 .gguf 文件（供"本地模型"管理视图）。

    返回 [{"path"(正斜线), "name", "size"(字节), "mtime"(float|None),
    "is_mmproj"(bool)}]，按文件名排序。目录无效返回 []；单个文件
    stat 失败（权限/被独占）时该文件 size=0、mtime=None，不影响其余。
    """
    models = []
    for p in scan_gguf_files(directory):
        name = os.path.basename(p)
        try:
            st = os.stat(p)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, None
        models.append({
            "path": normalize_path(p),
            "name": name,
            "size": size,
            "mtime": mtime,
            "is_mmproj": "mmproj" in name.lower(),
        })
    return sorted(models, key=lambda m: m["name"].lower())