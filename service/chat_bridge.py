import os
import json
import uuid
import threading
import time
import urllib.request
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import mimetypes
import re
from urllib.parse import urlparse

CHAT_DIR = os.path.abspath("data/chat")
CONVERSATIONS_DIR = os.path.abspath("data/conversations")
CONVERSATIONS_FILE = os.path.join(CHAT_DIR, "conversations.json")
AGENTS_DIR = os.path.abspath("data/agents")
AGENTS_FILE = os.path.join(CHAT_DIR, "agents.json")
PORT_FILE = os.path.join(CHAT_DIR, "bridge_port.txt")
WEBUI_DIR = os.path.abspath("data/webui")
MEMORY_DIR = os.path.join(AGENTS_DIR, "memory")
SETTINGS_FILE = os.path.join(CHAT_DIR, "settings.json")
TASK_INDEX_FILE = os.path.join(CHAT_DIR, "task_index.json")

DEFAULT_AGENT = {
    "id": "default",
    "name": "默认助手",
    "system_prompt": "你是一个有用的AI助手。",
    "temperature": 0.8,
    "top_p": None,
    "top_k": None,
    "repeat_penalty": None,
    "presence_penalty": None,
    "frequency_penalty": None,
    "min_p": None,
    "alias": "",
    "avatar": "",
    "created_at": "2024-01-01T00:00:00",
}


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


def _read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _sanitize_name(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, '')
    return name.strip() or "unnamed"


def _agent_path(name):
    return os.path.join(AGENTS_DIR, f"{_sanitize_name(name)}.json")


def _list_agents():
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


def _write_agent_file(agent):
    os.makedirs(AGENTS_DIR, exist_ok=True)
    current = _list_agents()
    for a in current:
        if a["id"] == agent["id"] and a.get("name") != agent.get("name"):
            old = _agent_path(a["name"])
            if os.path.exists(old):
                os.remove(old)
            break
    _save_json(_agent_path(agent.get("name", "unnamed")), agent)


def _delete_agent_file(name):
    path = _agent_path(name)
    if os.path.exists(path):
        os.remove(path)


def _migrate_agents():
    if os.path.exists(AGENTS_FILE):
        data = _load_json(AGENTS_FILE, {"agents": []})
        for agent in data.get("agents", []):
            _write_agent_file(agent)
        os.remove(AGENTS_FILE)
    if not _list_agents():
        _write_agent_file(DEFAULT_AGENT)


def _conv_path(title):
    return os.path.join(CONVERSATIONS_DIR, f"{_sanitize_name(title)}.json")


def _list_conversations():
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


def _write_conv_file(conv):
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    current = _list_conversations()
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


def _delete_conv_file(title):
    path = _conv_path(title)
    if os.path.exists(path):
        os.remove(path)


def _migrate_convs():
    if os.path.exists(CONVERSATIONS_FILE):
        data = _load_json(CONVERSATIONS_FILE, {"conversations": []})
        for conv in data.get("conversations", []):
            if "agents" not in conv:
                conv["agents"] = []
            _write_conv_file(conv)
        os.remove(CONVERSATIONS_FILE)


def _memory_path(agent_id):
    return os.path.join(MEMORY_DIR, f"{_sanitize_name(agent_id)}.json")


def _load_memory(agent_id):
    path = _memory_path(agent_id)
    return _load_json(path, [])


def _save_memory(agent_id, memory):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    _save_json(_memory_path(agent_id), memory)


def _clean_memory(agent_id, threshold_days):
    if threshold_days <= 0:
        return
    memory = _load_memory(agent_id)
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
        _save_memory(agent_id, kept)


def _read_settings():
    return _load_json(SETTINGS_FILE, {"llm_url": ""})


def _write_settings(data):
    _save_json(SETTINGS_FILE, data)


def _read_task_index():
    return _load_json(TASK_INDEX_FILE, [])


def _write_task_index(index):
    _save_json(TASK_INDEX_FILE, index)


def _rebuild_task_index():
    index = []
    for conv in _list_conversations():
        for task in conv.get("tasks", []):
            if task.get("enabled", False):
                index.append({
                    "task_id": task["id"],
                    "conversation_id": conv["id"],
                    "agent_id": task.get("agent_id", ""),
                    "next_run_time": task.get("next_run_time", ""),
                })
    _write_task_index(index)


def _call_llm(llm_url, messages):
    url = llm_url.rstrip("/") + "/v1/chat/completions"
    body = json.dumps({"messages": messages, "stream": False, "temperature": 0.7}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Content-Type": "application/json"}, timeout=120)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


class SchedulerService:
    def __init__(self):
        self._running = False
        self._thread = None
        self._executing = set()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                pass
            time.sleep(1)

    def _tick(self):
        index = _read_task_index()
        now_iso = _now()
        for entry in index:
            if not entry.get("enabled", False):
                continue
            if entry.get("task_id") in self._executing:
                continue
            if entry.get("next_run_time", "") <= now_iso:
                self._executing.add(entry["task_id"])
                t = threading.Thread(target=self._execute_task, args=(entry,), daemon=True)
                t.start()

    def _execute_task(self, entry):
        task_id = entry["task_id"]
        conv_id = entry["conversation_id"]
        agent_id = entry["agent_id"]
        try:
            settings = _read_settings()
            llm_url = settings.get("llm_url", "")
            if not llm_url:
                return

            conv = None
            for c in _list_conversations():
                if c["id"] == conv_id:
                    conv = c
                    break
            if not conv:
                return

            task = None
            for t in conv.get("tasks", []):
                if t["id"] == task_id:
                    task = t
                    break
            if not task or not task.get("enabled", False):
                return

            agent = None
            for a in _list_agents():
                if a["id"] == agent_id:
                    agent = a
                    break
            if not agent:
                return

            messages = []
            messages.append({"role": "system", "content": agent.get("system_prompt") or "You are a helpful assistant."})

            msgs_for_llm = conv.get("messages", [])[-50:]
            for m in msgs_for_llm:
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})

            messages.append({"role": "user", "content": task.get("instruction", "")})

            response = _call_llm(llm_url, messages)

            conv["messages"].append({
                "role": "assistant",
                "content": response,
                "agent_id": agent_id,
                "agent_name": agent.get("name", ""),
            })
            conv["updated_at"] = _now()
            _write_conv_file(conv)

            task["last_run_time"] = _now()
            task["last_result"] = response[:200]

            now = datetime.now()
            interval = task.get("interval_seconds", 3600)
            task["next_run_time"] = (now + __import__("datetime").timedelta(seconds=interval)).strftime("%Y-%m-%dT%H:%M:%S")

            conv["tasks"] = [t for t in conv.get("tasks", []) if t["id"] != task_id] + [task]
            _write_conv_file(conv)
            _rebuild_task_index()

        except Exception:
            pass
        finally:
            self._executing.discard(task_id)


_scheduler = SchedulerService()


class BridgeHandler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/conversations":
            convs = _list_conversations()
            convs.sort(key=lambda c: c.get("updated_at", c.get("created_at", "")), reverse=True)
            summary = []
            for c in convs:
                summary.append({
                    "id": c["id"],
                    "title": c.get("title", "未命名对话"),
                    "created_at": c.get("created_at", ""),
                    "updated_at": c.get("updated_at", ""),
                    "message_count": len(c.get("messages", [])),
                    "agent_count": len(c.get("agents", [])),
                })
            self._json(200, {"conversations": summary})

        elif re.match(r"^/conversations/[^/]+/tasks$", path):
            cid = path.split("/")[2]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                self._json(200, {"tasks": conv.get("tasks", [])})
            else:
                self._json(404, {"error": "conversation not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/"):
            cid = path[len("/conversations/"):]
            for c in _list_conversations():
                if c["id"] == cid:
                    self._json(200, c)
                    return
            self._json(404, {"error": "conversation not found"})

        elif path == "/settings":
            self._json(200, _read_settings())

        elif re.match(r"^/agents/[^/]+/memory$", path):
            aid = path.split("/")[2]
            _clean_memory(aid, 90)
            self._json(200, {"memory": _load_memory(aid)})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = _load_memory(aid)
            if 0 <= idx < len(memory):
                self._json(200, memory[idx])
            else:
                self._json(404, {"error": "memory not found"})

        elif path == "/agents":
            self._json(200, {"agents": _list_agents()})

        elif path.startswith("/agents/") and len(path) > len("/agents/") and "/memory" not in path:
            aid = path[len("/agents/"):]
            for a in _list_agents():
                if a["id"] == aid:
                    self._json(200, a)
                    return
            self._json(404, {"error": "agent not found"})

        else:
            self._serve_static(path)

    def _serve_static(self, path):
        if path == "" or path == "/":
            path = "/chat.html"
        safe = os.path.normpath(path).strip("/\\")
        filepath = os.path.join(WEBUI_DIR, safe)
        if not filepath.startswith(os.path.normpath(WEBUI_DIR)):
            self._json(403, {"error": "forbidden"})
            return
        if not os.path.isfile(filepath):
            self._json(404, {"error": "not found"})
            return
        mime, _ = mimetypes.guess_type(filepath)
        self.send_response(200)
        self._cors()
        if mime:
            self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            body = _read_body(self)
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return

        if path == "/conversations":
            conv = {
                "id": str(uuid.uuid4()),
                "title": body.get("title", "新对话"),
                "agents": body.get("agents", []),
                "background": body.get("background", ""),
                "created_at": _now(),
                "updated_at": _now(),
                "messages": body.get("messages", []),
            }
            _write_conv_file(conv)
            self._json(201, conv)

        elif path == "/agents":
            agent = {
                "id": str(uuid.uuid4()),
                "name": body.get("name", "新角色"),
                "system_prompt": body.get("system_prompt", ""),
                "temperature": body.get("temperature", 0.8),
                "top_p": body.get("top_p"),
                "top_k": body.get("top_k"),
                "repeat_penalty": body.get("repeat_penalty"),
                "presence_penalty": body.get("presence_penalty"),
                "frequency_penalty": body.get("frequency_penalty"),
                "min_p": body.get("min_p"),
                "memory_budget": body.get("memory_budget"),
                "memory_clean_days": body.get("memory_clean_days"),
                "alias": body.get("alias", ""),
                "avatar": body.get("avatar", ""),
                "created_at": _now(),
            }
            _write_agent_file(agent)
            self._json(201, agent)

        elif re.match(r"^/agents/[^/]+/memory$", path):
            aid = path.split("/")[2]
            memory = _load_memory(aid)
            entry = {
                "content": body.get("content", ""),
                "importance": body.get("importance", "low"),
                "created_at": _now(),
            }
            if entry["content"]:
                memory.append(entry)
                _save_memory(aid, memory)
            self._json(201, memory)

        elif re.match(r"^/conversations/[^/]+/tasks$", path):
            cid = path.split("/")[2]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if not conv:
                self._json(404, {"error": "conversation not found"})
                return
            task = {
                "id": str(uuid.uuid4()),
                "agent_id": body.get("agent_id", ""),
                "name": body.get("name", "新任务"),
                "instruction": body.get("instruction", ""),
                "enabled": body.get("enabled", True),
                "interval_seconds": body.get("interval_seconds", 3600),
                "next_run_time": _now(),
                "last_run_time": None,
                "last_result": None,
            }
            conv.setdefault("tasks", []).append(task)
            conv["updated_at"] = _now()
            _write_conv_file(conv)
            _rebuild_task_index()
            self._json(201, task)

        else:
            self._json(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            body = _read_body(self)
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return

        if re.match(r"^/conversations/[^/]+/tasks/[^/]+$", path):
            parts = path.split("/")
            cid = parts[2]
            tid = parts[4]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if not conv:
                self._json(404, {"error": "conversation not found"})
                return
            tasks = conv.get("tasks", [])
            for i, t in enumerate(tasks):
                if t["id"] == tid:
                    for key in ["name", "instruction", "enabled", "interval_seconds"]:
                        if key in body:
                            tasks[i][key] = body[key]
                    conv["tasks"] = tasks
                    conv["updated_at"] = _now()
                    _write_conv_file(conv)
                    _rebuild_task_index()
                    self._json(200, tasks[i])
                    return
            self._json(404, {"error": "task not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/") and "/tasks" not in path:
            cid = path[len("/conversations/"):]
            convs = _list_conversations()
            for i, c in enumerate(convs):
                if c["id"] == cid:
                    if "title" in body:
                        convs[i]["title"] = body["title"]
                    if "agents" in body:
                        convs[i]["agents"] = body["agents"]
                    if "messages" in body:
                        convs[i]["messages"] = body["messages"]
                    if "background" in body:
                        convs[i]["background"] = body["background"]
                    convs[i]["updated_at"] = _now()
                    _write_conv_file(convs[i])
                    self._json(200, convs[i])
                    return
            self._json(404, {"error": "conversation not found"})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = _load_memory(aid)
            if 0 <= idx < len(memory):
                if "content" in body:
                    memory[idx]["content"] = body["content"]
                if "importance" in body:
                    memory[idx]["importance"] = body["importance"]
                _save_memory(aid, memory)
                self._json(200, memory[idx])
            else:
                self._json(404, {"error": "memory not found"})

        elif path.startswith("/agents/") and len(path) > len("/agents/") and "/memory" not in path:
            aid = path[len("/agents/"):]
            agents = _list_agents()
            for i, a in enumerate(agents):
                if a["id"] == aid:
                    if "name" in body:
                        agents[i]["name"] = body["name"]
                    if "system_prompt" in body:
                        agents[i]["system_prompt"] = body["system_prompt"]
                    if "temperature" in body:
                        agents[i]["temperature"] = body["temperature"]
                    if "alias" in body:
                        agents[i]["alias"] = body["alias"]
                    if "avatar" in body:
                        agents[i]["avatar"] = body["avatar"]
                    for key in ["top_p", "top_k", "repeat_penalty", "presence_penalty", "frequency_penalty", "min_p", "memory_budget", "memory_clean_days"]:
                        if key in body:
                            agents[i][key] = body[key]
                    _write_agent_file(agents[i])
                    self._json(200, agents[i])
                    return
            self._json(404, {"error": "agent not found"})

        elif path == "/settings":
            settings = _read_settings()
            for key in body:
                settings[key] = body[key]
            _write_settings(settings)
            self._json(200, settings)

        elif re.match(r"^/conversations/[^/]+/tasks/[^/]+$", path):
            parts = path.split("/")
            cid = parts[2]
            tid = parts[4]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if not conv:
                self._json(404, {"error": "conversation not found"})
                return
            tasks = conv.get("tasks", [])
            for i, t in enumerate(tasks):
                if t["id"] == tid:
                    for key in ["name", "instruction", "enabled", "interval_seconds"]:
                        if key in body:
                            tasks[i][key] = body[key]
                    conv["tasks"] = tasks
                    conv["updated_at"] = _now()
                    _write_conv_file(conv)
                    _rebuild_task_index()
                    self._json(200, tasks[i])
                    return
            self._json(404, {"error": "task not found"})

        else:
            self._json(404, {"error": "not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if re.match(r"^/conversations/[^/]+/tasks/[^/]+$", path):
            parts = path.split("/")
            cid = parts[2]
            tid = parts[4]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                tasks = conv.get("tasks", [])
                conv["tasks"] = [t for t in tasks if t["id"] != tid]
                conv["updated_at"] = _now()
                _write_conv_file(conv)
                _rebuild_task_index()
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "conversation not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/") and "/tasks" not in path:
            cid = path[len("/conversations/"):]
            for c in _list_conversations():
                if c["id"] == cid:
                    _delete_conv_file(c.get("title", "未命名对话"))
                    self._json(200, {"deleted": True})
                    return
            self._json(404, {"error": "conversation not found"})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = _load_memory(aid)
            if 0 <= idx < len(memory):
                memory.pop(idx)
                _save_memory(aid, memory)
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "memory not found"})

        elif re.match(r"^/agents/[^/]+/memory$", path):
            aid = path.split("/")[2]
            _save_memory(aid, [])
            self._json(200, {"deleted": True})

        elif re.match(r"^/conversations/[^/]+/tasks/[^/]+$", path):
            parts = path.split("/")
            cid = parts[2]
            tid = parts[4]
            conv = None
            for c in _list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                tasks = conv.get("tasks", [])
                conv["tasks"] = [t for t in tasks if t["id"] != tid]
                conv["updated_at"] = _now()
                _write_conv_file(conv)
                _rebuild_task_index()
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "conversation not found"})

        elif path.startswith("/agents/") and len(path) > len("/agents/"):
            aid = path[len("/agents/"):]
            for a in _list_agents():
                if a["id"] == aid:
                    _delete_agent_file(a["name"])
                    self._json(200, {"deleted": True})
                    return
            self._json(404, {"error": "agent not found"})

        else:
            self._json(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass


def find_free_port(start=18765, max_attempts=10):
    import socket
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def start_bridge():
    os.makedirs(CHAT_DIR, exist_ok=True)
    _migrate_agents()
    _migrate_convs()
    _rebuild_task_index()

    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), BridgeHandler)
    with open(PORT_FILE, "w", encoding="utf-8") as f:
        f.write(str(port))

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    _scheduler.start()

    return server, port
