"""新建启动脚本的参数选择对话框。"""
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QDialog, QDialogButtonBox, QScrollArea,
)

from service.script_builder import CATEGORIES, get_switch_default


class NewScriptDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建启动脚本 - 选择参数")
        self.setFixedWidth(620)
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

                if sw.get("show_input", sw["default"] != ""):
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

    def get_config(self):
        result = {}
        for cat in CATEGORIES:
            for sw in cat["switches"]:
                cb = self.checkboxes[sw["key"]]
                if cb.isChecked():
                    if sw.get("show_input", sw["default"] != ""):
                        val = self.value_inputs[sw["key"]].text().strip()
                        if not val:
                            continue
                    else:
                        val = ""
                    result[sw["key"]] = val
        return result
