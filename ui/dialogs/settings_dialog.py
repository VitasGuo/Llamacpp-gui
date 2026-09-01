"""启动脚本默认参数设置对话框。

表单编辑 script_builder.CATEGORIES 中各参数的默认值；保存走
Settings.get_instance() 属性修改 + save()，取消不保存。
分组与 CATEGORIES 一致（不含本轮范围外的字段）。
"""
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDialogButtonBox, QScrollArea, QWidget, QCheckBox,
)

from config.config import Settings
from service import autostart_service

# (分组标题, [(Settings 字段名, 显示标签), ...]) —— 分组与 script_builder.CATEGORIES 对应
SECTIONS = [
    ("通用参数", [
        ("gpu_layers", "--gpu-layers (GPU 层数)"),
        ("port", "--port (端口号)"),
        ("ctx_size", "--ctx-size (上下文大小)"),
        ("alias", "--alias (模型别名)"),
        ("host", "--host (监听地址)"),
    ]),
    ("并发与批处理参数", [
        ("np", "-np (最大并发数量)"),
        ("b", "-b (逻辑批处理上限)"),
        ("ub", "-ub (物理批处理上限)"),
    ]),
    ("模型参数", [
        ("main_gpu", "--main-gpu (指定主推理gpu，单显卡忽略该参数)"),
        ("ts", "-ts (混合gpu负载, 例如1,3，单显卡忽略该参数)"),
    ]),
    ("MTP 参数", [
        ("spec_type", "--spec-type (MTP预测类型-需模型支持)"),
        ("spec_draft_n_max", "--spec-draft-n-max (额外预测token数)"),
    ]),
    ("模型量化参数", [
        ("cache_type_k", "--cache-type-k (k量化)"),
        ("cache_type_v", "--cache-type-v (v量化)"),
    ]),
]

# 纯数字字段（整数校验，与新建脚本对话框的 numeric 处理一致）
_INT_KEYS = {"gpu_layers", "port", "ctx_size", "np", "b", "ub", "main_gpu", "spec_draft_n_max"}


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 - 启动脚本默认参数")
        self.setFixedWidth(560)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        tip = QLabel("以下默认值用于新建启动脚本的参数预填，保存后对新创建的脚本生效。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        settings = Settings.get_instance()

        # 常规区域：开机自启动（写入注册表），与托盘菜单开关同步
        gen_label = QLabel("常规")
        gen_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 8px 0 2px 0;")
        layout.addWidget(gen_label)
        self.auto_start_check = QCheckBox("开机自动启动（关闭窗口时最小化到系统托盘）")
        self.auto_start_check.setChecked(autostart_service.is_enabled())
        layout.addWidget(self.auto_start_check)

        form = QFormLayout()
        self.inputs = {}
        for title, fields in SECTIONS:
            section_label = QLabel(title)
            section_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 8px 0 2px 0;")
            form.addRow(section_label)
            for key, text in fields:
                edit = QLineEdit(str(getattr(settings, key, "")))
                if key in _INT_KEYS:
                    edit.setValidator(QIntValidator(0, 1000000000))
                self.inputs[key] = edit
                form.addRow(QLabel(text), edit)

        scroll = QWidget()
        scroll.setLayout(form)
        scroll_area = QScrollArea()
        scroll_area.setWidget(scroll)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        button_box.accepted.connect(self._save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _save(self):
        settings = Settings.get_instance()
        for key, edit in self.inputs.items():
            setattr(settings, key, edit.text().strip())
        settings.save()
        autostart_service.set_enabled(self.auto_start_check.isChecked())
        self.accept()
