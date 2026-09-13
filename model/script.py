import os
import re
from datetime import datetime


class ScriptEntry:
    def __init__(self, name="", content="", model_path="", pid=None, started_at="", port=None, pinned=False):
        self.name = name
        self.content = content
        self.saved_at = datetime.now().isoformat()
        self.model_path = model_path
        self.pinned = pinned  # 置顶标记：UI 排序置顶在前，用户可取消
        # 运行时字段（多服务器管理：由 ui 层从 process_service 的 pids.json 合并填充；
        # 持久化在 data/pids.json，不写入 scripts.json —— 后者由 _sync_config 全量重建）
        self.pid = pid
        self.started_at = started_at
        self.port = port

    @staticmethod
    def sanitize_filename(name):
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'\s+', '_', name).strip()
        return name or "unnamed"

    def save_to_file(self, scripts_dir):
        os.makedirs(scripts_dir, exist_ok=True)
        safe_name = self.sanitize_filename(self.name)
        bat_path = os.path.join(scripts_dir, f"{safe_name}.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(self.content.replace("\r\n", "\n").replace("\r", "\n"))
        return bat_path

    def to_dict(self):
        return {
            "name": self.name,
            "content": self.content,
            "saved_at": self.saved_at,
            "model_path": self.model_path,
            "pinned": self.pinned,
            "pid": self.pid,
            "started_at": self.started_at,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, d):
        entry = cls(
            name=d.get("name", ""),
            content=d.get("content", ""),
            model_path=d.get("model_path", ""),
            pinned=bool(d.get("pinned", False)),
        )
        entry.saved_at = d.get("saved_at", "")
        entry.pid = d.get("pid")
        entry.started_at = d.get("started_at", "")
        entry.port = d.get("port")
        return entry
