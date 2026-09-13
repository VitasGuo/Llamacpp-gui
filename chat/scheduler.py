"""定时任务调度器。"""
import threading
import time
import traceback
from datetime import datetime, timedelta

from .log import error
from . import repository as repo
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
            # 安全取值：索引条目缺 task_id（脏数据）时无法调度，跳过该条目，
            # 避免下方 executing.add 抛 KeyError 中断本 tick、饿死其后的到期任务
            task_id = entry.get("task_id") or ""
            if not task_id:
                # 60s 内至多记一条日志（固定键），避免每个 tick（1s）刷量
                now = time.monotonic()
                key = "<tick-missing-task_id>"
                last = self._last_skip_log_ts.get(key)
                if last is None or now - last >= 60:
                    self._last_skip_log_ts[key] = now
                    error(f"[scheduler] 索引条目缺 task_id（entry={entry!r}），无法调度，跳过")
                continue
            if task_id in self._executing:
                continue
            next_run = entry.get("next_run_time", "")
            if next_run <= now_iso:
                self._executing.add(task_id)
                t = threading.Thread(target=self._execute_task, args=(entry,), daemon=True)
                t.start()

    def _execute_task(self, entry):
        # 安全取值：索引条目缺 key（脏数据）时不抛 KeyError 致工作线程死亡
        task_id = entry.get("task_id") or ""
        conv_id = entry.get("conversation_id") or ""
        agent_id = entry.get("agent_id", "")
        if not task_id or not conv_id:
            # 脏数据（缺必需字段）：60s 内至多记一条日志后直接跳过，
            # 避免每个 tick 无限重试；同时释放 executing 标记，防止任务被永久锁定
            self._executing.discard(task_id)
            now = time.monotonic()
            key = task_id or "<missing-task_id>"
            last = self._last_skip_log_ts.get(key)
            if last is None or now - last >= 60:
                self._last_skip_log_ts[key] = now
                error(f"[scheduler] 索引条目缺 task_id/conversation_id（task_id={task_id!r}, conv_id={conv_id!r}），跳过任务")
            return
        try:
            settings = read_settings()
            llm_url = settings.get("llm_url", "")
            if not llm_url:
                # 未配置模型地址：按失败退避推进 next_run_time，而不是原样返回。
                # 原样返回时 next_run_time 已过期 → 每个 tick(1s) 都派线程空转
                summary = "未配置模型 API 地址（llm_url 为空），任务跳过"
                now = time.monotonic()
                last = self._last_skip_log_ts.get(task_id)
                if last is None or now - last >= 60:
                    self._last_skip_log_ts[task_id] = now
                    error(f"[scheduler] No llm_url，跳过任务 {task_id}")
                self._record_task_error(conv_id, task_id, summary)
                return

            # 锁内：读 conv/task/agent 并构造请求（网络调用放锁外，避免长持锁）
            with repo.CONV_LOCK:
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

                # 应用角色配置的采样参数（temperature/top_p/…），与聊天页行为一致；
                # 未配置的字段不传，由 call_llm 内部使用服务端默认
                sampling = {}
                for key in (
                    "temperature", "top_p", "top_k", "min_p",
                    "repeat_penalty", "presence_penalty", "frequency_penalty",
                ):
                    value = agent.get(key)
                    if value is not None:
                        sampling[key] = value

            # 锁外：网络请求（最长 120s），不阻塞其他对话的读写
            response = call_llm(llm_url, messages, sampling=sampling)

            # 锁内：重新读最新 conv 再写回（避免基于调用前的旧对象覆盖并发更新）
            with repo.CONV_LOCK:
                fresh = None
                for c in list_conversations():
                    if c["id"] == conv_id:
                        fresh = c
                        break
                if not fresh:
                    return

                fresh["messages"].append({
                    "role": "assistant",
                    "content": response,
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", ""),
                })
                fresh["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

                updated_task = next(
                    (t for t in fresh.get("tasks", []) if t["id"] == task_id), None
                )
                if updated_task is None:
                    return  # 任务已被删除/停用，不再写回

                updated_task["last_run_time"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                updated_task["last_result"] = response[:200]
                # 成功：清空错误状态
                updated_task["last_error"] = ""
                updated_task["error_count"] = 0

                now = datetime.now()
                interval = updated_task.get("interval_seconds", 3600)
                # 基于上次计划时间（当前 next_run_time）推算，执行耗时不产生漂移
                updated_task["next_run_time"] = self._next_run_time(
                    updated_task.get("next_run_time"), interval, now)

                fresh["tasks"] = [t for t in fresh.get("tasks", []) if t["id"] != task_id] + [updated_task]
                write_conv_file(fresh)
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
        # 守卫：缺 id（脏数据）时直接返回，避免无意义的磁盘读取
        if not conv_id or not task_id:
            return
        try:
            with repo.CONV_LOCK:  # 读-改-写原子，避免与 handler/主流程并发覆盖
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
                # 与成功路径同一"基于计划时间"逻辑（计划时间缺失/畸形时退化为 now）
                task["next_run_time"] = self._next_run_time(task.get("next_run_time"), interval, now)

                conv["tasks"] = [t for t in conv.get("tasks", []) if t["id"] != task_id] + [task]
                write_conv_file(conv)
                rebuild_task_index()
        except Exception as e:
            error(f"[scheduler] 任务 {task_id} 失败信息回写失败: {e}")

    @staticmethod
    def _next_run_time(planned_iso, interval, now):
        """按上次计划时间推算 next_run_time，消除累积漂移。

        base = 上次计划时间（task.next_run_time）；new_next = base + interval；
        若 new_next < now（机器休眠/执行超时），取 now（立即再调度，不补跑多次）；
        计划时间缺失/畸形时退化为 base = now（不排到过去，无 1s 级重试）。
        interval 非数值或 < 60s（绕过前端校验的脏数据）时退化为 60s（UI 声明的最小间隔），
        防止 interval<=0 时 new_next 被钳到 now 造成 1s 级重试风暴。
        """
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = 60
        if interval < 60:
            interval = 60
        base = now
        if planned_iso:
            try:
                base = datetime.fromisoformat(planned_iso)
            except (TypeError, ValueError):
                base = now
        new_next = base + timedelta(seconds=interval)
        if new_next < now:
            new_next = now
        return new_next.strftime("%Y-%m-%dT%H:%M:%S")
