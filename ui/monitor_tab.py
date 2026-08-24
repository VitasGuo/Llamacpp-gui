import time
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QProgressBar, QComboBox, QFrame, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QPainter
from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis


def _bar_style(pct):
    if pct >= 80:
        return "QProgressBar::chunk { background: #e74c3c; border-radius: 3px; }" \
               "QProgressBar { border: 1px solid #bbb; border-radius: 4px; text-align: center; min-height: 18px; }"
    if pct >= 50:
        return "QProgressBar::chunk { background: #f39c12; border-radius: 3px; }" \
               "QProgressBar { border: 1px solid #bbb; border-radius: 4px; text-align: center; min-height: 18px; }"
    return "QProgressBar::chunk { background: #27ae60; border-radius: 3px; }" \
           "QProgressBar { border: 1px solid #bbb; border-radius: 4px; text-align: center; min-height: 18px; }"


def _fmt_bytes(n):
    if n >= 10 ** 12:
        return f"{n / 10 ** 12:.1f}TB"
    if n >= 10 ** 9:
        return f"{n / 10 ** 9:.1f}GB"
    if n >= 10 ** 6:
        return f"{n / 10 ** 6:.1f}MB"
    return f"{n / 10 ** 3:.1f}KB"


class GpuCard(QFrame):
    def __init__(self, index, name, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        header = QLabel(f"GPU {index} ({name})")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        self.util_bar = QProgressBar()
        self.util_bar.setRange(0, 100)
        self.util_bar.setFixedHeight(18)
        self.util_label = QLabel("0%")
        self.util_label.setFixedWidth(60)
        util_row = QHBoxLayout()
        util_row.addWidget(QLabel("  利用率"))
        util_row.addWidget(self.util_bar, 1)
        util_row.addWidget(self.util_label)
        util_row.setSpacing(8)
        layout.addLayout(util_row)

        self.mem_bar = QProgressBar()
        self.mem_bar.setRange(0, 100)
        self.mem_bar.setFixedHeight(18)
        self.mem_label = QLabel("0 / 0 GB")
        self.mem_label.setFixedWidth(150)
        mem_row = QHBoxLayout()
        mem_row.addWidget(QLabel("  显存  "))
        mem_row.addWidget(self.mem_bar, 1)
        mem_row.addWidget(self.mem_label)
        mem_row.setSpacing(8)
        layout.addLayout(mem_row)

        # 温度：0–100°C 进度条（沿用 _bar_style：≥80°C 红色预警）
        self.temp_bar = QProgressBar()
        self.temp_bar.setRange(0, 100)
        self.temp_bar.setFixedHeight(18)
        self.temp_label = QLabel("--°C")
        self.temp_label.setFixedWidth(60)
        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("  温度  "))
        temp_row.addWidget(self.temp_bar, 1)
        temp_row.addWidget(self.temp_label)
        temp_row.setSpacing(8)
        layout.addLayout(temp_row)

        # 功耗：无固定量程，用文本展示（µW → W 在 service 层换算）
        self.power_label = QLabel("功耗: -- W")
        self.power_label.setStyleSheet("color: #555;")
        layout.addWidget(self.power_label)

    def update(self, util_pct, mem_used, mem_total, temp=None, power_w=None):
        self.util_bar.setValue(int(util_pct))
        self.util_bar.setStyleSheet(_bar_style(util_pct))
        self.util_label.setText(f"{util_pct:.0f}%")

        pct = (mem_used / mem_total * 100) if mem_total > 0 else 0
        self.mem_bar.setValue(int(pct))
        self.mem_bar.setStyleSheet(_bar_style(pct))
        self.mem_label.setText(f"{_fmt_bytes(mem_used)} / {_fmt_bytes(mem_total)}")

        if temp is None:
            self.temp_bar.setValue(0)
            self.temp_label.setText("N/A")
        else:
            self.temp_bar.setValue(min(max(int(temp), 0), 100))
            self.temp_bar.setStyleSheet(_bar_style(temp))
            self.temp_label.setText(f"{temp:.0f}°C")

        if power_w is None:
            self.power_label.setText("功耗: N/A")
        else:
            self.power_label.setText(f"功耗: {power_w:.1f} W")


class TpsChart(QWidget):
    """多系列 t/s 折线图：每个服务一条线（图例=脚本名/模型名），60 秒滑动窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = {}  # name -> QLineSeries
        self._data = {}    # name -> deque((ts, tps))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._chart = QChart()
        self._chart.legend().setVisible(True)
        self._chart.setTitle("推理速度 (最近 60 秒)")

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0, 60)
        self._axis_x.setLabelFormat("%d")
        self._axis_x.setTitleText("秒")
        self._axis_x.setTickCount(7)
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)

        self._axis_y = QValueAxis()
        self._axis_y.setRange(0, 100)
        self._axis_y.setLabelFormat("%.0f")
        self._axis_y.setTitleText("t/s")
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)

        self._view = QChartView(self._chart)
        layout.addWidget(self._view)

    def _ensure_series(self, name):
        s = self._series.get(name)
        if s is None:
            s = QLineSeries()
            s.setName(name)
            self._series[name] = s
            self._chart.addSeries(s)
            s.attachAxis(self._axis_x)
            s.attachAxis(self._axis_y)
        return s

    def add_tps(self, name, tps):
        data = self._data.get(name)
        if data is None:
            data = self._data[name] = deque(maxlen=300)
        data.append((time.time(), tps))
        self._ensure_series(name)
        self._refresh()

    def _refresh(self):
        now = time.time()
        cutoff = now - 60
        max_tps = 0
        for name, data in list(self._data.items()):
            while data and data[0][0] < cutoff:
                data.popleft()
            if not data:
                # 该服务已停（窗口内无新点）→ 移除系列，图例同步消失
                s = self._series.pop(name)
                del self._data[name]
                self._chart.removeSeries(s)
                s.deleteLater()
                continue
            max_tps = max(max_tps, max(v for _, v in data))
            s = self._series[name]
            win_start = cutoff if len(data) > 1 else data[0][0]
            s.clear()
            for ts, tps in data:
                s.append(ts - win_start, tps)
        self._axis_y.setRange(0, max_tps * 1.2 if max_tps > 0 else 100)


class HistoryChart(QWidget):
    """t/s 历史折线图（只读）：按所选时间范围渲染 data/history/ 的 JSONL 数据，
    每服务一条线（图例=脚本名），用于调参前后对比。与实时 TpsChart 相互独立。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = {}  # name -> QLineSeries
        self._points = []  # [{ts, server, tps}]
        self._window = 3600.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._chart = QChart()
        self._chart.legend().setVisible(True)
        self._chart.setTitle("t/s 历史")

        self._axis_x = QValueAxis()
        self._axis_x.setLabelFormat("%.0f")
        self._axis_x.setTickCount(7)
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)

        self._axis_y = QValueAxis()
        self._axis_y.setRange(0, 100)
        self._axis_y.setLabelFormat("%.0f")
        self._axis_y.setTitleText("t/s")
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)

        self._view = QChartView(self._chart)
        layout.addWidget(self._view)
        self._apply_window()

    def _ensure_series(self, name):
        s = self._series.get(name)
        if s is None:
            s = QLineSeries()
            s.setName(name)
            self._series[name] = s
            self._chart.addSeries(s)
            s.attachAxis(self._axis_x)
            s.attachAxis(self._axis_y)
        return s

    def set_window(self, seconds):
        self._window = float(seconds)
        self._apply_window()
        self._refresh()

    def _apply_window(self):
        # 1 小时用秒，更长的范围用小时（避免 X 轴出现 604800 这类大数）
        if self._window <= 3600:
            self._axis_x.setTitleText("秒")
        else:
            self._axis_x.setTitleText("小时")

    def load_points(self, points):
        self._points = points or []
        self._refresh()

    def _x(self, ts, start):
        return ts - start if self._window <= 3600 else (ts - start) / 3600.0

    def _refresh(self):
        now = time.time()
        start = now - self._window
        per_name = {}
        for p in self._points:
            if p["ts"] >= start:
                per_name.setdefault(p["server"], []).append((p["ts"], p["tps"]))
        # 窗口内无数据的系列移除（图例同步消失）
        for name in list(self._series):
            if name not in per_name:
                s = self._series.pop(name)
                self._chart.removeSeries(s)
                s.deleteLater()
        max_tps = 0
        for name, pts in per_name.items():
            s = self._ensure_series(name)
            s.clear()
            for ts, tps in pts:
                s.append(self._x(ts, start), tps)
            max_tps = max(max_tps, max(v for _, v in pts))
        self._axis_x.setRange(0, self._x(now, start))
        self._axis_y.setRange(0, max_tps * 1.2 if max_tps > 0 else 100)


class MonitorTab(QWidget):
    FREQ_OPTIONS = [
        ("0.5 秒", 500),
        ("1 秒 (默认)", 1000),
        ("2 秒", 2000),
    ]

    # /metrics 不可用时回退日志正则 t/s 的"新鲜度"窗口（秒）：
    # 日志 t/s 只在服务器打印速度行时更新，超过窗口的值视为陈旧不参与绘制
    LOG_TPS_FALLBACK_WINDOW = 5.0

    def __init__(self, monitor_service, parent=None):
        super().__init__(parent)
        self._service = monitor_service
        self._server_running = False
        self._server_start_time = 0.0
        self._gpu_cards = []
        self._focus_name = ""     # 聚焦（选中）脚本：t/s 标签优先显示它
        self._log_tps = {}        # name -> (ts, tps)：日志正则通道（回退数据源）
        self._metrics_ok = {}     # name -> bool：/metrics 最近一次是否可用
        self._log_drawn_ts = {}   # name -> 已绘制的日志正则 t/s 点的 ts（防重复绘点）

        self._setup_ui()
        self._service.metrics_updated.connect(self._on_metrics)

        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.setInterval(1000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(self._build_top_bar())
        layout.addWidget(self._build_system_group())

        self._gpu_group = QGroupBox("GPU")
        self._gpu_inner = QVBoxLayout(self._gpu_group)
        self._gpu_inner.setContentsMargins(8, 8, 8, 8)
        self._gpu_inner.setSpacing(4)
        self._gpu_placeholder = QLabel("未检测到 NVIDIA GPU")
        self._gpu_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gpu_placeholder.setStyleSheet("color: #888; padding: 16px;")
        if not self._service.is_gpu_available():
            self._gpu_inner.addWidget(self._gpu_placeholder)
        layout.addWidget(self._gpu_group)

        bottom = QHBoxLayout()
        bottom.addWidget(self._build_status_group())
        self._tps_chart = TpsChart()
        bottom.addWidget(self._tps_chart, 2)
        layout.addLayout(bottom)

        layout.addWidget(self._build_history_group())
        self._refresh_history()

    # t/s 历史视图时间范围（秒）
    HIST_RANGE_OPTIONS = [
        ("1 小时", 3600),
        ("24 小时", 86400),
        ("7 天", 7 * 86400),
    ]

    def _build_history_group(self):
        group = QGroupBox("t/s 历史（最近 7 天，用于调参前后对比）")
        v = QVBoxLayout(group)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("时间范围:"))
        self._hist_range = QComboBox()
        for label, val in self.HIST_RANGE_OPTIONS:
            self._hist_range.addItem(label, val)
        self._hist_range.currentIndexChanged.connect(self._on_hist_range_changed)
        bar.addWidget(self._hist_range)
        self._hist_refresh_btn = QPushButton("刷新")
        self._hist_refresh_btn.clicked.connect(self._refresh_history)
        bar.addWidget(self._hist_refresh_btn)
        bar.addStretch()
        v.addLayout(bar)

        self._hist_chart = HistoryChart()
        self._hist_chart.setFixedHeight(170)
        v.addWidget(self._hist_chart)
        return group

    def _on_hist_range_changed(self, index):
        self._hist_chart.set_window(self._hist_range.currentData())
        # 换范围必须按新范围重新读取：set_window 只缩放坐标轴并重渲染
        # 已加载点，不重读则 1h→7d 后仍显示旧的 1h 数据（挤在轴右端）
        self._refresh_history()

    def _refresh_history(self):
        """从 service 读取所选范围的历史 t/s 数据并重绘历史图。"""
        points = self._service.load_history(self._hist_range.currentData())
        self._hist_chart.load_points(points)

    def _build_top_bar(self):
        w = QWidget()
        bar = QHBoxLayout(w)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QLabel("采样频率:"))
        self._freq = QComboBox()
        for label, val in self.FREQ_OPTIONS:
            self._freq.addItem(label, val)
        self._freq.setCurrentIndex(1)
        self._freq.setToolTip("数值越小，刷新越频繁，系统开销略增")
        self._freq.currentIndexChanged.connect(self._on_freq_changed)
        bar.addWidget(self._freq)
        bar.addStretch()
        return w

    def _build_system_group(self):
        group = QGroupBox("系统资源")
        grid = QGridLayout(group)
        grid.setVerticalSpacing(6)

        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        self._cpu_text = QLabel("0%")
        self._cpu_text.setFixedWidth(60)
        grid.addWidget(QLabel("CPU"), 0, 0)
        grid.addWidget(self._cpu_bar, 0, 1)
        grid.addWidget(self._cpu_text, 0, 2)

        self._ram_bar = QProgressBar()
        self._ram_bar.setRange(0, 100)
        self._ram_text = QLabel("0%  0 / 0 GB")
        self._ram_text.setFixedWidth(200)
        grid.addWidget(QLabel("RAM"), 1, 0)
        grid.addWidget(self._ram_bar, 1, 1)
        grid.addWidget(self._ram_text, 1, 2)

        return group

    def _build_status_group(self):
        group = QGroupBox("服务器状态")
        v = QVBoxLayout(group)

        self._tps_label = QLabel("推理速度: -- t/s")
        self._tps_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        v.addWidget(self._tps_label)

        self._uptime_label = QLabel("运行时长: --")
        v.addWidget(self._uptime_label)

        self._status_label = QLabel("状态: \u25cf 未运行")
        self._status_label.setStyleSheet("color: #888; font-weight: bold;")
        v.addWidget(self._status_label)

        v.addStretch()
        return group

    def _on_freq_changed(self, index):
        self._service.set_interval(self._freq.currentData())

    def set_focus_script(self, name):
        """聚焦脚本（脚本列表选中项）：t/s 标签优先显示该服务的速度。"""
        self._focus_name = name or ""

    @pyqtSlot(str, float)
    def update_tps(self, name, tps):
        """日志正则 t/s 通道（回退数据源），按脚本归属。

        /metrics 可用时该通道只作记录（不重复绘点）；不可用时在新鲜度
        窗口内作为该服务的 t/s 数据源。
        """
        now = time.time()
        self._log_tps[name] = (now, tps)
        if not self._metrics_ok.get(name, False):
            self._tps_chart.add_tps(name, tps)
            self._log_drawn_ts[name] = now
            # 日志回退通道的 t/s 也进历史落盘缓冲（与实时曲线同源同规则）
            self._service.record_tps(name, tps)
        self._maybe_update_label(name, tps)

    def _update_servers(self, servers):
        """合并 /metrics 推送（按服务维度）：可用则绘点，不可用则回退日志正则。"""
        now = time.time()
        active = set()
        for s in servers:
            name = s.get("name")
            if not name:
                continue
            active.add(name)
            ok = bool(s.get("ok"))
            self._metrics_ok[name] = ok
            tps = s.get("tps")
            if ok and tps is not None:
                self._tps_chart.add_tps(name, tps)
                self._maybe_update_label(name, tps)
        # 回退：/metrics 不可用且存在新鲜的日志正则 t/s
        for name in list(self._log_tps):
            ts, tps = self._log_tps[name]
            if now - ts > 300:
                del self._log_tps[name]
                self._log_drawn_ts.pop(name, None)
                continue
            if name in active and not self._metrics_ok.get(name, False) \
                    and now - ts < self.LOG_TPS_FALLBACK_WINDOW:
                # 每条日志行只绘一次：update_tps 已绘过则跳过；日志行在
                # _metrics_ok 仍为旧值(True)期间到达时，由扫描补绘
                if self._log_drawn_ts.get(name, 0.0) < ts:
                    self._tps_chart.add_tps(name, tps)
                    self._log_drawn_ts[name] = ts
                self._maybe_update_label(name, tps)

    def _maybe_update_label(self, name, tps):
        """t/s 标签：聚焦脚本优先；未聚焦时显示最近到达的值（单一服务时与旧行为一致）。"""
        if self._focus_name and name != self._focus_name:
            return
        self._tps_label.setText(f"推理速度: {tps:.1f} t/s")

    def on_server_started(self):
        self._server_running = True
        self._server_start_time = time.time()
        self._status_label.setText("状态: \u25cf 运行中")
        self._status_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        self._uptime_timer.start()

    def on_server_stopped(self):
        self._server_running = False
        self._status_label.setText("状态: \u25cf 未运行")
        self._status_label.setStyleSheet("color: #888; font-weight: bold;")
        self._uptime_label.setText("运行时长: --")
        self._uptime_timer.stop()

    def _update_uptime(self):
        if self._server_running:
            e = time.time() - self._server_start_time
            h, r = divmod(int(e), 3600)
            m, s = divmod(r, 60)
            self._uptime_label.setText(f"运行时长: {h:02d}:{m:02d}:{s:02d}")

    @pyqtSlot(dict)
    def _on_metrics(self, m):
        cpu = m["cpu"]
        self._cpu_bar.setValue(int(cpu))
        self._cpu_bar.setStyleSheet(_bar_style(cpu))
        self._cpu_text.setText(f"{cpu:.0f}%")

        rp = m["ram_percent"]
        self._ram_bar.setValue(int(rp))
        self._ram_bar.setStyleSheet(_bar_style(rp))
        self._ram_text.setText(f"{rp:.0f}%  {_fmt_bytes(m['ram_used'])} / {_fmt_bytes(m['ram_total'])}")

        self._update_gpus(m.get("gpus", []))
        self._update_servers(m.get("servers", []))

    def _update_gpus(self, gpus):
        if not gpus:
            # Qt 的 removeWidget 不改变父对象：仅判 parent 时，占位符被
            # removeWidget+hide 后就永远满足不了"无父"条件，"GPU 消失→恢复
            # →再消失"循环后占位符不再恢复。补 isHidden() 判定（widget 自身
            # 显隐标志，不受窗口可见性影响）。
            if not self._gpu_placeholder.parent() or self._gpu_placeholder.isHidden():
                for c in self._gpu_cards:
                    c.setParent(None)
                    c.deleteLater()
                self._gpu_cards.clear()
                self._gpu_inner.addWidget(self._gpu_placeholder)
                self._gpu_placeholder.show()
            return

        if self._gpu_placeholder.parent():
            self._gpu_inner.removeWidget(self._gpu_placeholder)
            self._gpu_placeholder.hide()

        while len(self._gpu_cards) < len(gpus):
            i = len(self._gpu_cards)
            card = GpuCard(i, gpus[i]["name"], self._gpu_group)
            self._gpu_cards.append(card)
            self._gpu_inner.addWidget(card)

        while len(self._gpu_cards) > len(gpus):
            c = self._gpu_cards.pop()
            c.setParent(None)
            c.deleteLater()

        for i, info in enumerate(gpus):
            self._gpu_cards[i].update(
                info["util"], info["mem_used"], info["mem_total"],
                info.get("temp"), info.get("power_w"),
            )
