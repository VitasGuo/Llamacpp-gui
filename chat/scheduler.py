"""定时任务调度器。"""
import threading
import time
import traceback
from datetime import datetime, timedelta

from .log import error
from .repository import (
    call_llm,
    list_agents,
    list_conversations,
    read_settings,
    read_task_index,
    rebuild_task_index,
    write_conv_file,
)


# 错误摘要落盘上限（last_error / last_result 共用），完整 traceback 只进日志文件
_ERROR_SUMMARY_MAX = 200


class SchedulerService:
    def __init__(self):
        self._running = False
        self._thread = None
        self._executing = set()
        self._last_tick_error_log = None
        self._last_skip_log_ts = {}

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
                # 保留 try/except 防守护线程死亡；记日志并 60s 节流（防异常持续时刷日志）
                now = time.monotonic()
                if self._last_tick_error_log is None or now - self._last_tick_error_log >= 60:
                    self._last_tick_error_log = now
                    error(f"[scheduler] _tick 异常: {e}\n{traceback.format_exc()}")
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
                # 任务到期后每秒都会进到这里，日志 60s 内至多记一条，避免刷量
                now = time.monotonic()
                last = self._last_skip_log_ts.get(task_id)
                if last is None or now - last >= 60:
                    self._last_skip_log_ts[task_id] = now
                    error(f"[scheduler] No llm_url，跳过任务 {task_id}")
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
            # 成功：清空错误状态
            task["last_error"] = ""
            task["error_count"] = 0

            now = datetime.now()
            interval = task.get("interval_seconds", 3600)
            task["next_run_time"] = (now + timedelta(seconds=interval)).strftime("%Y-%m-%dT%H:%M:%S")

            conv["tasks"] = [t for t in conv.get("tasks", []) if t["id"] != task_id] + [task]
            write_conv_file(conv)
            rebuild_task_index()

        except Exception as e:
            # 记日志（含 traceback）并回写错误状态 + 推进 next_run_time（失败退避，
            # 避免 LLM 服务器挂掉时每个 tick 重试）
            summary = f"{type(e).__name__}: {e}"[:_ERROR_SUMMARY_MAX]
            error(f"[scheduler] 任务 {task_id} 执行失败: {summary}\n{traceback.format_exc()}")
            self._record_task_error(conv_id, task_id, summary)
        finally:
            self._executing.discard(task_id)

    def _record_task_error(self, conv_id, task_id, summary):
        """失败回写：last_error / last_result / error_count / last_run_time，并按 interval 推进 next_run_time。

        重新从磁盘读取 conv，避免把主流程已半更新（如已 append 消息未落盘）的
        内存状态一并持久化。回写自身失败时只记日志、不再抛出。
        """
        try:
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
            if not task:
                return

            now = datetime.now()
            interval = task.get("interval_seconds", 3600)
            task["last_error"] = summary
            # webui 原样展示 last_result，保持字符串类型
            task["last_result"] = "[执行失败] " + summary
            task["error_count"] = task.get("error_count", 0) + 1
            task["last_run_time"] = now.strftime("%Y-%m-%dT%H:%M:%S")
            task["next_run_time"] = (now + timedelta(seconds=interval)).strftime("%Y-%m-%dT%H:%M:%S")

            conv["tasks"] = [t for t in conv.get("tasks", []) if t["id"] != task_id] + [task]
            write_conv_file(conv)
            rebuild_task_index()
        except Exception as e:
            error(f"[scheduler] 任务 {task_id} 失败信息回写失败: {e}")
