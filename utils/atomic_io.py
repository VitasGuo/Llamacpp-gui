"""原子 JSON 写入工具。

所有 JSON 持久化（配置、脚本清单、下载队列、运行时状态等）统一走这里：
先写同目录临时文件并 fsync，再用 os.replace 原子替换目标文件。
进程中途崩溃/断电时，要么保留旧文件，要么留下完整新文件，
不会出现写坏一半的 JSON 导致下次读取静默回退默认值、数据无声丢失。
"""

import os
import json
import uuid


def atomic_write_json(path, data, indent=2, ensure_ascii=False):
    """原子写入 JSON 文件：临时文件 + fsync + os.replace。

    - 自动创建父目录（os.makedirs(exist_ok=True)）
    - 临时文件名含 uuid4().hex，保证并发线程各自独立，互不覆盖
    - 写失败时尽力删除残留临时文件，OSError 照常向上抛（调用方容错行为不变）
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    tmp_path = os.path.join(
        parent, f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        # 尽力清理残留临时文件，删除失败不影响原始错误传播
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
