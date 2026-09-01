"""本地模型扫描服务：递归检索目录下的 .gguf 模型文件。"""
import os


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