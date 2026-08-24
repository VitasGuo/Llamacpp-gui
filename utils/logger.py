import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR

logger = logging.getLogger("llamacpp_gui")
logger.setLevel(logging.DEBUG)

_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.DEBUG)
_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S")
_handler.setFormatter(_formatter)
logger.addHandler(_handler)

# 文件日志: data/logs/app.log，单文件 2MB，最多 3 个备份（轮转 app.log.1 ~ app.log.3）
os.makedirs(LOGS_DIR, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, "app.log"),
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(module)s:%(lineno)d] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logger.addHandler(_file_handler)


def debug(msg):
    logger.debug(msg)


def info(msg):
    logger.info(msg)


def warn(msg):
    logger.warning(msg)


def error(msg):
    logger.error(msg)
