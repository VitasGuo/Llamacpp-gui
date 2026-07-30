"""模型文件下载管理服务。"""
import os
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from model.download_entry import DownloadEntry, DownloadQueue
from service.modelscope import download_file, get_download_url


class PauseException(Exception):
    pass


class DownloadWorker(QThread):
    """单个文件的下载工作线程。"""
    progress_signal = pyqtSignal(str, int, int)
    speed_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(str, bool, str)

    def __init__(self, url, dest_path, file_path, resume_pos=0):
        super().__init__()
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
            self.progress_signal.emit(self.file_path, current, total)
            now = time.time()
            elapsed = now - last_time
            if elapsed >= 1.0:
                speed = (current - last_bytes) / elapsed
                self.speed_signal.emit(self.file_path, speed)
                last_time = now
                last_bytes = current

        try:
            current, total = download_file(
                self.url, self.dest_path,
                resume_pos=self.resume_pos,
                chunk_callback=on_chunk,
            )
            if self._cancelled:
                if os.path.exists(self.dest_path):
                    os.remove(self.dest_path)
                self.finished_signal.emit(self.file_path, False, "已取消")
            else:
                self.finished_signal.emit(self.file_path, True, "")
        except PauseException:
            self.finished_signal.emit(self.file_path, False, "已暂停")
        except StopIteration:
            self.finished_signal.emit(self.file_path, False, "已取消")
        except Exception as e:
            self.finished_signal.emit(self.file_path, False, str(e))

    def pause(self):
        self._paused = True

    def cancel(self):
        self._cancelled = True


class DownloadManager(QObject):
    """下载任务管理器，封装下载队列和线程生命周期。"""
    progress_signal = pyqtSignal(str, int, int)
    speed_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.download_queue = DownloadQueue()
        self._workers = {}

    def start_download(self, model_id, file_path, file_size, dl_path):
        """启动下载。如果已在队列中则恢复，否则新建。"""
        existing = self.download_queue.find(model_id, file_path)
        active_worker = self._workers.get(file_path)
        if active_worker and active_worker.isRunning():
            return False  # 已在下载中

        filename = os.path.basename(file_path)
        dest_path = os.path.join(dl_path, filename)

        if existing and existing.status in ("paused", "downloading", "pending"):
            entry = existing
            entry.status = "downloading"
            resume_pos = entry.downloaded
        elif existing and existing.status in ("completed", "failed", "cancelled"):
            entry = existing
            entry.status = "downloading"
            entry.downloaded = 0
            resume_pos = 0
        else:
            entry = DownloadEntry(
                model_id=model_id,
                file_path=file_path,
                file_size=file_size,
                dest_path=dest_path,
                downloaded=0,
                status="downloading",
            )
            self.download_queue.add(entry)

        url = get_download_url(model_id, file_path)
        resume_pos = entry.downloaded
        worker = DownloadWorker(url, dest_path, file_path, resume_pos)
        self._workers[file_path] = worker

        worker.progress_signal.connect(self._on_progress)
        worker.speed_signal.connect(self._on_speed)
        worker.finished_signal.connect(lambda fp, ok, err: self._on_finished(fp, ok, err, entry))
        worker.start()
        self.download_queue.update(entry)
        return True

    def _on_progress(self, file_path, current, total):
        """转发进度信号并更新队列。"""
        entry = self._find_entry_by_path(file_path)
        if entry:
            entry.downloaded = current
            if total > 0:
                entry.file_size = total
            self.download_queue.update(entry)
        self.progress_signal.emit(file_path, current, total)

    def _on_speed(self, file_path, speed):
        self.speed_signal.emit(file_path, speed)

    def _on_finished(self, file_path, success, error, entry):
        worker = self._workers.pop(file_path, None)
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
        self.finished_signal.emit(file_path, success, error)

    def pause_download(self, entry):
        worker = self._workers.get(entry.file_path)
        if worker:
            worker.pause()
            entry.status = "paused"
            self.download_queue.update(entry)

    def cancel_download(self, entry):
        worker = self._workers.get(entry.file_path)
        if worker:
            worker.cancel()
        entry.status = "cancelled"
        if os.path.exists(entry.dest_path):
            try:
                os.remove(entry.dest_path)
            except OSError:
                pass
        entry.downloaded = 0
        self.download_queue.update(entry)

    def resume_download(self, entry, dl_path):
        return self.start_download(entry.model_id, entry.file_path, entry.file_size, dl_path)

    def retry_download(self, entry, dl_path):
        entry.downloaded = 0
        self.download_queue.update(entry)
        return self.start_download(entry.model_id, entry.file_path, entry.file_size, dl_path)

    def remove_download(self, entry):
        self.download_queue.remove(entry)
        self._workers.pop(entry.file_path, None)
        if entry.dest_path and os.path.exists(entry.dest_path):
            try:
                os.remove(entry.dest_path)
            except OSError:
                pass

    def _find_entry_by_path(self, file_path):
        for e in self.download_queue.entries:
            if e.file_path == file_path:
                return e
        return None

    @property
    def entries(self):
        return self.download_queue.entries

    def pending_downloads(self):
        return [e for e in self.download_queue.entries if e.status in ("pending", "downloading", "paused")]

    def worker_for(self, file_path):
        return self._workers.get(file_path)

    def is_worker_active(self, file_path):
        w = self._workers.get(file_path)
        return w is not None and w.isRunning()
