"""新建启动脚本的参数选择对话框。"""
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QScrollArea,
)

from service.script_builder import CATEGORIES, get_switch_default
from service.tailscale import get_tailscale_ipv4


def _build_choice_widget(choices, saved):
    """构建下拉框：解析哨兵值 __tailscale__ 为实际 Tailscale IP。

    返回 (QComboBox, tailscale_ip)；未检测到 Tailscale 时该项回退 0.0.0.0。
    """
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
    return combo, ts_ip


class NewScriptDialog(QDialog):
    def __init__(self, parent=None, visual_model_path="", default_name=""):
        super().__init__(parent)
        self.setWindowTitle("新建启动脚本 - 选择参数")
        self.setFixedWidth(620)
        self._visual_model_path = visual_model_path
        self._default_name = default_name
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        tip = QLabel(
            "勾选需要启用的参数，并填写对应值，需要注意llama.cpp版本，"
            "某些参数需要新版才能支持。如果发现某些参数启用后报错，"
            "则需要升级llamacpp版本，或者关闭该参数。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # 脚本名称：预填所选模型文件名（自动命名），可修改
        name_row = QHBoxLayout()
        name_label = QLabel("脚本名称:")
        name_label.setStyleSheet("font-weight: bold;")
        name_row.addWidget(name_label)
        self.name_edit = QLineEdit(self._default_name)
        name_row.addWidget(self.name_edit)
        layout.addLayout(name_row)

        form = QFormLayout()
        self.checkboxes = {}
        self.value_inputs = {}

        for cat in CATEGORIES:
            if cat["title"]:
                label = QLabel(cat["title"])
                label.setStyleSheet(
                    "font-weight: bold; font-size: 12px; padding: 8px 0 2px 0;"
                )
                form.addRow(label)

            if cat["note"]:
                note = QLabel(cat["note"])
                note.setStyleSheet(
                    "color: #cc6600; font-size: 11px; padding: 0 0 4px 0;"
                )
                note.setWordWrap(True)
                form.addRow(note)

            for sw in cat["switches"]:
                cb = QCheckBox(sw["label"])
                cb.setChecked(sw.get("checked", cat["checked"]))
                self.checkboxes[sw["key"]] = cb

                if sw.get("choices"):
                    # 下拉选项（如 --host 监听方式）：预填 Settings 用户值，Tailscale 哨兵自动解析
                    combo, _ = _build_choice_widget(
                        sw["choices"], get_switch_default(sw["key"])
                    )
                    val_widget = combo
                elif sw.get("show_input", sw["default"] != ""):
                    # 默认值预填：Settings 用户设置优先，否则 CATEGORIES 内置默认
                    val_widget = QLineEdit(get_switch_default(sw["key"]))
                    if sw.get("numeric"):
                        val_widget.setValidator(QIntValidator(0, 1000000000))
                else:
                    val_widget = None
                self.value_inputs[sw["key"]] = val_widget

                row = QHBoxLayout()
                row.addWidget(cb)
                if val_widget:
                    row.addWidget(val_widget)
                row.addStretch()

                form_row = QWidget()
                form_row.setLayout(row)
                form.addRow(form_row)

        scroll = QWidget()
        scroll.setLayout(form)
        scroll_area = QScrollArea()
        scroll_area.setMinimumHeight(300)
        scroll_area.setWidget(scroll)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # 当前模型已绑定视觉编码器时，默认勾选 --mmproj，多模态能力开箱即用
        if self._visual_model_path and "mmproj" in self.checkboxes:
            self.checkboxes["mmproj"].setChecked(True)

    def get_name(self):
        """返回脚本名称（去除首尾空白；空名由调用方校验）。"""
        return self.name_edit.text().strip()

    def get_config(self):
        result = {}
        for cat in CATEGORIES:
            for sw in cat["switches"]:
                cb = self.checkboxes[sw["key"]]
                if not cb.isChecked():
                    continue
                val_widget = self.value_inputs[sw["key"]]
                if sw.get("choices"):
                    # 下拉控件（如 --host）：必须用 currentData() 取值，
                    # QComboBox 没有 text() 方法，误调会直接 AttributeError 崩溃
                    val = val_widget.currentData() or ""
                elif sw.get("show_input", sw["default"] != ""):
                    val = val_widget.text().strip()
                else:
                    val = ""
                if not val and (sw.get("choices") or sw.get("show_input", sw["default"] != "")):
                    continue  # 勾选了但值为空：跳过该参数
                result[sw["key"]] = val
        return result
