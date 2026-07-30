"""定时任务调度器。"""
import sys
import threading
import time
from datetime import datetime, timedelta

from .repository import (
    call_llm,
    list_agents,
    list_conversations,
    read_settings,
    read_task_index,
    rebuild_task_index,
    write_conv_file,
)


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
            except Exception:
                pass
            time.sleep(1)

    def _tick(self):
        index = read_task_index()
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for entry in index:
            if entry.get("task_id") in self._executing:
                continue
            next_run = entry.get("next_run_time", "")
            if next_run <= now_iso:
                self._executing.add(entry["task_id"])
                t = threading.Thread(target=self._execute_task, args=(entry,), daemon=True)
                t.start()

    def _execute_task(self, entry):
        task_id = entry["task_id"]
        conv_id = entry["conversation_id"]
        agent_id = entry["agent_id"]
        try:
            settings = read_settings()
            llm_url = settings.get("llm_url", "")
            if not llm_url:
                print(f"[scheduler] No llm_url", file=sys.stderr)
                return

            conv = None
            for c in list_conversations():
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
            if not task or not task.get("enabled", True):
                return

            agent = None
            for a in list_agents():
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

            response = call_llm(llm_url, messages)

            conv["messages"].append({
                "role": "assistant",
                "content": response,
                "agent_id": agent_id,
                "agent_name": agent.get("name", ""),
            })
            conv["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            write_conv_file(conv)

            task["last_run_time"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            task["last_result"] = response[:200]

            now = datetime.now()
            interval = task.get("interval_seconds", 3600)
            task["next_run_time"] = (now + timedelta(seconds=interval)).strftime("%Y-%m-%dT%H:%M:%S")

            conv["tasks"] = [t for t in conv.get("tasks", []) if t["id"] != task_id] + [task]
            write_conv_file(conv)
            rebuild_task_index()

        except Exception:
            pass
        finally:
            self._executing.discard(task_id)
