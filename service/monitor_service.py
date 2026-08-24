import re
import time
import threading
import urllib.request

import psutil
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from utils.logger import error

# /metrics 轮询间隔与单次请求超时（秒）
METRICS_FETCH_INTERVAL = 1.0
METRICS_TIMEOUT = 1.0

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
            (name, entry.get("port"))
            for name, entry in runtime.items()
            if isinstance(entry.get("port"), int) and 0 < entry["port"] <= 65535
        ]
        seen = set()
        for name, port in targets:
            seen.add(name)
            tps, ok = self._fetch_server_tps(name, port)
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

    def _fetch_server_tps(self, name, port):
        """抓取单个服务器的 /metrics 并计算 t/s = Δgen_tokens/Δt。

        返回 (tps, ok)：ok=False 表示 /metrics 不可用（服务未起/旧版本无端点/
        缺 token 计数器）→ UI 回退日志正则 t/s；ok=True 且 tps=None 表示
        本轮为基线/计数器回退重基线，暂无值。
        计数器回退（服务器重启）时重新基线，不算负值。
        必须绕过代理：127.0.0.1 端点显式 ProxyHandler({})。
        """
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )
            with opener.open(
                f"http://127.0.0.1:{port}/metrics", timeout=METRICS_TIMEOUT
            ) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            # 服务未起/连接拒绝等持续发生时会每秒触发，节流 60s 内至多一条
            now = time.monotonic()
            if (
                self._last_metrics_err_log_ts is None
                or now - self._last_metrics_err_log_ts >= 60
            ):
                error(f"/metrics 抓取失败 [{name}] 127.0.0.1:{port}: {e}")
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
        self.metrics_updated.emit({
            "cpu": cpu,
            "ram_percent": mem.percent,
            "ram_used": mem.used,
            "ram_total": mem.total,
            "gpus": gpus,
            "servers": servers,
        })

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
