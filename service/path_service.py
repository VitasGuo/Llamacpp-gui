import os
import shutil


def ensure_webui():
    """将内置 chat_webui 静态文件复制到 data/webui。"""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "chat_webui")
    dst = os.path.abspath("data/webui")
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for fname in ["chat.html", "chat.css", "chat.js", "marked.min.js"]:
        s = os.path.join(src, fname)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(dst, fname))
