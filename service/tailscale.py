"""Tailscale 检测工具：定位本机 Tailscale IPv4 地址。

llama-server 通过 `--host <tailscale-ip>` 可只监听 Tailscale 虚拟网卡，
实现仅 Tailnet 内的外网访问。本模块负责可靠地拿到该 IP。
"""
import ipaddress
import os
import socket
import subprocess
import time

import psutil

from config.config import Settings

# 禁止弹出命令行窗口（项目硬性约定）
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# Tailscale 使用 CGNAT 段 100.64.0.0/10
TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# tailscale.exe 的常见安装位置（GUI 进程的 PATH 未必包含 Program Files）
_KNOWN_EXE = r"C:\Program Files\Tailscale\tailscale.exe"

# 结果 TTL 缓存：探测含 subprocess（timeout 3s），而 GUI 在每次切换
# 选中脚本时都会调用（外网地址重算）——绝不 tolerate 每次点击都跑子进程
_CACHE_TTL = 60.0
_cached_ip = ""
_cached_at = 0.0


def is_tailscale_ip(ip: str) -> bool:
    """判断一个 IPv4 地址是否落在 Tailscale CGNAT 段内。"""
    try:
        return ipaddress.ip_address(ip).version == 4 and ipaddress.ip_address(ip) in TAILSCALE_NETWORK
    except ValueError:
        return False


def _run_tailscale(exe: str):
    """执行 `tailscale ip -4`，返回首行输出（未成功/无输出返回空串）。"""
    try:
        result = subprocess.run(
            [exe, "ip", "-4"],
            capture_output=True, text=True, timeout=3,
            creationflags=NO_WINDOW,
        )
        return result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    except Exception:
        return ""


def get_tailscale_ipv4() -> str:
    """返回本机 Tailscale IPv4；未安装/未连接/异常且无手动指定时返回空串。

    优先使用用户手动指定的 Settings.tailscale_ip（可覆盖自动检测、当自动检测失败时兜底）；
    否则自动检测。自动检测结果缓存 60s。
    探测顺序：
    1. PATH 中的 `tailscale` 命令
    2. 已知安装路径 C:\\Program Files\\Tailscale\\tailscale.exe
    3. 枚举网卡，匹配 100.64.0.0/10 段的 IPv4（psutil 兜底）
    """
    override = getattr(Settings.get_instance(), "tailscale_ip", "") or ""
    if override and is_tailscale_ip(override):
        return override
    return _detect_cached()


def _detect_cached():
    global _cached_ip, _cached_at
    now = time.monotonic()
    if now - _cached_at < _CACHE_TTL:
        return _cached_ip
    _cached_at = now
    _cached_ip = _detect_tailscale_ipv4()
    return _cached_ip


def _detect_tailscale_ipv4() -> str:
    # 方式 1/2：tailscale 命令（含精确段校验，避免误收）
    candidates = ["tailscale"]
    if os.path.exists(_KNOWN_EXE):
        candidates.append(_KNOWN_EXE)
    for exe in candidates:
        ip = _run_tailscale(exe)
        if is_tailscale_ip(ip):
            return ip

    # 方式 3：网卡枚举兜底
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and is_tailscale_ip(a.address):
                    return a.address
    except Exception:
        pass
    return ""
