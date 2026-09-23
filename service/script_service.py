import os
import re
import json
from datetime import datetime

from model.script import ScriptEntry
from config import SCRIPTS_DIR, SCRIPTS_CONFIG, REPLACED_SCRIPTS_DIR
from utils.path_utils import normalize_path
from utils.atomic_io import atomic_write_json


def name_model_score(name, model_path):
    """脚本名与模型路径的契合度（0~1）：脚本名各 token 在模型文件名中的命中比例。

    用于同模型多条绑定时判断哪个脚本名"不像别的模型"：MiniCPM5 的绑定里
    `Mini CPM5-2B` 得分 1.0，错位的 `gemma-4-E4B` 得分 0.0。
    """
    tokens = [t for t in re.split(r"[-_.\s]+", name or "") if t]
    if not tokens or not model_path:
        return 0.0
    stem = os.path.splitext(os.path.basename(model_path))[0].lower()
    if not stem:
        return 0.0
    return sum(1 for t in tokens if t.lower() in stem) / len(tokens)


def binding_rank(name, model_path, pinned, saved_at):
    """绑定条目的保留优先级：名字与模型契合度 > 置顶 > 最新保存。"""
    return (name_model_score(name, model_path), bool(pinned), saved_at or "")


class ScriptService:
    def __init__(self):
        self.scripts_dir = SCRIPTS_DIR
        self.config_file = SCRIPTS_CONFIG
        self.replaced_dir = REPLACED_SCRIPTS_DIR

    def save_script(self, entry):
        if not entry.name:
            return ""
        bat_path = entry.save_to_file(self.scripts_dir)
        self._upsert_config_entry(entry)
        return bat_path

    def get_script_for_model(self, model_path):
        """按模型路径查找已绑定的脚本（模型即脚本标识）。

        归一化路径后比较（/ 与 \\ 视为同一、大小写不敏感——Windows 文件
        系统不区分大小写，同一文件不同大小写选择应命中同一绑定）。
        多个条目绑定同一模型（历史脏数据）时返回名字与模型最契合的一条，
        避免沿用错位的旧脚本名（例：跑 MiniCPM5 却显示 gemma-4-E4B）；
        命中返回 ScriptEntry，未绑定返回 None。
        """
        if not model_path:
            return None
        norm = normalize_path(model_path).lower()
        entries = self.load_scripts()
        candidates = [
            e for e in entries
            if e.model_path and normalize_path(e.model_path).lower() == norm
        ]
        if candidates:
            return max(candidates, key=lambda e: binding_rank(
                e.name, e.model_path, e.pinned, e.saved_at))
        # 兜底：旧版无 model_path 字段的条目（.bat 里 -m 路径派生）
        derived = ScriptEntry.derive_name(model_path)
        return next((e for e in entries if e.name == derived), None)

    def reset_script(self, model_path, content):
        """重置模型脚本：把该模型已保存的脚本整版替换为 content（默认参数版）。

        用于「重置参数」：旧参数不再保留，一个模型仍只留一条绑定。脚本名复用
        现有绑定名（保持名字稳定——改名会让运行中清单 / pids.json 记录错位），
        无绑定时用 derive_name 规范名。返回脚本名。
        """
        old = self.get_script_for_model(model_path)
        name = old.name if old else ScriptEntry.derive_name(model_path)
        self.save_script(ScriptEntry(
            name=name, content=content, model_path=model_path))
        return name

    def migrate_bindings(self):
        """一次性清理脚本绑定脏数据：以 .bat 为准校正路径，同模型只留一条绑定。

        v1.19.0：修复"一个模型绑多条脚本 / 脚本名与模型错位"。
        成因（早期版本遗留）：脚本名曾可自由编辑，改绑或改名后 scripts.json
        会残留多条同 model_path 条目，且部分条目的 model_path 与实际 .bat 的
        -m 已脱节；而查询取第一条命中，导致运行中的模型显示成别的脚本名。

        规则：① model_path 与 .bat 的 -m 不一致时以 .bat 为准（.bat 是内容
        唯一来源）；② 同一模型路径的多条绑定只保留 binding_rank 最高的一条；
        ③ 被淘汰条目的 .bat 移入 data/scripts_replaced 备份（不删除，可找回）；
        ④ .bat 已不存在的过期条目顺手清掉（load_scripts 本就忽略它们）。
        幂等，可重复调用。

        返回 {"corrected", "removed", "dropped", "remap", "backup_dir"}；无改动时各项为 0/空。
        """
        data = self._load_config_data()
        kept = []
        corrected = 0
        dropped = 0
        for d in data.get("scripts", []):
            if not isinstance(d, dict) or not d.get("name"):
                continue
            name = d.get("name", "")
            if not os.path.exists(self.get_script_path(name)):
                dropped += 1
                continue  # 过期条目：.bat 已不存在
            content = self.load_script_content(name)
            m = re.search(r'-m\s+"([^"]+)"', content)
            if m:
                real = m.group(1).strip().replace("\\", "/")
                if real != (d.get("model_path") or ""):
                    d = dict(d, model_path=real)  # .bat 为事实源
                    corrected += 1
            kept.append(d)

        # 同一模型路径分组：只保留契合度最高的一条
        groups = {}
        for d in kept:
            key = normalize_path(d.get("model_path") or "").lower()
            if key:
                groups.setdefault(key, []).append(d)
        losers = []
        remap = {}
        for group in groups.values():
            if len(group) < 2:
                continue
            winner = max(group, key=lambda d: binding_rank(
                d.get("name", ""), d.get("model_path", ""),
                d.get("pinned"), d.get("saved_at", "")))
            for d in group:
                if d is winner:
                    continue
                losers.append(d)
                remap[d.get("name", "")] = winner.get("name", "")

        backup_dir = ""
        if losers:
            for d in losers:
                backup_dir = self._backup_bat(d.get("name", "")) or backup_dir
        final = [d for d in kept if id(d) not in {id(x) for x in losers}]
        if corrected or losers or dropped:
            data["scripts"] = final
            atomic_write_json(self.config_file, data, indent=4, ensure_ascii=False)
        return {
            "corrected": corrected,
            "removed": len(losers),
            "dropped": dropped,
            "remap": remap,
            "backup_dir": backup_dir,
        }

    def _backup_bat(self, name):
        """把被淘汰条目的 .bat 移入备份目录（不删除，便于人工找回）。"""
        safe = ScriptEntry.sanitize_filename(name)
        src = os.path.join(self.scripts_dir, f"{safe}.bat")
        if not os.path.exists(src):
            return ""
        os.makedirs(self.replaced_dir, exist_ok=True)
        dst = os.path.join(self.replaced_dir, f"{safe}.bat")
        n = 1
        while os.path.exists(dst):
            dst = os.path.join(self.replaced_dir, f"{safe}.{n}.bat")
            n += 1
        os.replace(src, dst)
        return self.replaced_dir

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
        """增量 upsert scripts.json：按脚本名**或模型路径**命中既有条目则更新，
        未命中则追加；原子写回（临时文件 + os.replace），崩溃不会写坏 JSON。

        加入模型路径维度后，"一个模型只留一条绑定"成为写入端不变量——旧版
        只按脚本名匹配，同一模型换名保存会不断追加重复条目，查询时取第一条
        命中就显示成别的脚本名。命中条目改名时同步删除其旧 .bat，避免孤儿。
        """
        data = self._load_config_data()
        new = {
            "name": entry.name,
            "content": entry.content,
            "saved_at": datetime.now().isoformat(),
            "model_path": entry.model_path,
            "pinned": bool(entry.pinned),
        }
        norm = normalize_path(entry.model_path or "").lower()
        for item in data["scripts"]:
            if not isinstance(item, dict):
                continue
            same_name = self._matches(item.get("name", ""), entry.name)
            same_model = bool(norm) and (
                normalize_path(item.get("model_path") or "").lower() == norm)
            if not (same_name or same_model):
                continue
            old_name = item.get("name", "")
            if old_name and old_name != entry.name:
                self._remove_bat(old_name)  # 改名：清掉旧 .bat，避免成孤儿脚本
            item.update(new)
            break
        else:
            data["scripts"].append(new)
        atomic_write_json(self.config_file, data, indent=4, ensure_ascii=False)

    def _remove_bat(self, name):
        """删除脚本对应的 .bat（不动 scripts.json）。"""
        bat_path = self.get_script_path(name)
        if os.path.exists(bat_path):
            os.remove(bat_path)
