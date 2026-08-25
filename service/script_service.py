import os
import re
import json
from datetime import datetime

from model.script import ScriptEntry
from config import SCRIPTS_DIR, SCRIPTS_CONFIG
from utils.atomic_io import atomic_write_json


class ScriptService:
    def __init__(self):
        self.scripts_dir = SCRIPTS_DIR
        self.config_file = SCRIPTS_CONFIG

    def save_script(self, entry):
        if not entry.name:
            return ""
        bat_path = entry.save_to_file(self.scripts_dir)
        self._upsert_config_entry(entry)
        return bat_path

    def delete_script(self, name):
        safe_name = ScriptEntry.sanitize_filename(name)
        bat_path = os.path.join(self.scripts_dir, f"{safe_name}.bat")
        if os.path.exists(bat_path):
            os.remove(bat_path)
            self._remove_config_entry(name)
            return True
        return False

    def load_scripts(self):
        """scripts.json 条目（.bat 仍存在的）+ 目录里的孤儿 .bat（不在 json 中）。

        过期条目（.bat 已不存在）静默丢弃、不回写；孤儿 .bat 保持
        "目录里有 .bat 就能被看到"的既有行为（手动放入/旧版本遗留）。
        """
        scripts = []
        known_bats = set()  # 已登记脚本对应的 .bat 文件名（识别孤儿用）
        entries = []
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        raw = data.get("scripts", [])
                        if isinstance(raw, list):
                            entries = raw
            except (json.JSONDecodeError, IOError):
                entries = []
        for d in entries:
            if not isinstance(d, dict):
                continue
            name = d.get("name", "")
            if not name:
                continue
            bat_path = self.get_script_path(name)
            if not os.path.exists(bat_path):
                continue  # .bat 已不存在的过期条目：静默丢弃（不回写）
            scripts.append(ScriptEntry.from_dict(d))
            known_bats.add(os.path.basename(bat_path))
        # 孤儿 .bat：目录里存在但 scripts.json 未登记
        if os.path.isdir(self.scripts_dir):
            for filename in os.listdir(self.scripts_dir):
                if not filename.endswith(".bat") or filename in known_bats:
                    continue
                bat_path = os.path.join(self.scripts_dir, filename)
                try:
                    with open(bat_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except IOError:
                    content = ""
                m = re.search(r'-m\s+"([^"]+)"', content)
                orphan = ScriptEntry(
                    name=filename[:-4],
                    content=content,
                    model_path=m.group(1) if m else "",
                )
                orphan.saved_at = ""  # 孤儿 .bat 无保存时间记录
                scripts.append(orphan)
        return scripts

    def load_script_content(self, name):
        safe_name = ScriptEntry.sanitize_filename(name)
        bat_path = os.path.join(self.scripts_dir, f"{safe_name}.bat")
        if os.path.exists(bat_path):
            with open(bat_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_script_path(self, name):
        safe_name = ScriptEntry.sanitize_filename(name)
        return os.path.join(self.scripts_dir, f"{safe_name}.bat")

    # ── scripts.json 增量维护（name/model_path/saved_at 的唯一来源；.bat 仍是内容唯一来源）──

    @staticmethod
    def _matches(saved_name, entry_name):
        """scripts.json 条目与目标脚本是否指向同一脚本：
        sanitize 后文件名一致（"我的 模型" 与 "我的_模型" 同名文件），
        或 display name 完全相同。"""
        if not saved_name or not entry_name:
            return False
        return (
            ScriptEntry.sanitize_filename(saved_name)
            == ScriptEntry.sanitize_filename(entry_name)
            or saved_name == entry_name
        )

    def _load_config_data(self) -> dict:
        """读取 scripts.json；缺失/损坏/结构非法时返回空结构（容错，同现状）。"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("scripts"), list):
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        return {"scripts": []}

    def _upsert_config_entry(self, entry):
        """增量 upsert scripts.json：命中既有条目则更新 name/model_path/saved_at/content，
        未命中则追加；原子写回（临时文件 + os.replace），崩溃不会写坏 JSON。"""
        data = self._load_config_data()
        new = {
            "name": entry.name,
            "content": entry.content,
            "saved_at": datetime.now().isoformat(),
            "model_path": entry.model_path,
        }
        for item in data["scripts"]:
            if isinstance(item, dict) and self._matches(item.get("name", ""), entry.name):
                item.update(new)
                break
        else:
            data["scripts"].append(new)
        atomic_write_json(self.config_file, data, indent=4, ensure_ascii=False)

    def _remove_config_entry(self, name):
        """从 scripts.json 移除匹配条目（匹配条件同 upsert）；无匹配时不写盘。"""
        data = self._load_config_data()
        kept = [
            item for item in data["scripts"]
            if not (isinstance(item, dict) and self._matches(item.get("name", ""), name))
        ]
        if len(kept) == len(data["scripts"]):
            return  # 无匹配条目，无需写盘
        data["scripts"] = kept
        atomic_write_json(self.config_file, data, indent=4, ensure_ascii=False)
