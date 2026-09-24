"""本地模型文件管理的后台 worker。

扫描（os.walk 大模型库）与删除（GB 级文件 + 绑定清理写盘 + 进程存活探测）
都是 IO/子进程操作，不能在 GUI 线程执行（服务层规则：GUI 线程不得做阻塞 IO；
`ProcessService.is_running` 内部会跑 tasklist，约 0.6s）。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from service import model_file_service
from service.model_scanner import list_local_models
from service.process_service import ProcessService
from service.script_service import ScriptService
from utils.logger import error


class LocalModelScanWorker(QThread):
    """扫描模型目录 → scanned([{path,name,size,mtime,is_mmproj}])。"""

    scanned = pyqtSignal(list)

    def __init__(self, model_dir, parent=None):
        super().__init__(parent)
        self._model_dir = model_dir

    def run(self):
        try:
            models = list_local_models(self._model_dir)
        except Exception as e:  # 目录不可读等异常不应让视图卡在"扫描中"
            error(f"本地模型扫描异常: {e}")
            models = []
        self.scanned.emit(models)


class LocalModelDeleteWorker(QThread):
    """删除 .gguf（逐项容错）+ 清理这些模型的启动脚本绑定。

    done({"freed", "deleted", "errors", "bindings", "blocked"})：
    - `bindings`：每个主模型一条 `remove_binding_for_model` 结果；
    - `blocked`：被拦下的模型描述串（**任一主模型正在运行则整体不删**——
      运行中 llama-server 以 mmap 持有 .gguf，Windows 会拒绝删除；且删了会
      留下"服务在跑但脚本/文件已不存在"的错位状态）。
    """

    done = pyqtSignal(dict)

    def __init__(self, paths, model_paths=None, parent=None):
        super().__init__(parent)
        self._paths = list(paths or [])
        self._model_paths = [p for p in (model_paths or []) if p]

    def run(self):
        script_svc = ScriptService()
        process_svc = ProcessService()

        blocked = []
        for mp in self._model_paths:
            try:
                entry = script_svc.get_script_for_model(mp)
            except Exception as e:
                error(f"查询模型绑定失败 {mp}: {e}")
                entry = None
            if entry and process_svc.is_running(entry.name):
                blocked.append(f"{mp}（脚本 {entry.name}）")
        if blocked:
            self.done.emit({"freed": 0, "deleted": [], "errors": [], "bindings": [],
                            "blocked": "、".join(blocked)})
            return

        result = model_file_service.delete_model_files(self._paths)
        bindings = []
        for mp in self._model_paths:
            try:
                bindings.append(script_svc.remove_binding_for_model(mp))
            except Exception as e:  # 绑定清理失败不能吞掉文件删除结果
                error(f"清理脚本绑定失败 {mp}: {e}")
                result["errors"].append(f"清理脚本绑定失败（{mp}）: {e}")
        result["bindings"] = bindings
        result["blocked"] = ""
        self.done.emit(result)