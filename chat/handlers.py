"""聊天桥 HTTP 请求处理器。"""
import json
import os
import re
import uuid
import mimetypes
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .log import error as _log_error
from .repository import (
    _load_json,
    _now,
    _save_json,
    delete_agent,
    delete_conversation,
    list_agents,
    list_conversations,
    load_memory,
    rebuild_task_index,
    save_memory,
    read_settings,
    write_agent_file,
    write_conv_file,
    write_settings,
    write_task_index,
)

WEBUI_DIR = os.path.abspath("data/webui")

# 由 server.start_bridge 注入：llm_url_provider 返回当前运行的模型 API 地址，
# bridge_port 为桥服务实际绑定端口。供 GET /bridge/info 返回给前端自动填充。
_llm_url_provider = None
_bridge_port = None


def set_bridge_info(llm_url_provider=None, bridge_port=None):
    global _llm_url_provider, _bridge_port
    _llm_url_provider = llm_url_provider
    _bridge_port = bridge_port


def _default_llm_url():
    """调用 GUI 注入的 provider 获取当前模型 API 地址；未注入/异常时返回空串。"""
    if _llm_url_provider is None:
        return ""
    try:
        return _llm_url_provider() or ""
    except Exception:
        return ""


def _read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


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
            convs = list_conversations()
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
            for c in list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                self._json(200, {"tasks": conv.get("tasks", [])})
            else:
                self._json(404, {"error": "conversation not found"})

        elif re.match(r"^/conversations/[^/]+/meta$", path):
            # 轮询轻量化：只返回更新时间和计数，不含消息体，
            # 前端先查 meta 是否有变化，再决定是否拉取完整对话
            cid = path.split("/")[2]
            conv = None
            for c in list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                self._json(200, {
                    "id": conv["id"],
                    "updated_at": conv.get("updated_at", ""),
                    "message_count": len(conv.get("messages", [])),
                    "agent_count": len(conv.get("agents", [])),
                    "task_count": len(conv.get("tasks", [])),
                })
            else:
                self._json(404, {"error": "conversation not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/"):
            cid = path[len("/conversations/"):]
            for c in list_conversations():
                if c["id"] == cid:
                    self._json(200, c)
                    return
            self._json(404, {"error": "conversation not found"})

        elif path == "/settings":
            self._json(200, read_settings())

        elif path == "/bridge/info":
            # 前端自动获取默认模型 API 地址：返回桥端口 + 当前运行的模型地址
            self._json(200, {
                "bridge_port": _bridge_port,
                "llm_url": _default_llm_url(),
            })

        elif re.match(r"^/agents/[^/]+/memory$", path):
            # 纯读取：清理逻辑已挪到 server.py 的 6h 周期任务，GET 不再有副作用
            aid = path.split("/")[2]
            self._json(200, {"memory": load_memory(aid)})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = load_memory(aid)
            if 0 <= idx < len(memory):
                self._json(200, memory[idx])
            else:
                self._json(404, {"error": "memory not found"})

        elif path == "/agents":
            self._json(200, {"agents": list_agents()})

        elif path.startswith("/agents/") and len(path) > len("/agents/") and "/memory" not in path:
            aid = path[len("/agents/"):]
            for a in list_agents():
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
            _log_error(f"webui 404: 请求 {path}，文件 {filepath} 不存在（CWD={os.getcwd()}）")
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
            write_conv_file(conv)
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
            write_agent_file(agent)
            self._json(201, agent)

        elif re.match(r"^/agents/[^/]+/memory$", path):
            aid = path.split("/")[2]
            memory = load_memory(aid)
            entry = {
                "content": body.get("content", ""),
                "importance": body.get("importance", "low"),
                "created_at": _now(),
            }
            if entry["content"]:
                memory.append(entry)
                save_memory(aid, memory)
            self._json(201, memory)

        elif re.match(r"^/conversations/[^/]+/tasks$", path):
            cid = path.split("/")[2]
            conv = None
            for c in list_conversations():
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
            write_conv_file(conv)
            rebuild_task_index()
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
            for c in list_conversations():
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
                    write_conv_file(conv)
                    rebuild_task_index()
                    self._json(200, tasks[i])
                    return
            self._json(404, {"error": "task not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/") and "/tasks" not in path:
            cid = path[len("/conversations/"):]
            convs = list_conversations()
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
                    write_conv_file(convs[i])
                    self._json(200, convs[i])
                    return
            self._json(404, {"error": "conversation not found"})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = load_memory(aid)
            if 0 <= idx < len(memory):
                if "content" in body:
                    memory[idx]["content"] = body["content"]
                if "importance" in body:
                    memory[idx]["importance"] = body["importance"]
                save_memory(aid, memory)
                self._json(200, memory[idx])
            else:
                self._json(404, {"error": "memory not found"})

        elif path.startswith("/agents/") and len(path) > len("/agents/") and "/memory" not in path:
            aid = path[len("/agents/"):]
            agents = list_agents()
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
                    write_agent_file(agents[i])
                    self._json(200, agents[i])
                    return
            self._json(404, {"error": "agent not found"})

        elif path == "/settings":
            settings = read_settings()
            for key in body:
                settings[key] = body[key]
            write_settings(settings)
            self._json(200, settings)

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
            for c in list_conversations():
                if c["id"] == cid:
                    conv = c
                    break
            if conv:
                tasks = conv.get("tasks", [])
                conv["tasks"] = [t for t in tasks if t["id"] != tid]
                conv["updated_at"] = _now()
                write_conv_file(conv)
                rebuild_task_index()
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "conversation not found"})

        elif path.startswith("/conversations/") and len(path) > len("/conversations/") and "/tasks" not in path:
            # 按 id 删除（repository 内按 id 定位文件，与名字唯一性解耦）
            cid = path[len("/conversations/"):]
            if delete_conversation(cid):
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "conversation not found"})

        elif re.match(r"^/agents/[^/]+/memory/\d+$", path):
            parts = path.split("/")
            aid = parts[2]
            idx = int(parts[4])
            memory = load_memory(aid)
            if 0 <= idx < len(memory):
                memory.pop(idx)
                save_memory(aid, memory)
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "memory not found"})

        elif re.match(r"^/agents/[^/]+/memory$", path):
            aid = path.split("/")[2]
            save_memory(aid, [])
            self._json(200, {"deleted": True})

        elif path.startswith("/agents/") and len(path) > len("/agents/"):
            # 按 id 删除：agent 文件 + 长期记忆文件
            aid = path[len("/agents/"):]
            if delete_agent(aid):
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "agent not found"})

        else:
            self._json(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass
