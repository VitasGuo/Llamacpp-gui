"""聊天桥 HTTP 服务器入口。"""
import os
import socket
import threading
import time
from http.server import HTTPServer

from .handlers import BridgeHandler
from .log import error
from .repository import (
    CHAT_DIR,
    clean_memory,
    list_agents,
    migrate_agents,
    migrate_convs,
    migrate_conversation_images,
    rebuild_task_index,
)
from .scheduler import SchedulerService

PORT_FILE = os.path.join(CHAT_DIR, "bridge_port.txt")

_scheduler = SchedulerService()

# 长期记忆周期清理：每 6h 对所有 agent 清理 90 天前的低重要性记忆
MEMORY_CLEAN_INTERVAL_SECONDS = 6 * 3600
MEMORY_CLEAN_THRESHOLD_DAYS = 90


def _memory_cleaner():
    """周期记忆清理守护线程：每 6h 对所有 agent 执行 clean_memory(90)。

    原先该清理挂在 GET /agents/{id}/memory 上（读操作带副作用），现独立为
    周期任务。异常只记日志，不终止线程；守护线程随进程退出。
    """
    while True:
        time.sleep(MEMORY_CLEAN_INTERVAL_SECONDS)
        try:
            for agent in list_agents():
                clean_memory(agent["id"], MEMORY_CLEAN_THRESHOLD_DAYS)
        except Exception as e:
            error(f"[chat] 周期记忆清理失败: {e}")


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
    migrate_conversation_images()
    rebuild_task_index()

    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), BridgeHandler)
    with open(PORT_FILE, "w", encoding="utf-8") as f:
        f.write(str(port))

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    _scheduler.start()

    # 周期记忆清理（独立于 GET 请求的副作用）
    cleaner = threading.Thread(target=_memory_cleaner, daemon=True)
    cleaner.start()

    return server, port
