"""模型文件下载管理服务。"""
import os
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from model.download_entry import DownloadEntry, DownloadQueue
from service import model_sources
from service.download_client import download_file
from utils.logger import error


class PauseException(Exception):
    pass


class DownloadWorker(QThread):
    """单个文件的下载工作线程。"""
    progress_signal = pyqtSignal(str, str, int, int)
    speed_signal = pyqtSignal(str, str, float)
    finished_signal = pyqtSignal(str, str, bool, str)

    def __init__(self, source, url, dest_path, file_path, resume_pos=0):
        super().__init__()
        self.source = source
        self.url = url
        self.dest_path = dest_path
        self.file_path = file_path
        self.resume_pos = resume_pos
        self._paused = False
        self._cancelled = False

    def run(self):
        last_time = time.time()
        last_bytes = self.resume_pos

        def on_chunk(current, total):
            nonlocal last_time, last_bytes
            if self._paused:
                raise PauseException
            if self._cancelled:
                raise StopIteration
            self.progress_signal.emit(self.source, self.file_path, current, total)
            now = time.time()
            elapsed = now - last_time
            if elapsed >= 1.0:
                speed = (current - last_bytes) / elapsed
                self.speed_signal.emit(self.source, self.file_path, speed)
                last_time = now
                last_bytes = current

        try:
            current, total = download_file(
                self.url, self.dest_path,
                resume_pos=self.resume_pos,
                chunk_callback=on_chunk,
            )
            if self._cancelled:
                self._remove_partial_file()
                self.finished_signal.emit(self.source, self.file_path, False, "已取消")
            else:
                self.finished_signal.emit(self.source, self.file_path, True, "")
        except PauseException:
            self.finished_signal.emit(self.source, self.file_path, False, "已暂停")
        except StopIteration:
            # 取消中断（下个 chunk 检查到 _cancelled）：同样删除半成品
            self._remove_partial_file()
            self.finished_signal.emit(self.source, self.file_path, False, "已取消")
        except Exception as e:
            error(f"下载失败 {self.source} {self.file_path}: {e}")
            self.finished_signal.emit(self.source, self.file_path, False, str(e))

    def _remove_partial_file(self):
        """取消时删除半成品文件。句柄在 worker 线程内持有，由 worker 自己删除，
        避免 manager 侧删除被占用文件失败/句柄竞态（Windows 上文件锁定）。"""
        if os.path.exists(self.dest_path):
            try:
                os.remove(self.dest_path)
            except OSError as e:
                error(f"取消下载后删除文件失败 {self.dest_path}: {e}")

    def pause(self):
        self._paused = True

    def cancel(self):
        self._cancelled = True


class DownloadManager(QObject):
    """下载任务管理器，封装下载队列和线程生命周期。"""
    progress_signal = pyqtSignal(str, str, int, int)
    speed_signal = pyqtSignal(str, str, float)
    finished_signal = pyqtSignal(str, str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.download_queue = DownloadQueue()
        self._workers = {}
        # queue.json 写盘节流：(source, file_path) -> 上次持久化的 time.monotonic()
        self._last_persist = {}

    def start_download(self, source, model_id, file_path, file_size, dl_path):
        """启动下载。如果已在队列中则恢复，否则新建；支持从本地已有文件续传。"""
        existing = self.download_queue.find(source, model_id, file_path)
        worker_key = (source, file_path)
        active_worker = self._workers.get(worker_key)
        if active_worker and active_worker.isRunning():
            return False  # 已在下载中

        filename = os.path.basename(file_path)
        # 下载目录可能已变更，本地文件存在性基于新算出的 dest_path 判断
        dest_path = os.path.join(dl_path, filename)
        local_size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0

        if existing and existing.status in ("paused", "downloading", "pending"):
            # 恢复暂停/进行中/待下载的任务。续传基线必须以本地实际文件为准：
            # 下载目录可能在暂停期间被修改（dest_path 变了，旧目录的
            # entry.downloaded 在新目录没有对应字节），直接沿用会把 Range
            # 起点/追加模式对到错误文件上，产出缺头的损坏 GGUF
            entry = existing
            entry.status = "downloading"
            if file_size > 0 and local_size >= file_size:
                # 新目录里已有完整同名文件 → 直接标记完成
                entry.status = "completed"
                entry.downloaded = local_size
                entry.dest_path = dest_path
                self.download_queue.update(entry)
                return True
            if (0 < local_size < file_size) or (file_size == 0 and local_size > 0):
                entry.downloaded = local_size
            else:
                entry.downloaded = 0
        elif existing and existing.status == "failed":
            entry = existing
            if file_size > 0 and local_size == file_size:
                # 本地文件已完整（失败发生在下载完成之后），直接标记完成
                entry.status = "completed"
                entry.downloaded = local_size
                self.download_queue.update(entry)
                return True  # 无需启动 worker
            # 失败重试：优先从本地已有文件续传，不再无条件从 0 开始
            entry.status = "downloading"
            if (0 < local_size < file_size) or (file_size == 0 and local_size > 0):
                entry.downloaded = local_size
            else:
                entry.downloaded = 0
        elif existing and existing.status in ("completed", "cancelled"):
            entry = existing
            entry.status = "downloading"
            entry.downloaded = 0
        else:
            # 新任务：先入队，再根据本地文件状态决定续传或直接完成
            entry = DownloadEntry(
                source=source,
                model_id=model_id,
                file_path=file_path,
                file_size=file_size,
                dest_path=dest_path,
                downloaded=0,
                status="downloading",
            )
            if file_size > 0 and local_size >= file_size:
                # 本地已有完整文件（同名残留），直接标记完成，无需启动 worker
                entry.status = "completed"
                entry.downloaded = local_size
                self.download_queue.add(entry)
                return True
            if (0 < local_size < file_size) or (file_size == 0 and local_size > 0):
                # 本地已有同名半成品（例如上次中断残留），从断点续传，避免 "wb" 截断
                entry.downloaded = local_size
            self.download_queue.add(entry)

        # 以本次计算出的 dest_path 为准（下载目录可能已变更）
        entry.dest_path = dest_path

        url = model_sources.get_download_url(source, model_id, file_path)
        worker = DownloadWorker(source, url, dest_path, file_path, entry.downloaded)
        self._workers[worker_key] = worker

        worker.progress_signal.connect(self._on_progress)
        worker.speed_signal.connect(self._on_speed)
        worker.finished_signal.connect(lambda src, fp, ok, err: self._on_finished(src, fp, ok, err, entry))
        worker.start()
        self.download_queue.update(entry)
        return True

    def _on_progress(self, source, file_path, current, total):
        """转发进度信号并更新队列；queue.json 写盘节流（同一文件至少 1 秒一次）。"""
        entry = self._find_entry_by_path(source, file_path)
        if entry:
            # 内存中的 entry 每次回调照常更新
            entry.downloaded = current
            if total > 0:
                entry.file_size = total
            # 节流持久化：避免每个 1MB chunk 都全量重写 queue.json（最终状态由 _on_finished 落盘）
            now = time.monotonic()
            key = (source, file_path)
            if now - self._last_persist.get(key, 0.0) >= 1.0:
                self._last_persist[key] = now
                self.download_queue.update(entry)
        self.progress_signal.emit(source, file_path, current, total)

    def _on_speed(self, source, file_path, speed):
        self.speed_signal.emit(source, file_path, speed)

    def _on_finished(self, source, file_path, success, error, entry):
        worker = self._workers.pop((source, file_path), None)
        self._last_persist.pop((source, file_path), None)
        if success:
            entry.status = "completed"
        elif error == "已暂停":
            entry.status = "paused"
        elif error == "已取消":
            entry.status = "cancelled"
        else:
            entry.status = "failed"
        entry.downloaded = os.path.getsize(entry.dest_path) if os.path.exists(entry.dest_path) else entry.downloaded
        self.download_queue.update(entry)
        self.finished_signal.emit(source, file_path, success, error)

    def pause_download(self, entry):
        worker = self._workers.get((entry.source, entry.file_path))
        if worker:
            worker.pause()
            entry.status = "paused"
            self.download_queue.update(entry)

    def cancel_download(self, entry):
        worker = self._workers.get((entry.source, entry.file_path))
        if worker:
            worker.cancel()
        entry.status = "cancelled"
        # 有活动 worker 时不在此删文件：worker 仍持有打开句柄，继续写到下个 chunk
        # 才停止，删除被占用文件会失败（Windows 文件锁定）/产生句柄竞态，
        # 半成品由 worker 的 cancel 路径（_remove_partial_file）自己删除；
        # 仅在无活动 worker 时保留原有删除逻辑
        if not self.is_worker_active(entry.source, entry.file_path):
            if os.path.exists(entry.dest_path):
                try:
                    os.remove(entry.dest_path)
                except OSError as e:
                    error(f"取消下载后删除文件失败 {entry.dest_path}: {e}")
        entry.downloaded = 0
        self.download_queue.update(entry)

    def resume_download(self, entry, dl_path):
        return self.start_download(entry.source, entry.model_id, entry.file_path, entry.file_size, dl_path)

    def retry_download(self, entry, dl_path):
        entry.downloaded = 0
        self.download_queue.update(entry)
        return self.start_download(entry.source, entry.model_id, entry.file_path, entry.file_size, dl_path)

    def remove_download(self, entry):
        # 若仍有活动 worker，先取消，避免 UI 行已删除但后台继续下载（幽灵下载）；
        # worker 会在下个 chunk 停止并经 cancel 路径自行删除半成品
        worker_key = (entry.source, entry.file_path)
        worker = self._workers.get(worker_key)
        worker_active = worker is not None and worker.isRunning()
        if worker_active:
            worker.cancel()
        self.download_queue.remove(entry)
        if worker_active:
            # worker 仍在运行：不能弹出其最后引用（QThread 运行中被 GC 会崩溃），
            # 也不能与其并发删除文件；留给 worker 结束时自行移除/清理
            worker.finished_signal.connect(
                lambda *args, key=worker_key: self._workers.pop(key, None)
            )
        else:
            self._workers.pop(worker_key, None)
            if entry.dest_path and os.path.exists(entry.dest_path):
                try:
                    os.remove(entry.dest_path)
                except OSError as e:
                    error(f"移除下载后删除文件失败 {entry.dest_path}: {e}")

    def _find_entry_by_path(self, source, file_path):
        for e in self.download_queue.entries:
            if e.source == source and e.file_path == file_path:
                return e
        return None

    @property
    def entries(self):
        return self.download_queue.entries

    def pending_downloads(self):
        return [e for e in self.download_queue.entries if e.status in ("pending", "downloading", "paused")]

    def worker_for(self, source, file_path):
        return self._workers.get((source, file_path))

    def is_worker_active(self, source, file_path):
        w = self._workers.get((source, file_path))
        return w is not None and w.isRunning()
