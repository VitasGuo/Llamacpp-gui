import re
import os
import json
import time
import threading
import urllib.request
from datetime import datetime, timedelta

import psutil
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from config import HISTORY_DIR
from utils.logger import error

# /metrics 轮询间隔与单次请求超时（秒）
METRICS_FETCH_INTERVAL = 1.0
METRICS_TIMEOUT = 1.0

# t/s 历史落盘（T14）：data/history/tps-YYYYMMDD.jsonl 按天分文件，
# 每 HISTORY_FLUSH_INTERVAL 秒聚合写盘一次（非每次采样都写）
HISTORY_FLUSH_INTERVAL = 5.0
HISTORY_RETENTION_DAYS = 7
HISTORY_FILE_PREFIX = "tps-"

# llama.cpp /metrics 的 token 计数器字段名（不同版本命名可能不同，按序容错）
_GEN_TOKEN_NAMES = ("llm_generation_tokens", "llm_generation_tokens_total")
_PROM_LINE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
)


def parse_prometheus(text):
    """解析 Prometheus exposition 文本，返回 {指标名: 最新值(float)}。

    跳过 HELP/TYPE 注释行；带 label 的名称取 { 前的部分；非数值
    （NaN/Inf 等）的行忽略。字段名容错由调用方负责（缺失 → .get 为 None）。
    """
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE_RE.match(line)
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                continue
    return out


class MonitorService(QObject):
    metrics_updated = pyqtSignal(dict)

    def __init__(self, process_service=None, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._collect)
        self._gpu_handles = []
        self._gpu_available = False
        self._last_gpu_err_log_ts = None
        self._init_gpu()

        # /metrics 轮询（T12 多服务器 t/s）：
        # 后台守护线程抓取，结果写入 _server_tps；现有 1s QTimer 的
        # metrics_updated 信号顺带推送（跟随既有信号/推送模式，不新增信号）
        self._process_service = process_service
        self._lock = threading.Lock()
        self._server_tps = {}  # 脚本名 -> {name, port, tps, ok, updated_at}
        self._gen_state = {}   # 脚本名 -> (last_ts, last_gen_tokens) 基线
        self._last_metrics_err_log_ts = None
        self._metrics_stop = threading.Event()
        self._metrics_thread = threading.Thread(
            target=self._metrics_loop, daemon=True, name="metrics-poll"
        )
        self._metrics_thread.start()

        # t/s 历史落盘（T14）：滚动缓冲 + 5s 聚合写盘
        self._hist_buf = {}  # server 名 -> (样本 ts, tps)，保留最近一次
        self._hist_last_write = time.monotonic()
        self._last_hist_err_log_ts = None
        self._cleanup_history()

    def _init_gpu(self):
        try:
            from pynvml import (
                nvmlInit,
                nvmlDeviceGetHandleByIndex,
                nvmlDeviceGetCount,
            )
            nvmlInit()
            count = nvmlDeviceGetCount()
            if count > 0:
                self._gpu_handles = [
                    nvmlDeviceGetHandleByIndex(i) for i in range(count)
                ]
                self._gpu_available = True
        except Exception as e:
            error(f"GPU 监控初始化失败（NVML），已禁用 GPU 显示: {e}")
            self._gpu_handles = []
            self._gpu_available = False

    def set_interval(self, ms):
        self._timer.setInterval(ms)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self._metrics_stop.set()
        self._metrics_thread.join(timeout=2)

    def is_gpu_available(self):
        return self._gpu_available

    # ── /metrics 轮询（t/s 多服务器）──

    def _metrics_loop(self):
        while not self._metrics_stop.is_set():
            try:
                self._poll_servers_once()
            except Exception as e:
                # 守护线程兜底：单次异常不应杀死轮询线程（t/s 永久失效）
                now = time.monotonic()
                if (
                    self._last_metrics_err_log_ts is None
                    or now - self._last_metrics_err_log_ts >= 60
                ):
                    error(f"/metrics 轮询异常（继续轮询）: {e}")
                    self._last_metrics_err_log_ts = now
            self._metrics_stop.wait(METRICS_FETCH_INTERVAL)

    def _poll_servers_once(self):
        """轮询一次所有运行中服务器的 /metrics（供线程与测试直接调用）。"""
        if self._process_service is None:
            return
        try:
            runtime = self._process_service.load_runtime()
        except Exception as e:
            error(f"读取多服务器运行时状态失败: {e}")
            return

        targets = [
            (name, entry.get("port"), entry.get("host"))
            for name, entry in runtime.items()
            if isinstance(entry.get("port"), int) and 0 < entry["port"] <= 65535
        ]
        seen = set()
        for name, port, host in targets:
            seen.add(name)
            tps, ok = self._fetch_server_tps(name, port, host)
            with self._lock:
                self._server_tps[name] = {
                    "name": name,
                    "port": port,
                    "tps": tps,
                    "ok": ok,
                    "updated_at": time.time(),
                }
        with self._lock:
            for name in list(self._server_tps):
                if name not in seen:
                    # 脚本已停止/删除 → 清理状态（计数器基线一并清除）
                    self._server_tps.pop(name, None)
                    self._gen_state.pop(name, None)

    def _fetch_server_tps(self, name, port, host=None):
        """抓取单个服务器的 /metrics 并计算 t/s = Δgen_tokens/Δt。

        host 用 pids.json 记录的实际 --host：绑定 Tailscale IP 的服务在
        127.0.0.1 上不可达，必须用实际地址请求（否则精确计数恒失效）。
        返回 (tps, ok)：ok=False 表示 /metrics 不可用（服务未起/旧版本无端点/
        缺 token 计数器）→ UI 回退日志正则 t/s；ok=True 且 tps=None 表示
        本轮为基线/计数器回退重基线，暂无值。
        计数器回退（服务器重启）时重新基线，不算负值。
        必须绕过代理：本地端点显式 ProxyHandler({})。
        """
        if host in (None, "", "0.0.0.0"):
            host = "127.0.0.1"
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )
            with opener.open(
                f"http://{host}:{port}/metrics", timeout=METRICS_TIMEOUT
            ) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            # 服务未起/连接拒绝等持续发生时会每秒触发，节流 60s 内至多一条
            now = time.monotonic()
            if (
                self._last_metrics_err_log_ts is None
                or now - self._last_metrics_err_log_ts >= 60
            ):
                error(f"/metrics 抓取失败 [{name}] {host}:{port}: {e}")
                self._last_metrics_err_log_ts = now
            return None, False

        counters = parse_prometheus(text)
        gen = None
        for key in _GEN_TOKEN_NAMES:
            if key in counters:
                gen = counters[key]
                break
        if gen is None:
            # 端点存在但没有 token 计数器（字段名不符/版本差异）→ 该指标不可用
            return None, False

        now = time.time()
        state = self._gen_state.get(name)
        if state is None or gen < state[1]:
            # 首次基线，或计数器回退（服务器重启）→ 重新基线，本轮不出值
            self._gen_state[name] = (now, gen)
            return None, True
        dt = now - state[0]
        if dt <= 0:
            return None, True
        tps = (gen - state[1]) / dt
        self._gen_state[name] = (now, gen)
        return tps, True

    def _collect(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        gpus = self._get_all_gpu_stats()
        with self._lock:
            servers = [dict(s) for s in self._server_tps.values()]
        # t/s 历史：/metrics 通道的有效值进滚动缓冲（5s 聚合写盘）
        for s in servers:
            if s.get("ok") and s.get("tps") is not None:
                self._hist_buf[s["name"]] = (s["updated_at"], s["tps"])
        self._maybe_flush_history()
        self.metrics_updated.emit({
            "cpu": cpu,
            "ram_percent": mem.percent,
            "ram_used": mem.used,
            "ram_total": mem.total,
            "gpus": gpus,
            "servers": servers,
        })

    # ── t/s 历史持久化（data/history/tps-YYYYMMDD.jsonl，保留 7 天）──

    def record_tps(self, name, tps):
        """记录一个 t/s 采样点（历史落盘缓冲）。

        由 UI 层在日志正则回退通道调用（/metrics 不可用时），使历史曲线
        与实时曲线数据源一致；每 5s 聚合写盘一次，不是每次采样都写。
        """
        if name and isinstance(tps, (int, float)) and tps >= 0:
            self._hist_buf[name] = (time.time(), float(tps))

    def _maybe_flush_history(self):
        now_mono = time.monotonic()
        if not self._hist_buf or now_mono - self._hist_last_write < HISTORY_FLUSH_INTERVAL:
            return
        lines = [
            json.dumps({"ts": ts, "server": name, "tps": tps}, ensure_ascii=False)
            for name, (ts, tps) in self._hist_buf.items()
        ]
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            day = datetime.now().strftime("%Y%m%d")
            path = os.path.join(HISTORY_DIR, f"{HISTORY_FILE_PREFIX}{day}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self._hist_buf.clear()  # 写盘成功后才清缓冲，失败下轮重试
            self._hist_last_write = now_mono
        except OSError as e:
            now = time.monotonic()
            if (
                self._last_hist_err_log_ts is None
                or now - self._last_hist_err_log_ts >= 60
            ):
                error(f"t/s 历史写盘失败: {e}")
                self._last_hist_err_log_ts = now

    def _cleanup_history(self):
        """启动时清理超过保留期的历史文件（按天文件，文件名即日期）。"""
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            cutoff = (
                datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)
            ).strftime("%Y%m%d")
            for fn in os.listdir(HISTORY_DIR):
                if fn.startswith(HISTORY_FILE_PREFIX) and fn.endswith(".jsonl"):
                    day = fn[len(HISTORY_FILE_PREFIX):-len(".jsonl")]
                    # YYYYMMDD 字符串比较即时间序
                    if day.isdigit() and day < cutoff:
                        os.remove(os.path.join(HISTORY_DIR, fn))
        except OSError as e:
            error(f"t/s 历史清理失败: {e}")

    def load_history(self, seconds):
        """读取最近 seconds 秒的 t/s 历史，返回 [{ts, server, tps}]（时间升序）。

        按天文件组织：从 cutoff 所在天到今天逐文件读取（1h 视图只读 1 个文件，
        7d 视图最多 8 个），跳过损坏行。

        降采样（性能 #3）：7d 视图点数巨大（约 12 万行/服务），UI 线程全量
        解析+绘点会卡顿，长范围按时间桶聚合——
        ≤1h 不降采样；1h<范围≤24h 用 60s 桶；>24h 用 300s 桶。
        桶内 tps 取样本平均值，ts 取桶起点（bucket * bucket_seconds）。
        """
        if seconds <= 3600:
            bucket_seconds = 0  # 1 小时视图不降采样（~720 点/服务，可接受）
        elif seconds <= 86400:
            bucket_seconds = 60
        else:
            bucket_seconds = 300
        now = datetime.now()
        today = now.strftime("%Y%m%d")
        start_day = (now - timedelta(seconds=seconds)).strftime("%Y%m%d")
        cutoff_ts = time.time() - seconds
        out = []
        day = start_day
        while day <= today:
            path = os.path.join(HISTORY_DIR, f"{HISTORY_FILE_PREFIX}{day}.jsonl")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                d = json.loads(line)
                            except ValueError:
                                continue
                            ts = d.get("ts")
                            tps = d.get("tps")
                            if (
                                isinstance(ts, (int, float))
                                and isinstance(tps, (int, float))
                                and ts >= cutoff_ts
                            ):
                                out.append({
                                    "ts": ts,
                                    "server": d.get("server", ""),
                                    "tps": tps,
                                })
                except OSError:
                    pass
            day = (
                datetime.strptime(day, "%Y%m%d") + timedelta(days=1)
            ).strftime("%Y%m%d")
        if bucket_seconds:
            out = self._downsample_history(out, bucket_seconds)
        out.sort(key=lambda d: d["ts"])
        return out

    @staticmethod
    def _downsample_history(points, bucket_seconds):
        """按 (server, 时间桶) 降采样：每桶保留一个点（ts=桶起点，tps=桶内均值）。

        桶规则：bucket = int(ts) // bucket_seconds；点数从"每 5s 一个采样"
        降为"每桶一个"，7d 视图约减少 30~150 倍。
        """
        buckets = {}  # (server, bucket) -> [tps 累计, 样本数]
        for p in points:
            key = (p["server"], int(p["ts"]) // bucket_seconds)
            acc = buckets.get(key)
            if acc is None:
                buckets[key] = [p["tps"], 1]
            else:
                acc[0] += p["tps"]
                acc[1] += 1
        return [
            {"ts": bucket * bucket_seconds, "server": server, "tps": total / count}
            for (server, bucket), (total, count) in buckets.items()
        ]

    def _get_all_gpu_stats(self):
        if not self._gpu_available:
            return []
        try:
            from pynvml import (
                nvmlDeviceGetUtilizationRates,
                nvmlDeviceGetMemoryInfo,
                nvmlDeviceGetName,
                nvmlDeviceGetTemperature,
                nvmlDeviceGetPowerUsage,
                NVML_TEMPERATURE_GPU,
            )
            results = []
            for handle in self._gpu_handles:
                util = nvmlDeviceGetUtilizationRates(handle)
                mem = nvmlDeviceGetMemoryInfo(handle)
                name_raw = nvmlDeviceGetName(handle)
                if isinstance(name_raw, bytes):
                    name = name_raw.decode("utf-8", errors="replace")
                else:
                    name = name_raw
                # 温度/功耗独立容错：部分卡型可能不支持其中一项（NVML 报错），
                # 单项失败不影响其余指标，缺失以 None 推送（UI 显示 N/A）
                temp_c = None
                try:
                    temp_c = nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)
                except Exception:
                    pass
                power_w = None
                try:
                    power_w = nvmlDeviceGetPowerUsage(handle) / 1_000_000  # µW → W
                except Exception:
                    pass
                results.append({
                    "name": name,
                    "util": util.gpu,
                    "mem_used": mem.used,
                    "mem_total": mem.total,
                    "temp": temp_c,
                    "power_w": power_w,
                })
            return results
        except Exception as e:
            # NVML 持续故障时会每秒触发一次，节流为 60s 内至多记一条，避免日志刷量
            now = time.monotonic()
            if self._last_gpu_err_log_ts is None or now - self._last_gpu_err_log_ts >= 60:
                error(f"GPU 指标采样失败: {e}")
                self._last_gpu_err_log_ts = now
            return []
