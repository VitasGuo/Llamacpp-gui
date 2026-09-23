# config package
# 路径常量 - 集中管理
import os

SCRIPTS_DIR = "data/scripts"
SCRIPTS_CONFIG = "data/scripts.json"
# 脚本绑定清理时被淘汰的 .bat 备份目录（不删除，便于人工找回）
REPLACED_SCRIPTS_DIR = "data/scripts_replaced"
LAST_PID_FILE = "data/last_pid.pid"
APP_CONFIG_FILE = "data/app_config.json"
DOWNLOAD_DIR = "data/downloads"
DOWNLOAD_QUEUE_FILE = "data/downloads/queue.json"
LOGS_DIR = "data/logs"
UPDATE_CACHE_FILE = "data/update_cache.json"
RUNTIME_FILE = "data/pids.json"
HISTORY_DIR = "data/history"
WATCHLIST_FILE = "data/model_watchlist.json"
WATCHLIST_IGNORED_FILE = "data/model_watchlist_ignored.json"
