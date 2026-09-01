"""开机自启动服务：管理 HKCU Run 注册表项（Windows 专用）。

写入当前用户注册表 HKEY_CURRENT_USER\\...\\Run 项，仅影响当前用户，
无需管理员权限。源码运行时注册 pythonw + main.py；打包（PyInstaller）
运行时注册 exe 本身。
"""
import os
import sys
import winreg

# 当前用户"启动"注册表项
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "LlamaCPP GUI"


def app_command():
    """构造开机自启动命令。

    打包运行时用 exe 本身；源码运行用 pythonw（无控制台窗口）启动 main.py。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    base = pythonw if os.path.exists(pythonw) else exe
    # service/ 的上一级即项目根目录
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(root, "main.py")
    return f'"{base}" "{main_py}"'


def _open_run_key(mode):
    """打开 Run 注册表项；不存在则创建。"""
    try:
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, mode)
    except FileNotFoundError:
        return winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY)


def is_enabled():
    """自启动是否已启用（值存在且与 app_command 一致）。"""
    try:
        with _open_run_key(winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return isinstance(value, str) and value.lower() == app_command().lower()
    except (FileNotFoundError, OSError):
        return False


def enable():
    """写入自启动项。"""
    with _open_run_key(winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_command())


def disable():
    """删除自启动项（不存在则忽略）。"""
    try:
        with _open_run_key(winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except (FileNotFoundError, OSError):
        pass


def set_enabled(enabled):
    """按布尔开关设置自启动状态。"""
    if enabled:
        enable()
    else:
        disable()