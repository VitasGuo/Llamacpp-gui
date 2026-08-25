import os
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from config import DOWNLOAD_QUEUE_FILE
from utils.atomic_io import atomic_write_json


@dataclass
class DownloadEntry:
    model_id: str
    file_path: str
    source: str = "modelscope"
    file_size: int = 0
    dest_path: str = ""
    downloaded: int = 0
    status: str = "pending"

    @property
    def progress(self):
        if self.file_size <= 0:
            return 0
        return int(self.downloaded * 100 / self.file_size)

    @property
    def filename(self):
        return os.path.basename(self.file_path)

    def to_dict(self):
        return {
            "source": self.source,
            "model_id": self.model_id,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "dest_path": self.dest_path,
            "downloaded": self.downloaded,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            source=d.get("source", "modelscope"),
            model_id=d.get("model_id", ""),
            file_path=d.get("file_path", ""),
            file_size=d.get("file_size", 0),
            dest_path=d.get("dest_path", ""),
            downloaded=d.get("downloaded", 0),
            status=d.get("status", "pending"),
        )


class DownloadQueue:
    def __init__(self):
        self.entries: list[DownloadEntry] = []
        self._load()

    def add(self, entry: DownloadEntry):
        self.entries.append(entry)
        self._save()

    def remove(self, entry: DownloadEntry):
        self.entries.remove(entry)
        self._save()

    def update(self, entry: DownloadEntry):
        self._save()

    def find(self, source: str, model_id: str, file_path: str):
        for e in self.entries:
            if e.source == source and e.model_id == model_id and e.file_path == file_path:
                return e
        return None

    def pending_downloads(self):
        return [e for e in self.entries if e.status in ("pending", "downloading", "paused")]

    def _save(self):
        os.makedirs(os.path.dirname(DOWNLOAD_QUEUE_FILE), exist_ok=True)
        data = {
            "updated_at": datetime.now().isoformat(),
            "queue": [e.to_dict() for e in self.entries],
        }
        # 原子写（临时文件 + fsync + os.replace），进程中途崩溃不会留下写坏一半的 queue.json
        atomic_write_json(DOWNLOAD_QUEUE_FILE, data, indent=2, ensure_ascii=False)

    def _load(self):
        if os.path.exists(DOWNLOAD_QUEUE_FILE):
            try:
                with open(DOWNLOAD_QUEUE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for d in data.get("queue", []):
                        self.entries.append(DownloadEntry.from_dict(d))
            except (json.JSONDecodeError, IOError):
                pass
