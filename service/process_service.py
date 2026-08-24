import os
import json
import socket
import subprocess
from datetime import datetime
from typing import Optional

from config import LAST_PID_FILE, RUNTIME_FILE
from utils.logger import error


class ProcessService:
    def __init__(self):
        self.pid_file = LAST_PID_FILE
        self.runtime_file = RUNTIME_FILE  # 多服务器运行时状态 {脚本名: {pid, started_at, port}}
        self.keywords = ["llama-server.exe", "main.exe"]
        self.current_process = None
        self.current_pid = None

    def start_script(self, bat_path, script_name="", port=None):
        bat_path = os.path.abspath(bat_path)
        if not os.path.exists(bat_path):
            return {"success": False, "error": f"脚本文件不存在: {bat_path}"}
        try:
            process = subprocess.Popen(
                ["cmd", "/c", "chcp 65001 >nul && " + bat_path],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
            self.current_process = process
            self.current_pid = process.pid
            self._save_pid(process.pid)  # 兼容旧 data/last_pid.pid（回退/旧版本）
            if script_name:
                self.save_runtime(script_name, process.pid, port)
            return {"success": True, "pid": process.pid, "process": process}
        except Exception as e:
            error(f"启动脚本失败 {bat_path}: {e}")
            return {"success": False, "error": str(e)}

    def stop_by_pid(self, script_name=None):
        if script_name:
            # 多服务器：按脚本名查 pids.json 中的 pid 并结束
            entry = self.load_runtime().get(script_name) or {}
            pid = entry.get("pid")
            if not isinstance(pid, int):
                return False
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                )
            except Exception as e:
                error(f"stop_by_pid 结束进程失败 script={script_name} PID={pid}: {e}")
                return False
            self.clear_runtime(script_name)
            if self.current_pid == pid:
                self.current_pid = None
                self.current_process = None
                self._clear_pid()
            return True
        if self.current_pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.current_pid)],
                    capture_output=True,
                )
                self._clear_pid()
                self.current_pid = None
                self.current_process = None
                return True
            except Exception as e:
                error(f"stop_by_pid 结束进程失败 PID={self.current_pid}: {e}")
        return False

    def stop_by_name(self):
        killed = []
        for keyword in self.keywords:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {keyword}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.strip().split("\n"):
                    line = line.strip().strip('"')
                    if not line:
                        continue
                    parts = line.split('","')
                    if len(parts) >= 2:
                        pid = parts[1].strip('"')
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True,
                            )
                            killed.append({"name": keyword, "pid": pid})
                        except Exception as e:
                            error(f"stop_by_name 结束进程失败 {keyword} PID={pid}: {e}")
            except Exception as e:
                error(f"stop_by_name tasklist 查询失败 {keyword}: {e}")
        self._clear_pid()
        self.current_pid = None
        self.current_process = None
        self.clear_all_runtime()  # 全部 llama 进程已被杀掉，清空所有脚本的运行时记录
        return killed

    def stop_all(self):
        result = {"pid_stopped": False, "name_stopped": []}
        if self.current_pid:
            result["pid_stopped"] = self.stop_by_pid()
        result["name_stopped"] = self.stop_by_name()
        return result

    def read_output(self, process=None):
        process = process if process is not None else self.current_process
        if process and process.stdout:
            line = process.stdout.readline()
            if line:
                return line.rstrip("\n\r")
        return None

    def is_process_alive(self, process=None):
        process = process if process is not None else self.current_process
        if process:
            return process.poll() is None
        return False

    def _pid_alive(self, pid):
        """tasklist 查询指定 PID 是否存活（Windows）。"""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            )
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split('","')
                if len(parts) >= 2:
                    pid_str = parts[1].strip('"')
                    if pid_str.isdigit() and int(pid_str) == pid:
                        return True
            return False
        except Exception as e:
            error(f"is_running tasklist 查询失败 PID={pid}: {e}")
            return False

    def is_running(self, script_name=None):
        if script_name:
            entry = self.load_runtime().get(script_name)
            if not entry:
                return False
            pid = entry.get("pid")
            if not isinstance(pid, int):
                return False
            return self._pid_alive(pid)
        if self.current_pid:
            return self._pid_alive(self.current_pid)
        return False

    def restore_last_pid(self) -> Optional[int]:
        pid = self.load_last_pid()
        if pid is None:
            return None
        self.current_pid = pid
        if self.is_running():
            return pid
        self._clear_pid()
        self.current_pid = None
        return None

    # ── 多服务器运行时状态（data/pids.json：{脚本名: {pid, started_at, port}}）──

    def load_runtime(self) -> dict:
        """读取 pids.json；不存在/损坏/条目非法时返回空（容错）。"""
        if not os.path.exists(self.runtime_file):
            return {}
        try:
            with open(self.runtime_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    k: v for k, v in data.items()
                    if isinstance(v, dict) and isinstance(v.get("pid"), int)
                }
        except (OSError, ValueError) as e:
            error(f"读取 {self.runtime_file} 失败: {e}")
        return {}

    def _write_runtime(self, runtime: dict):
        try:
            os.makedirs(os.path.dirname(self.runtime_file) or ".", exist_ok=True)
            with open(self.runtime_file, "w", encoding="utf-8") as f:
                json.dump(runtime, f, ensure_ascii=False, indent=2)
        except OSError as e:
            error(f"写入 {self.runtime_file} 失败: {e}")

    def save_runtime(self, name, pid, port=None):
        """记录脚本的运行时信息（启动时调用）。"""
        if not name:
            return
        runtime = self.load_runtime()
        runtime[name] = {
            "pid": int(pid),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "port": port,
        }
        self._write_runtime(runtime)

    def clear_runtime(self, name):
        """移除脚本的运行时记录（进程停止/退出时调用）。"""
        runtime = self.load_runtime()
        if name in runtime:
            runtime.pop(name)
            self._write_runtime(runtime)

    def clear_all_runtime(self):
        """清空所有脚本的运行时记录（"清理全部"入口后调用）。"""
        self._write_runtime({})

    def restore_all_pids(self) -> dict:
        """启动恢复（多脚本）：返回 {脚本名: pid}（仍存活的）；死掉的条目清除。

        与旧版 restore_last_pid（data/last_pid.pid）的关系：pids.json 有条目时
        以它为准；仅当 pids.json 为空时才回退旧文件（升级兼容，旧文件持续
        记录"最近一次启动的 PID"，行为不变）。
        """
        alive = {}
        for name, entry in self.load_runtime().items():
            pid = entry.get("pid")
            if isinstance(pid, int) and pid > 0 and self._pid_alive(pid):
                alive[name] = pid
            else:
                self.clear_runtime(name)
        return alive

    def is_port_in_use(self, port) -> bool:
        """socket bind 探测端口是否被占用（启动前预检）。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", int(port)))
                return False
        except OSError:
            return True

    def _save_pid(self, pid):
        with open(self.pid_file, "w") as f:
            f.write(str(pid))

    def _clear_pid(self):
        if os.path.exists(self.pid_file):
            os.remove(self.pid_file)

    def load_last_pid(self):
        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    return int(f.read().strip())
            except (ValueError, IOError) as e:
                error(f"读取 {self.pid_file} 失败: {e}")
        return None
