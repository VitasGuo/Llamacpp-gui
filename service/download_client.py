"""通用 HTTP 断点续传下载。"""
import os
import urllib.request

from utils.logger import error

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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            # 兼容处理：新版本 urllib 用 resp.status，个别旧版本用 resp.getcode()
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            # 断点续传时若服务器/代理忽略 Range 而返回 200（整文件内容），
            # 继续追加写会使文件内容重复拼接、损坏，故重置为从头下载
            if resume_pos > 0 and status != 206:
                mode = "wb"
                resume_pos = 0

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
    except Exception as e:
        error(f"下载请求失败 {url} (resume_pos={resume_pos}): {e}")
        raise

    return resume_pos, total
