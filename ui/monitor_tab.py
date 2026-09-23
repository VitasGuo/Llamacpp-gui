"""压缩版系统监控组件（嵌入主控制页，与日志并排显示）。

v1.15.0 起，原独立"性能监控"标签页（MonitorTab/TpsChart/HistoryChart）
已随监控能力并入主控制页而移除；本模块仅保留 CompactMonitor。
"""
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot


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


class _GpuRow(QWidget):
    """压缩版单 GPU 行：利用率 + 显存（温度并入显存文本），两行紧凑布局。"""

    def __init__(self, index, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        lbl = QLabel(f"GPU{index}")
        lbl.setFixedWidth(40)
        self.util_bar = QProgressBar()
        self.util_bar.setRange(0, 100)
        self.util_bar.setTextVisible(False)
        self.util_bar.setFixedHeight(16)
        self.util_label = QLabel("0%")
        self.util_label.setFixedWidth(44)
        row1.addWidget(lbl)
        row1.addWidget(self.util_bar, 1)
        row1.addWidget(self.util_label)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        lbl2 = QLabel("显存")
        lbl2.setFixedWidth(40)
        self.mem_bar = QProgressBar()
        self.mem_bar.setRange(0, 100)
        self.mem_bar.setTextVisible(False)
        self.mem_bar.setFixedHeight(16)
        self.mem_label = QLabel("0 / 0 GB")
        self.mem_label.setFixedWidth(190)
        row2.addWidget(lbl2)
        row2.addWidget(self.mem_bar, 1)
        row2.addWidget(self.mem_label)
        v.addLayout(row2)

    def update_stats(self, info):
        util = info["util"]
        self.util_bar.setValue(int(util))
        self.util_bar.setStyleSheet(_bar_style(util))
        self.util_label.setText(f"{util:.0f}%")

        mu, mt = info["mem_used"], info["mem_total"]
        pct = (mu / mt * 100) if mt > 0 else 0
        self.mem_bar.setValue(int(pct))
        self.mem_bar.setStyleSheet(_bar_style(pct))
        temp = info.get("temp")
        t = f"{temp:.0f}°C" if temp is not None else "N/A"
        self.mem_label.setText(f"{_fmt_bytes(mu)} / {_fmt_bytes(mt)} · {t}")


class CompactMonitor(QWidget):
    """主控制页嵌入的压缩版系统监控（CPU/RAM/GPU + t/s），与日志并排显示。

    加载模型/调参时无需切换标签即可同时查看日志与系统负载。
    """

    def __init__(self, monitor_service, parent=None):
        super().__init__(parent)
        self._service = monitor_service
        self._focus_name = ""
        self._running = {}   # 脚本名 -> 启动时间 ts
        self._gpu_rows = []  # [_GpuRow]
        self._setup_ui()
        self._service.metrics_updated.connect(self._on_metrics)
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.setInterval(1000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("系统负载")
        v = QVBoxLayout(group)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # CPU / RAM 行
        for label, setter, text_w in (
            ("CPU", "_cpu", 44),
            ("RAM", "_ram", 190),
        ):
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFixedWidth(40)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(16)
            txt = QLabel("--")
            txt.setFixedWidth(text_w)
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            row.addWidget(txt)
            v.addLayout(row)
            setattr(self, f"{setter}_bar", bar)
            setattr(self, f"{setter}_label", txt)

        # GPU 动态区（每卡一个 _GpuRow）
        self._gpu_placeholder = QLabel("未检测到 NVIDIA GPU")
        self._gpu_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gpu_placeholder.setStyleSheet("color: #888;")
        self._gpu_box = QVBoxLayout()
        self._gpu_box.setSpacing(4)
        if not self._service.is_gpu_available():
            self._gpu_box.addWidget(self._gpu_placeholder)
        v.addLayout(self._gpu_box)

        self._tps_label = QLabel("推理速度: -- t/s")
        self._tps_label.setStyleSheet("font-weight: bold;")
        v.addWidget(self._tps_label)
        self._uptime_label = QLabel("运行时长: --")
        v.addWidget(self._uptime_label)
        self._status_label = QLabel("状态: \u25cf 未运行")
        self._status_label.setStyleSheet("color: #888;")
        v.addWidget(self._status_label)

        layout.addWidget(group)

    def set_focus_script(self, name):
        """聚焦脚本（模型选中项）：t/s 与状态优先显示该服务。"""
        self._focus_name = name or ""

    @pyqtSlot(str, float)
    def update_tps(self, name, tps):
        """日志正则 t/s 通道（/metrics 不可用时的回退数据源）。

        除更新标签外，采样点也进历史落盘缓冲（record_tps），保证
        /metrics 不可用期间历史曲线不缺数据；缓冲按服务名覆盖，
        与 /metrics 通道并存不会重复写点。
        """
        self._update_tps_label(name, tps)
        self._service.record_tps(name, tps)

    def on_server_started(self, name):
        name = name or "default"
        self._running[name] = time.time()
        self._uptime_timer.start()
        self._refresh_status()

    def on_server_stopped(self, name):
        name = name or "default"
        self._running.pop(name, None)
        if not self._running:
            self._uptime_timer.stop()
        self._refresh_status()

    def on_all_servers_stopped(self):
        """全部 llama 进程被清理后整体重置（恢复的服务没有 LogWorker
        代理 on_server_stopped，由主窗口清理入口统一调用）。"""
        self._running.clear()
        self._uptime_timer.stop()
        self._refresh_status()

    def _update_tps_label(self, name, tps):
        if self._focus_name and name != self._focus_name:
            return
        self._tps_label.setText(f"推理速度: {tps:.1f} t/s")

    def _refresh_status(self):
        if self._focus_name and self._focus_name in self._running:
            self._status_label.setText("状态: \u25cf 运行中")
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")
        elif self._running:
            n = len(self._running)
            self._status_label.setText(f"状态: \u25cf {n} 个服务运行中")
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self._status_label.setText("状态: \u25cf 未运行")
            self._status_label.setStyleSheet("color: #888;")
            self._uptime_label.setText("运行时长: --")

    def _update_uptime(self):
        if not self._running:
            self._uptime_label.setText("运行时长: --")
            return
        if self._focus_name and self._focus_name in self._running:
            start = self._running[self._focus_name]
        else:
            start = max(self._running.values())
        e = time.time() - start
        h, r = divmod(int(e), 3600)
        m, s = divmod(r, 60)
        self._uptime_label.setText(f"运行时长: {h:02d}:{m:02d}:{s:02d}")

    @pyqtSlot(dict)
    def _on_metrics(self, m):
        cpu = m["cpu"]
        self._cpu_bar.setValue(int(cpu))
        self._cpu_bar.setStyleSheet(_bar_style(cpu))
        self._cpu_label.setText(f"{cpu:.0f}%")

        rp = m["ram_percent"]
        self._ram_bar.setValue(int(rp))
        self._ram_bar.setStyleSheet(_bar_style(rp))
        self._ram_label.setText(
            f"{rp:.0f}%  {_fmt_bytes(m['ram_used'])} / {_fmt_bytes(m['ram_total'])}"
        )

        self._update_gpus(m.get("gpus", []))
        for s in m.get("servers", []):
            if s.get("ok") and s.get("tps") is not None:
                self._update_tps_label(s.get("name") or "", s["tps"])

    def _update_gpus(self, gpus):
        if not gpus:
            # 仅判 parent 会在"消失→恢复"循环后不再恢复占位符
            if not self._gpu_placeholder.parent() or self._gpu_placeholder.isHidden():
                for w in self._gpu_rows:
                    self._gpu_box.removeWidget(w)
                    w.deleteLater()
                self._gpu_rows.clear()
                self._gpu_box.addWidget(self._gpu_placeholder)
                self._gpu_placeholder.show()
            return

        if self._gpu_placeholder.parent():
            self._gpu_box.removeWidget(self._gpu_placeholder)
            self._gpu_placeholder.hide()

        while len(self._gpu_rows) < len(gpus):
            w = _GpuRow(len(self._gpu_rows))
            self._gpu_rows.append(w)
            self._gpu_box.addWidget(w)

        while len(self._gpu_rows) > len(gpus):
            w = self._gpu_rows.pop()
            self._gpu_box.removeWidget(w)
            w.deleteLater()

        for i, info in enumerate(gpus):
            self._gpu_rows[i].update_stats(info)
