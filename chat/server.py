"""聊天桥 HTTP 服务器入口。"""
import os
import socket
import threading
from http.server import HTTPServer

from .handlers import BridgeHandler
from .repository import (
    CHAT_DIR,
    migrate_agents,
    migrate_convs,
    rebuild_task_index,
)
from .scheduler import SchedulerService

PORT_FILE = os.path.join(CHAT_DIR, "bridge_port.txt")

_scheduler = SchedulerService()


def find_free_port(start=18765, max_attempts=10):
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def start_bridge():
    """启动聊天桥 HTTP 服务器（守护线程）。返回 (server, port)。"""
    os.makedirs(CHAT_DIR, exist_ok=True)
    migrate_agents()
    migrate_convs()
    rebuild_task_index()

    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), BridgeHandler)
    with open(PORT_FILE, "w", encoding="utf-8") as f:
        f.write(str(port))

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    _scheduler.start()

    return server, port
