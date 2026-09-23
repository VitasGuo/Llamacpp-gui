"""表单式脚本编辑器：把启动脚本参数展示为填空/下拉/勾选，避免直接编辑 .bat 代码。

脚本绑定模型（无独立脚本名）。全部参数集中在一个可滚动面板：
- 常用分组（通用/模型/量化）直接显示；
- 高级参数（并发批处理/MTP/MOE）收进底部可折叠的"高级参数"区（默认收起）。

模型别名（--alias）自动取模型文件名，只读，不用用户手工填。
"""
import os
import re

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QComboBox, QScrollArea, QGroupBox, QWidget, QToolButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator

from service.script_builder import CATEGORIES, get_switch_default, ctx_options_for
from service.tailscale import get_tailscale_ipv4, is_tailscale_ip

# 主面板展示的常用分组（按标题匹配；其余归入"高级参数"折叠区）
COMMON_TITLES = {"通用参数", "模型参数", "模型量化参数"}
# 自动生成、只读的参数 key（模型别名 = 模型文件名，随选中模型自动带出）
AUTO_READONLY_KEYS = {"alias"}
# 无模型时 ctx 下拉的初始挡位与默认值（选中模型后按 GGUF 上限刷新）
_CTX_INIT_TIERS = [8192, 16384, 32768, 65536, 131072]
_CTX_INIT_DEFAULT = 32768


def _ctx_tier_label(value):
    """挡位显示文本：8192 → "8K (8192)"，1048576 → "1M (1048576)"；非 1024 倍数直接数字。"""
    if value % 1048576 == 0:
        return f"{value // 1048576}M ({value})"
    if value % 1024 == 0:
        return f"{value // 1024}K ({value})"
    return str(value)


def _ctx_text_value(text):
    """从 ctx 下拉文本取数值：挡位文本取括号内数字，手填纯数字原样返回。"""
    m = re.search(r"\((\d+)\)\s*$", text or "")
    return m.group(1) if m else (text or "").strip()


def _build_choice_widget(choices, saved):
    """下拉框：__tailscale__ 哨兵解析为实际 Tailscale IP（未检测回退 0.0.0.0）。"""
    combo = QComboBox()
    ts_ip = get_tailscale_ipv4()
    for choice in choices:
        value, label = choice["value"], choice["label"]
        if value == "__tailscale__":
            if ts_ip:
                value, label = ts_ip, f"Tailscale 专用 ({ts_ip})"
            else:
                value, label = "0.0.0.0", "Tailscale 专用 (未检测到，回退 0.0.0.0)"
        combo.addItem(label, value)
    idx = combo.findData(saved)
    if idx < 0:
        idx = combo.findData("0.0.0.0")
    if idx < 0:
        idx = 0
    combo.setCurrentIndex(idx)
    return combo


class _CollapsibleSection(QWidget):
    """可折叠分组：点击标题箭头展开/收起内部内容（默认收起）。"""

    def __init__(self, title, content_widget, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QToolButton()
        self.header.setText(title)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.setStyleSheet("QToolButton { font-weight: bold; }")
        self.header.toggled.connect(self._toggle)
        layout.addWidget(self.header)

        self.content = content_widget
        self.content.setVisible(False)
        layout.addWidget(self.content)

    def _toggle(self, checked):
        self.header.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.content.setVisible(checked)

    def set_collapsed(self, collapsed):
        self.header.setChecked(not collapsed)
        self._toggle(not collapsed)


class ScriptFormWidget(QWidget):
    """脚本参数表单（主控制页常用参数 + 底部可折叠的高级参数，共用同一份状态）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkboxes = {}      # key -> QCheckBox
        self.value_inputs = {}    # key -> QComboBox / QLineEdit / None
        self.model_label = None
        self._ctx_default = str(_CTX_INIT_DEFAULT)  # 当前模型 ctx 挡位默认值
        self.widget = self._build()


    def _build(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 模型只读信息
        top = QHBoxLayout()
        self.model_label = QLabel("模型: （未选择）")
        self.model_label.setStyleSheet("color: gray; font-size: 11px;")
        self.model_label.setWordWrap(True)
        top.addWidget(self.model_label)
        layout.addLayout(top)

        # 高级参数分组的内容（默认收起）
        adv_content = QWidget()
        adv_layout = QVBoxLayout(adv_content)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(6)

        for cat in CATEGORIES:
            if not cat["switches"]:
                continue
            group = QGroupBox(cat.get("title") or "参数")
            gv = QVBoxLayout(group)
            gv.setSpacing(4)
            if cat.get("note"):
                note = QLabel(cat["note"])
                note.setStyleSheet("color: #cc6600; font-size: 11px;")
                note.setWordWrap(True)
                gv.addWidget(note)
            for sw in cat["switches"]:
                row = QHBoxLayout()
                row.setSpacing(6)
                cb = QCheckBox(sw["label"])
                cb.setChecked(sw.get("checked", cat["checked"]))
                self.checkboxes[sw["key"]] = cb
                row.addWidget(cb)

                if sw.get("key") == "ctx_size":
                    # ctx：可编辑下拉（挡位点选 + 任意值手填），挡位随模型上限刷新
                    combo = self._build_ctx_widget()
                    self.value_inputs[sw["key"]] = combo
                    row.addWidget(combo)
                elif sw.get("choices"):
                    combo = _build_choice_widget(sw["choices"], get_switch_default(sw["key"]))
                    self.value_inputs[sw["key"]] = combo
                    row.addWidget(combo)
                elif sw.get("show_input", sw["default"] != ""):
                    le = QLineEdit(get_switch_default(sw["key"]))
                    if sw["key"] in AUTO_READONLY_KEYS:
                        # 模型别名由模型文件名自动生成，只读
                        le.setReadOnly(True)
                    if sw.get("numeric"):
                        le.setValidator(QIntValidator(0, 1000000000))
                    self.value_inputs[sw["key"]] = le
                    row.addWidget(le)
                else:
                    self.value_inputs[sw["key"]] = None
                row.addStretch()
                gv.addLayout(row)
            target = adv_layout if cat.get("title") not in COMMON_TITLES else layout
            target.addWidget(group)

        adv_layout.addStretch()
        layout.addWidget(_CollapsibleSection("高级参数", adv_content))
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        return outer

    def _build_ctx_widget(self):
        """ctx-size 可编辑下拉：挡位候选项可点选，lineEdit 可手填任意值。

        挡位列表与默认值由 _update_ctx_tiers 按当前模型的 GGUF 上限刷新；
        NoInsert 防手填值被自动插进候选项。
        """
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        le = combo.lineEdit()
        if le:
            le.setValidator(QIntValidator(1, 1000000000))
        for t in _CTX_INIT_TIERS:
            combo.addItem(_ctx_tier_label(t), str(t))
        combo.setCurrentIndex(max(combo.findData(str(_CTX_INIT_DEFAULT)), 0))
        return combo

    def _update_ctx_tiers(self, model_path):
        """按模型 GGUF 上下文上限刷新 ctx 挡位（set_model_path 时调用）。

        当前值（含手填）尽量保留（同模型重复调用不丢用户输入）；
        无模型时回到初始挡位。默认挡位存 _ctx_default 供整体重置用。
        """
        combo = self.value_inputs.get("ctx_size")
        if not isinstance(combo, QComboBox):
            return
        if model_path:
            tiers, default = ctx_options_for(model_path)
        else:
            tiers, default = _CTX_INIT_TIERS, _CTX_INIT_DEFAULT
        self._ctx_default = str(default)
        current = _ctx_text_value(combo.currentText())
        combo.clear()
        for t in tiers:
            combo.addItem(_ctx_tier_label(t), str(t))
        if current.isdigit():
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(current)
        else:
            combo.setCurrentIndex(max(combo.findData(self._ctx_default), 0))

    # ── 对外接口 ───────────────────────────────────────────────

    def set_model_path(self, model_path):
        """显示当前模型（只读信息），并按模型上限刷新 ctx 挡位。"""
        if model_path:
            base = model_path.replace("\\", "/").rsplit("/", 1)[-1]
            self.model_label.setText(f"模型: {base}\n{model_path}")
        else:
            self.model_label.setText("模型: （未选择）")
        self._update_ctx_tiers(model_path)

    def set_alias_auto(self, model_path):
        """自动设置模型别名（= 模型文件名，去扩展名）；无模型时忽略。"""
        if "alias" not in self.checkboxes:
            return
        base = os.path.splitext(os.path.basename(model_path or ""))[0]
        if base:
            self.checkboxes["alias"].setChecked(True)
            w = self.value_inputs.get("alias")
            if isinstance(w, QLineEdit):
                w.setText(base)

    def refresh_tailscale_ip(self):
        """Tailscale 手动指定 IP 变化后，刷新 host 下拉里的 Tailscale 选项（live 生效）。"""
        combo = self.value_inputs.get("host")
        if not isinstance(combo, QComboBox):
            return
        ts_ip = get_tailscale_ipv4()
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data == "__tailscale__" or is_tailscale_ip(data or ""):
                if ts_ip:
                    combo.setItemText(i, f"Tailscale 专用 ({ts_ip})")
                    combo.setItemData(i, ts_ip)
                break

    def _reset_to_defaults(self):
        """把全部开关/值恢复到 CATEGORIES 出厂默认（消除上一个模型的残留勾选）。

        切换模型时表单必须整体回到默认再回填，否则模型 A 的高级参数勾选
        会残留到模型 B，运行前自动保存会把残留参数永久写进 B 的脚本。
        """
        for cat in CATEGORIES:
            for sw in cat.get("switches", []):
                key = sw["key"]
                cb = self.checkboxes.get(key)
                if cb is None:
                    continue
                cb.setChecked(sw.get("checked", cat.get("checked", False)))
                w = self.value_inputs.get(key)
                default = get_switch_default(key)
                if isinstance(w, QComboBox):
                    if w.isEditable():
                        # ctx 挡位默认值随模型（GGUF 上限）而非 CATEGORIES 静态默认
                        default = self._ctx_default
                    idx = w.findData(default)
                    if idx < 0:
                        idx = w.findData("0.0.0.0")
                    if idx < 0:
                        idx = 0
                    w.setCurrentIndex(idx)
                elif isinstance(w, QLineEdit):
                    w.setText(default)

    def set_preset(self, config):
        """用参数 dict 回填表单（选中模型已绑定脚本回填 / 自动生成结果）。

        先整体重置为 CATEGORIES 默认，再回填 config 中出现的 key：
        未出现的 key 一律回到默认（不勾选/默认值），保证表单与目标脚本
        内容一致——这是"表单为事实源"的前提，残留会导致切模型污染。
        """
        self._reset_to_defaults()
        config = config or {}
        for key in self.checkboxes:
            if key not in config:
                continue
            val = config[key]
            w = self.value_inputs[key]
            if isinstance(w, QComboBox):
                idx = w.findData(val)
                if idx >= 0:
                    w.setCurrentIndex(idx)
                elif w.isEditable():
                    # 手填/历史值不在挡位里：直接填进编辑框（不丢用户配置）
                    w.setEditText(str(val))
            elif isinstance(w, QLineEdit):
                if val:
                    w.setText(str(val))
            else:
                self.checkboxes[key].setChecked(bool(val) and val not in ("", "off"))
            # 出现在 config 中的参数即视为启用的开关
            if val not in (None, "", "off"):
                self.checkboxes[key].setChecked(True)

    def clear_form(self):
        """清空为"未配置"（新模型无绑定脚本时）。"""
        for cb in self.checkboxes.values():
            cb.setChecked(False)

    def get_config(self):
        """把表单读成 config dict（面向 build_bat_content）。"""
        result = {}
        for key, cb in self.checkboxes.items():
            if not cb.isChecked():
                continue
            w = self.value_inputs[key]
            if isinstance(w, QComboBox):
                if w.isEditable():
                    # 手填值不在任何候选项上，必须读 currentText（currentData
                    # 会停留在旧候选项的值，与所见不一致）；挡位文本取括号内数字
                    val = _ctx_text_value(w.currentText())
                else:
                    val = w.currentData() or ""
            elif isinstance(w, QLineEdit):
                val = w.text().strip()
            else:
                val = "on"
            if not val and isinstance(w, (QComboBox, QLineEdit)):
                continue  # 勾选了但值为空 → 跳过该参数
            result[key] = val
        return result