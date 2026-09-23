"""旧版本清理后台 worker。

规划（list_installed + 递归大小统计）与删除（rmtree 大目录）都是 IO 密集
操作，多版本时秒级耗时，不能在 GUI 线程执行；两阶段拆分便于中间插入
用户确认对话框。
"""
import os

from PyQt6.QtCore import QThread, pyqtSignal

from service import llamacpp_update_service as svc


class CleanupPlanWorker(QThread):
    """后台计算清理计划。planned(del_versions, del_zips, size_ver, size_zip)。"""

    planned = pyqtSignal(list, list, int, int)

    def __init__(self, root, current_exe, running_dirs, parent=None):
        super().__init__(parent)
        self._root = root
        self._current_exe = current_exe
        self._running_dirs = running_dirs

    def run(self):
        del_versions = svc.plan_version_cleanup(
            self._root, self._current_exe, keep_series=2,
            running_dirs=self._running_dirs)
        del_zips = svc.plan_zip_cleanup(self._root)
        size_ver = sum(svc.dir_size(d["dir"]) for d in del_versions)
        size_zip = sum(os.path.getsize(z) for z in del_zips if os.path.isfile(z))
        self.planned.emit(del_versions, del_zips, size_ver, size_zip)


class CleanupExecWorker(QThread):
    """后台执行删除（逐项失败不中断）。done(freed_bytes, errors)。"""

    done = pyqtSignal(int, list)

    def __init__(self, del_versions, del_zips, parent=None):
        super().__init__(parent)
        self._del_versions = del_versions
        self._del_zips = del_zips

    def run(self):
        freed = 0
        errors = []
        for d in self._del_versions:
            try:
                freed += svc.delete_version_dir(d["dir"])
            except OSError as e:
                errors.append(f"{d['tag']}: {e}")
        for z in self._del_zips:
            try:
                freed += svc.delete_zip(z)
            except OSError as e:
                errors.append(f"{os.path.basename(z)}: {e}")
        self.done.emit(freed, errors)
