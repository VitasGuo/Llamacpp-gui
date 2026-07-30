"""聊天模块数据访问层。"""
import os
import json
import urllib.request
from datetime import datetime

from .models import DEFAULT_AGENT

# 路径常量
CHAT_DIR = os.path.abspath("data/chat")
CONVERSATIONS_DIR = os.path.abspath("data/conversations")
AGENTS_DIR = os.path.abspath("data/agents")
MEMORY_DIR = os.path.join(AGENTS_DIR, "memory")
SETTINGS_FILE = os.path.join(CHAT_DIR, "settings.json")
TASK_INDEX_FILE = os.path.join(CHAT_DIR, "task_index.json")


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    _save_json(_agent_path(agent.get("name", "unnamed")), agent)


def delete_agent_file(name):
    path = _agent_path(name)
    if os.path.exists(path):
        os.remove(path)


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


def migrate_convs():
    old_file = os.path.join(CHAT_DIR, "conversations.json")
    if os.path.exists(old_file):
        data = _load_json(old_file, {"conversations": []})
        for conv in data.get("conversations", []):
            if "agents" not in conv:
                conv["agents"] = []
            write_conv_file(conv)
        os.remove(old_file)


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
        if item.get("importance") == "high":
            kept.append(item)
            continue
        created = datetime.fromisoformat(item.get("created_at", "2000-01-01T00:00:00")).timestamp()
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

def call_llm(llm_url, messages):
    url = llm_url.rstrip("/") + "/v1/chat/completions"
    body = json.dumps({"messages": messages, "stream": False, "temperature": 0.7}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
