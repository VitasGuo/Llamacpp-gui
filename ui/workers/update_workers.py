"""llama.cpp 版本检查和软件更新检查工作线程。"""
import json
import urllib.request
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal


class CheckUpdateWorker(QThread):
    result_signal = pyqtSignal(str, str)

    def run(self):
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
                headers={"User-Agent": "llamacpp-gui/1.0", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                published = data.get("published_at", "")
                if published:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    self.result_signal.emit(dt.strftime("%Y-%m-%d"), "")
                    return
            self.result_signal.emit("", "未能获取到版本信息")
        except Exception as e:
            self.result_signal.emit("", str(e))


class CheckAppUpdateWorker(QThread):
    result_signal = pyqtSignal(str, str)

    def run(self):
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/kkblank/Llamacpp-gui/releases/latest",
                headers={"User-Agent": "llamacpp-gui/1.0", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                published = data.get("published_at", "")
                if published:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    self.result_signal.emit(dt.strftime("%Y-%m-%d"), "")
                    return
            self.result_signal.emit("", "未能获取到版本信息")
        except Exception as e:
            self.result_signal.emit("", str(e))
