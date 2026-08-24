import os
import subprocess
from typing import Optional

from config import LAST_PID_FILE


class ProcessService:
    def __init__(self):
        self.pid_file = LAST_PID_FILE
        self.keywords = ["llama-server.exe", "main.exe"]
        self.current_process = None
        self.current_pid = None

    def start_script(self, bat_path):
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
            self._save_pid(process.pid)
            return {"success": True, "pid": process.pid, "process": process}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_by_pid(self):
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
            except Exception:
                pass
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
                        except Exception:
                            pass
            except Exception:
                pass
        self._clear_pid()
        self.current_pid = None
        self.current_process = None
        return killed

    def stop_all(self):
        result = {"pid_stopped": False, "name_stopped": []}
        if self.current_pid:
            result["pid_stopped"] = self.stop_by_pid()
        result["name_stopped"] = self.stop_by_name()
        return result

    def read_output(self):
        if self.current_process and self.current_process.stdout:
            line = self.current_process.stdout.readline()
            if line:
                return line.rstrip("\n\r")
        return None

    def is_process_alive(self):
        if self.current_process:
            return self.current_process.poll() is None
        return False

    def is_running(self):
        if self.current_pid:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {self.current_pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split('","')
                    if len(parts) >= 2:
                        pid = parts[1].strip('"')
                        if pid.isdigit() and int(pid) == self.current_pid:
                            return True
                return False
            except Exception:
                return False
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
            except (ValueError, IOError):
                pass
        return None
