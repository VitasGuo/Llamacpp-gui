import os
import re


def validate_path(path):
    """验证目录路径是否存在。"""
    return os.path.exists(path) and os.path.isdir(path)


def validate_file(path):
    """验证文件是否存在。"""
    return os.path.exists(path) and os.path.isfile(path)


def validate_gguf(path):
    """验证是否为存在的 .gguf 文件。"""
    return validate_file(path) and path.lower().endswith(".gguf")


def validate_llamacpp_file(path):
    """验证是否为 llama-server.exe 文件。"""
    if not os.path.isfile(path):
        return False
    return os.path.basename(path).lower() == "llama-server.exe"


def sanitize_filename(name):
    """移除文件名中的非法字符。"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)
