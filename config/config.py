import os
import json

from config import APP_CONFIG_FILE
from utils.atomic_io import atomic_write_json

class Settings:
    _instance = None

    def __init__(self):
        self.config = {
            "llamacpp_path": "",
            "model_path": "",
        }

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    def load(self):
        if os.path.exists(APP_CONFIG_FILE):
            try:
                with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        os.makedirs(os.path.dirname(APP_CONFIG_FILE), exist_ok=True)
        atomic_write_json(APP_CONFIG_FILE, self.config, indent=4, ensure_ascii=False)

    @property
    def llamacpp_path(self):
        return self.config.get("llamacpp_path", "")

    @llamacpp_path.setter
    def llamacpp_path(self, value):
        self.config["llamacpp_path"] = value

    @property
    def model_path(self):
        return self.config.get("model_path", "")

    @model_path.setter
    def model_path(self, value):
        self.config["model_path"] = value

    @property
    def model_dir(self):
        return self.config.get("model_dir", "")

    @model_dir.setter
    def model_dir(self, value):
        self.config["model_dir"] = value

    @property
    def visual_model_path(self):
        return self.config.get("visual_model_path", "")

    @visual_model_path.setter
    def visual_model_path(self, value):
        self.config["visual_model_path"] = value

    @property
    def download_path(self):
        return self.config.get("download_path", "")

    @download_path.setter
    def download_path(self, value):
        self.config["download_path"] = value

    # ── 启动脚本默认参数（设置对话框可修改；未配置时用内置默认，与历史硬编码一致）──

    @property
    def gpu_layers(self):
        return self.config.get("gpu_layers", "99")

    @gpu_layers.setter
    def gpu_layers(self, value):
        self.config["gpu_layers"] = value

    @property
    def port(self):
        return self.config.get("port", "8080")

    @port.setter
    def port(self, value):
        self.config["port"] = value

    @property
    def ctx_size(self):
        return self.config.get("ctx_size", "32768")

    @ctx_size.setter
    def ctx_size(self, value):
        self.config["ctx_size"] = value

    @property
    def alias(self):
        return self.config.get("alias", "qwen")

    @alias.setter
    def alias(self, value):
        self.config["alias"] = value

    @property
    def host(self):
        return self.config.get("host", "0.0.0.0")

    @host.setter
    def host(self, value):
        self.config["host"] = value

    @property
    def np(self):
        return self.config.get("np", "2")

    @np.setter
    def np(self, value):
        self.config["np"] = value

    @property
    def b(self):
        return self.config.get("b", "2048")

    @b.setter
    def b(self, value):
        self.config["b"] = value

    @property
    def ub(self):
        return self.config.get("ub", "1024")

    @ub.setter
    def ub(self, value):
        self.config["ub"] = value

    @property
    def main_gpu(self):
        return self.config.get("main_gpu", "0")

    @main_gpu.setter
    def main_gpu(self, value):
        self.config["main_gpu"] = value

    @property
    def ts(self):
        return self.config.get("ts", "1,3")

    @ts.setter
    def ts(self, value):
        self.config["ts"] = value

    @property
    def spec_type(self):
        return self.config.get("spec_type", "draft-mtp")

    @spec_type.setter
    def spec_type(self, value):
        self.config["spec_type"] = value

    @property
    def spec_draft_n_max(self):
        return self.config.get("spec_draft_n_max", "2")

    @spec_draft_n_max.setter
    def spec_draft_n_max(self, value):
        self.config["spec_draft_n_max"] = value

    @property
    def cache_type_k(self):
        return self.config.get("cache_type_k", "q8_0")

    @cache_type_k.setter
    def cache_type_k(self, value):
        self.config["cache_type_k"] = value

    @property
    def cache_type_v(self):
        return self.config.get("cache_type_v", "q8_0")

    @cache_type_v.setter
    def cache_type_v(self, value):
        self.config["cache_type_v"] = value
