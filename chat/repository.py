"""聊天模块数据访问层。"""
import os
import json
import threading
import uuid
import urllib.request
from datetime import datetime

from .log import error
from .models import DEFAULT_AGENT
from .image_compressor import compress_image_data_url

# 路径常量
CHAT_DIR = os.path.abspath("data/chat")
CONVERSATIONS_DIR = os.path.abspath("data/conversations")
AGENTS_DIR = os.path.abspath("data/agents")
MEMORY_DIR = os.path.join(AGENTS_DIR, "memory")
SETTINGS_FILE = os.path.join(CHAT_DIR, "settings.json")
TASK_INDEX_FILE = os.path.join(CHAT_DIR, "task_index.json")

# 对话文件读写锁（RLock 可重入）：scheduler 线程与 HTTP handler 线程并发
# 读写同一 conversation 时串行化写，避免后写者覆盖先写者的更新（traps #20）
CONV_LOCK = threading.RLock()


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        error(f"读取 JSON 文件失败 {path}: {e}")
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 原子写：先写线程唯一的临时文件，完整写入后 os.replace 覆盖目标文件，
    # 避免进程中途崩溃/断电时留下半截 JSON 导致数据丢失
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        error(f"写入 JSON 文件失败 {path}: {e}")
        # 尽力删除残留临时文件
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _sanitize_name(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, '')
    return name.strip() or "unnamed"


# ─── Agent CRUD ──────────────────────────────────────────

def _agent_path(name):
    return os.path.join(AGENTS_DIR, f"{_sanitize_name(name)}.json")


def list_agents():
    if not os.path.isdir(AGENTS_DIR):
        return []
    result = []
    for fname in os.listdir(AGENTS_DIR):
        if not fname.endswith(".json"):
            continue
        agent = _load_json(os.path.join(AGENTS_DIR, fname), None)
        if agent and "id" in agent:
            result.append(agent)
    return result


def write_agent_file(agent):
    os.makedirs(AGENTS_DIR, exist_ok=True)
    current = list_agents()
    for a in current:
        if a["id"] == agent["id"] and a.get("name") != agent.get("name"):
            old = _agent_path(a["name"])
            if os.path.exists(old):
                os.remove(old)
            break

    base = agent.get("name", "unnamed")
    path = _agent_path(base)
    if os.path.exists(path):
        existing = _load_json(path, None)
        if existing and existing.get("id") != agent["id"]:
            # 同名不同 id：自动改名，避免覆盖另一个 agent 的配置（与对话改名策略一致）
            for n in range(1, 100):
                t = f"{base}({n})"
                p = _agent_path(t)
                if not os.path.exists(p):
                    agent["name"] = t
                    path = p
                    break
    _save_json(path, agent)


def delete_agent_file(name):
    path = _agent_path(name)
    if os.path.exists(path):
        os.remove(path)


def delete_agent(agent_id):
    """按 id 删除：agent 文件 + 其长期记忆文件（按 agent_id 命名），返回是否删除成功。"""
    for a in list_agents():
        if a.get("id") == agent_id:
            path = _agent_path(a.get("name", "unnamed"))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    error(f"删除 agent 文件失败 {path}: {e}")
                    return False
            # 一并清理孤儿长期记忆文件
            memory = _memory_path(agent_id)
            if os.path.exists(memory):
                try:
                    os.remove(memory)
                except OSError as e:
                    # 记忆清理失败不影响 agent 删除结果，只记日志
                    error(f"删除 agent 记忆文件失败 {memory}: {e}")
            return True
    return False


def migrate_agents():
    old_file = os.path.join(CHAT_DIR, "agents.json")
    if os.path.exists(old_file):
        data = _load_json(old_file, {"agents": []})
        for agent in data.get("agents", []):
            write_agent_file(agent)
        os.remove(old_file)
    if not list_agents():
        write_agent_file(DEFAULT_AGENT)


# ─── Conversation CRUD ───────────────────────────────────

def _conv_path(title):
    return os.path.join(CONVERSATIONS_DIR, f"{_sanitize_name(title)}.json")


def list_conversations():
    if not os.path.isdir(CONVERSATIONS_DIR):
        return []
    result = []
    for fname in os.listdir(CONVERSATIONS_DIR):
        if not fname.endswith(".json"):
            continue
        conv = _load_json(os.path.join(CONVERSATIONS_DIR, fname), None)
        if conv and "id" in conv:
            result.append(conv)
    return result


def write_conv_file(conv):
    with CONV_LOCK:  # 写串行化：scheduler/handler 并发写同一对话时避免互相覆盖
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        current = list_conversations()
        for c in current:
            if c["id"] == conv["id"] and c.get("title") != conv.get("title"):
                old = _conv_path(c["title"])
                if os.path.exists(old):
                    os.remove(old)
                break

        base = conv.get("title", "未命名对话")
        path = _conv_path(base)
        if os.path.exists(path):
            existing = _load_json(path, None)
            if existing and existing.get("id") != conv["id"]:
                for n in range(1, 100):
                    t = f"{base}({n})"
                    p = _conv_path(t)
                    if not os.path.exists(p):
                        conv["title"] = t
                        path = p
                        break
        _save_json(path, conv)


def delete_conv_file(title):
    path = _conv_path(title)
    if os.path.exists(path):
        os.remove(path)


def delete_conversation(conv_id):
    """按 id 删除：找到匹配 id 的对话文件后删除，返回是否删除成功。"""
    for c in list_conversations():
        if c.get("id") == conv_id:
            path = _conv_path(c.get("title", "未命名对话"))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    error(f"删除对话文件失败 {path}: {e}")
                    return False
            return True
    return False


def migrate_convs():
    old_file = os.path.join(CHAT_DIR, "conversations.json")
    if os.path.exists(old_file):
        data = _load_json(old_file, {"conversations": []})
        for conv in data.get("conversations", []):
            if "agents" not in conv:
                conv["agents"] = []
            write_conv_file(conv)
        os.remove(old_file)


def migrate_conversation_images():
    """压缩旧对话中的内嵌图片，压缩成功的图片打上 _optimized 标记。

    在后台线程执行（图片多时逐张 PIL 解码较慢，不能阻塞 GUI 启动）；
    逐对话包 CONV_LOCK，与 HTTP handler / scheduler 串行化，避免基于
    旧快照覆盖并发更新（锁内含压缩耗时，但迁移仅在启动后跑一次）。
    """
    for conv in list_conversations():
        cid = conv.get("id")
        with CONV_LOCK:
            fresh = None
            for c in list_conversations():
                if c["id"] == cid:
                    fresh = c
                    break
            if not fresh:
                continue  # 迁移期间对话被删除
            changed = False
            for message in fresh.get("messages", []):
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict) or part.get("_optimized"):
                        continue
                    image_url = part.get("image_url")
                    if not isinstance(image_url, dict):
                        continue
                    url = image_url.get("url")
                    if not isinstance(url, str) or not url.startswith("data:image/"):
                        continue
                    compressed = compress_image_data_url(url)
                    if compressed is not None:
                        image_url["url"] = compressed
                        part["_optimized"] = True
                        changed = True
            if changed:
                write_conv_file(fresh)


# ─── Memory CRUD ─────────────────────────────────────────

def _memory_path(agent_id):
    return os.path.join(MEMORY_DIR, f"{_sanitize_name(agent_id)}.json")


def load_memory(agent_id):
    return _load_json(_memory_path(agent_id), [])


def save_memory(agent_id, memory):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    _save_json(_memory_path(agent_id), memory)


def clean_memory(agent_id, threshold_days):
    if threshold_days <= 0:
        return
    memory = load_memory(agent_id)
    now = int(datetime.now().timestamp())
    kept = []
    for item in memory:
        if not isinstance(item, dict):
            continue  # 单条畸形数据直接丢弃，不中断整轮清理
        if item.get("importance") == "high":
            kept.append(item)
            continue
        try:
            created = datetime.fromisoformat(item.get("created_at", "2000-01-01T00:00:00")).timestamp()
        except (TypeError, ValueError):
            created = 0  # 畸形时间视为过期，交给阈值判定
        if (now - created) < threshold_days * 86400:
            kept.append(item)
    if len(kept) != len(memory):
        save_memory(agent_id, kept)


# ─── Settings ────────────────────────────────────────────

def read_settings():
    return _load_json(SETTINGS_FILE, {"llm_url": ""})


def write_settings(data):
    _save_json(SETTINGS_FILE, data)


# ─── Task Index ──────────────────────────────────────────

def read_task_index():
    return _load_json(TASK_INDEX_FILE, [])


def write_task_index(index):
    _save_json(TASK_INDEX_FILE, index)


def rebuild_task_index():
    index = []
    for conv in list_conversations():
        for task in conv.get("tasks", []):
            if task.get("enabled", False):
                index.append({
                    "task_id": task["id"],
                    "conversation_id": conv["id"],
                    "agent_id": task.get("agent_id", ""),
                    "next_run_time": task.get("next_run_time", ""),
                })
    write_task_index(index)


# ─── LLM 调用 ────────────────────────────────────────────

def call_llm(llm_url, messages, sampling=None):
    """调用 llama-server 的 OpenAI 兼容接口（非流式）。

    sampling：可选的采样参数 dict（temperature/top_p/top_k/…），
    传入的字段会覆盖默认 temperature=0.7；None/空 dict 时保持原默认。
    """
    payload = {"messages": messages, "stream": False, "temperature": 0.7}
    if sampling:
        payload.update(sampling)
    url = llm_url.rstrip("/") + "/v1/chat/completions"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    # choices 键存在但为空列表时 get 默认值不生效，需 (or [{}]) 兜底
    choices = data.get("choices") or [{}]
    return choices[0].get("message", {}).get("content", "")
