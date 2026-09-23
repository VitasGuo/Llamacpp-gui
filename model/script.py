import os
import re
import hashlib
from datetime import datetime

from utils.path_utils import normalize_path


class ScriptEntry:
    def __init__(self, name="", content="", model_path="", pid=None, started_at="", port=None, pinned=False):
        # 脚本绑定模型：未显式给名时，自动由 model_path 推导（用户不可见/不可编辑）
        # —— 移除独立脚本名后，模型即脚本的唯一标识，name 只是内部稳定 key。
        if not name and model_path:
            name = self.derive_name(model_path)
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

    @staticmethod
    def derive_name(model_path):
        """由模型路径推导脚本名（脚本绑定模型的内部稳定 key）。

        格式：`<模型文件名去扩展名>_<归一化路径短hash8>`。
        短 hash 保证不同目录下的同名 .gguf 不产生 .bat 冲突，且对同一路径稳定可复现。
        """
        base = os.path.splitext(os.path.basename(model_path or ""))[0] or "model"
        base = ScriptEntry.sanitize_filename(base)
        digest = hashlib.sha1(normalize_path(model_path).encode("utf-8")).hexdigest()[:8]
        return f"{base}_{digest}"

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
