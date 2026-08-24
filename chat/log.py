"""聊天子系统日志（纯标准库，不依赖项目其他模块）。

轮转文件日志: data/chat/logs/chat.log（单文件 2MB，最多 3 个备份）。
提供 info/warn/error，供 scheduler.py / repository.py 的异常分支使用。
路径与 repository.py 的 CHAT_DIR（data/chat）保持一致。
"""
import logging
import os
from logging.handlers import RotatingFileHandler

CHAT_LOG_DIR = os.path.join(os.path.abspath("data"), "chat", "logs")
CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "chat.log")

logger = logging.getLogger("llamacpp_chat")
logger.setLevel(logging.DEBUG)
logger.propagate = False

os.makedirs(CHAT_LOG_DIR, exist_ok=True)

_handler = RotatingFileHandler(
    CHAT_LOG_FILE,
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_handler.setLevel(logging.DEBUG)
_handler.setFormatter(
    logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(module)s:%(lineno)d] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logger.addHandler(_handler)


def info(msg):
    logger.info(msg)


def warn(msg):
    logger.warning(msg)


def error(msg):
    logger.error(msg)
