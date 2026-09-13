"""版本管理标签页的后台工作线程。

三个 worker（遵守 AGENTS.md：QThread 放 ui/workers/，通过 pyqtSignal 与 UI 通信）：
- LocalInfoWorker：本地 llama-server --version（CUDA 版需 1~3s）+ NVML 驱动检测
- ReleasesWorker：GitHub releases 列表（1h 缓存）
- InstallWorker：下载（断点续传/可取消）→ SHA256 校验 → 解压安装；
  支持手动导入本地 zip（跳过下载直接安装）

暂停语义：InstallWorker 不做真正的暂停状态机——"暂停/继续"由 UI 取消当前
worker 后重新创建实现（下载从本地已有字节数自动续传），半成品 zip 始终保留。
"""
import os
import time

from PyQt6.QtCore import QThread, pyqtSignal

from service import llamacpp_update_service as svc
from service.download_client import download_file
from utils.logger import error


class _Cancelled(Exception):
    pass


class LocalInfoWorker(QThread):
    """本地版本 + GPU 信息检测（必须后台：--version 对 CUDA 版要 1~3s）。"""
    result_signal = pyqtSignal(dict)  # {build, raw_output, gpu, variant_hint, error}

    def __init__(self, exe_path, parent=None):
        super().__init__(parent)
        self.exe_path = exe_path

    def run(self):
        result = {"build": 0, "raw_output": "", "gpu": {"nvidia": False},
                  "variant_hint": "", "error": ""}
        try:
            build, output = svc.get_local_version(self.exe_path)
            result["build"] = build
            result["raw_output"] = output
            if not build and self.exe_path:
                result["error"] = "未能解析本地版本号（可尝试直接运行 llama-server --version）"
            result["variant_hint"] = svc.local_variant_hint(os.path.dirname(self.exe_path))
            result["gpu"] = svc.detect_gpu()
        except Exception as e:  # 任何意外都不允许带崩 GUI
            error(f"本地版本检测异常: {e}")
            result["error"] = str(e)
        self.result_signal.emit(result)


class ReleasesWorker(QThread):
    """GitHub releases 列表（svc 层带 1h 缓存与错误回退）。"""
    result_signal = pyqtSignal(list, str)  # (releases, error_msg)

    def run(self):
        releases, err = svc.fetch_releases()
        self.result_signal.emit(releases, err)


class InstallWorker(QThread):
    """下载 + 校验 + 解压安装（多文件任务顺序执行，进度按总字节聚合）。

    tasks: [{"url", "dest", "digest", "size", "label"}]
    local_zip: 手动导入模式（非空时忽略 tasks，直接解压安装）
    dll_sources: CUDA 运行库 DLL 的复制来源目录（当前 llama.cpp 目录 + 已安装版本目录）；
                 cudart 包未下载时，安装完成后从这里按大版本匹配复制
    """
    progress_signal = pyqtSignal(int, int, float)  # 当前总字节, 总字节, 速度 B/s
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str)   # (成功, 新exe路径或错误信息, tag)

    def __init__(self, tasks, dest_root, tag, variant, local_zip=None,
                 dll_sources=None, parent=None):
        super().__init__(parent)
        self.tasks = tasks or []
        self.dest_root = dest_root
        self.tag = tag
        self.variant = variant
        self.local_zip = local_zip
        self.dll_sources = dll_sources or []
        self._cancelled = False

    def cancel(self):
        """软取消：停止后续动作，已下载的半成品 zip 保留（下次自动续传）。"""
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.finished_signal.emit(False, "已取消", self.tag)
                return
            if not self.local_zip:
                self._download_all()
            if self._cancelled:
                self.finished_signal.emit(False, "已取消", self.tag)
                return
            self.status_signal.emit("解压安装中")
            new_exe = svc.install_from_zip(
                self.local_zip or self.tasks[0]["dest"],
                self.dest_root, self.tag, self.variant)
            # cudart 包合并进同一版本目录（主包先装好，再把 runtime DLL 解进去）
            if not self.local_zip:
                for t in self.tasks[1:]:
                    svc.extract_zip_into(t["dest"], os.path.dirname(new_exe))
            self._ensure_cuda_dlls(new_exe)
            self.finished_signal.emit(True, new_exe, self.tag)
        except _Cancelled:
            self.finished_signal.emit(False, "已取消", self.tag)
        except Exception as e:
            error(f"安装 {self.tag} 失败: {e}")
            self.finished_signal.emit(False, str(e), self.tag)

    def _ensure_cuda_dlls(self, new_exe):
        """CUDA 变体兜底：新目录缺 cudart/cublas DLL 时从现有目录按大版本复制。"""
        if not self.variant.startswith("cuda-"):
            return
        major = self.variant.split("-")[1].split(".")[0]
        new_dir = os.path.dirname(new_exe)
        if svc.find_cuda_dlls([new_dir], major):
            return  # cudart 包已解压到位
        if svc.has_cudart("", major):
            return  # 系统 toolkit 提供匹配大版本的 CUDA DLL（PATH 加载，与当前运行方式一致）
        copied = svc.copy_cuda_dlls(self.dll_sources, new_dir, major)
        if copied:
            self.status_signal.emit(f"已从现有目录复制 {copied} 个 CUDA 运行库 DLL")
        else:
            self.status_signal.emit("警告：缺少 CUDA 运行库（cudart/cublas），新版本可能无法启动")

    def _download_all(self):
        total = sum(t.get("size", 0) for t in self.tasks)
        done_bytes = 0
        for i, t in enumerate(self.tasks, 1):
            if self._cancelled:
                raise _Cancelled
            resume = 0
            if os.path.exists(t["dest"]):
                resume = os.path.getsize(t["dest"])
                if t.get("size") and resume >= t["size"]:
                    done_bytes += resume  # 上次已下完：不再下载，但下方仍会做 SHA256 校验
            else:
                # 继续下载前不需要 resume，保持 0
                pass
            if not (os.path.exists(t["dest"]) and t.get("size") and resume >= t["size"]):
                done_bytes += resume
                self.status_signal.emit(f"下载中（{i}/{len(self.tasks)}）：{t['label']}")
                last_time, last_bytes, emitted_speed = time.time(), resume, 0.0

                def on_chunk(current, _total, _base=done_bytes - resume,
                             _t0=last_time, _b0=resume):
                    nonlocal last_time, last_bytes, emitted_speed
                    if self._cancelled:
                        raise _Cancelled
                    now = time.time()
                    if now - last_time >= 1.0:
                        emitted_speed = (current - last_bytes) / (now - last_time)
                        last_time, last_bytes = now, current
                    self.progress_signal.emit(_base + current, total, emitted_speed)

                download_file(t["url"], t["dest"], resume_pos=resume,
                              chunk_callback=on_chunk)
                done_bytes = (done_bytes - resume) + os.path.getsize(t["dest"])
                self.progress_signal.emit(done_bytes, total, 0.0)
            # SHA256 校验（GitHub API 提供 digest 时）——本地已有完整 zip 也校验，
            # 避免损坏残留包被直接用于解压安装
            digest = (t.get("digest") or "").split(":", 1)
            if len(digest) == 2 and digest[0] == "sha256":
                self.status_signal.emit(f"校验 SHA256：{t['label']}")
                if svc.sha256_of(t["dest"]) != digest[1]:
                    os.remove(t["dest"])
                    raise ValueError(f"SHA256 校验失败：{t['label']}（已删除，请重试）")
