"""通用 HTTP 断点续传下载。"""
import os
import urllib.request

CHUNK_SIZE = 1024 * 1024


def download_file(url: str, dest_path: str, resume_pos: int = 0, chunk_callback=None):
    headers = {
        "User-Agent": "llamacpp-gui/1.0",
    }
    if resume_pos > 0:
        headers["Range"] = f"bytes={resume_pos}-"

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    mode = "ab" if resume_pos > 0 else "wb"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = resume_pos
        if resume_pos == 0:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                total = int(content_length)
        else:
            content_range = resp.headers.get("Content-Range", "")
            if "/" in content_range:
                total = int(content_range.split("/")[1])

        with open(dest_path, mode) as f:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                resume_pos += len(chunk)
                if chunk_callback:
                    chunk_callback(resume_pos, total)

    return resume_pos, total
