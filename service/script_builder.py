"""启动脚本参数构建逻辑。"""
import re

from config.config import Settings

# 监听方式下拉选项（新建脚本对话框的 --host）。
# "__tailscale__" 是哨兵值：对话框打开时解析为检测到的 Tailscale IP，
# 未检测到则回退 0.0.0.0，保证脚本仍可启动。
HOST_CHOICES = [
    {"label": "仅本机 (127.0.0.1)", "value": "127.0.0.1"},
    {"label": "所有接口 (0.0.0.0)", "value": "0.0.0.0"},
    {"label": "Tailscale 专用", "value": "__tailscale__"},
]

CATEGORIES = [
    {
        "title": "通用参数",
        "note": "",
        "checked": True,
        "switches": [
            {"key": "gpu_layers", "label": "--gpu-layers (GPU 层数)", "default": "99"},
            {"key": "port", "label": "--port (端口号)", "default": "8080"},
            {"key": "ctx_size", "label": "--ctx-size (上下文大小)", "default": "32768"},
            {"key": "alias", "label": "--alias (模型别名)", "default": "qwen"},
            {"key": "host", "label": "--host (监听方式)", "default": "0.0.0.0", "choices": HOST_CHOICES},
        ],
    },
    {
        "title": "并发与批处理参数",
        "note": "",
        "checked": False,
        "switches": [
            {"key": "np", "label": "-np (最大并发数量)", "default": "2", "numeric": True},
            {"key": "b", "label": "-b (逻辑批处理上限)", "default": "2048", "numeric": True},
            {"key": "ub", "label": "-ub (物理批处理上限)", "default": "1024", "numeric": True},
        ],
    },
    {
        "title": "模型参数",
        "note": "",
        "checked": False,
        "switches": [
            {"key": "no_mmproj_offload", "label": "--no-mmproj-offload (不加载视觉模型)", "default": ""},
            {"key": "mmproj", "label": "--mmproj (是否启用外挂视觉模型)", "default": ""},
            {"key": "reasoning", "label": "--reasoning off (关闭模型思考)", "default": ""},
            {"key": "main_gpu", "label": "--main-gpu (指定主推理gpu，单显卡忽略该参数)", "default": "0"},
            {"key": "ts", "label": "-ts (混合gpu负载, 例如1,3，意思为两张显卡负载比例为1:3，单显卡忽略该参数)", "default": "1,3"},
        ],
    },
    {
        "title": "MTP 参数",
        "note": "需要支持MTP的模型才能开启",
        "checked": False,
        "switches": [
            {"key": "spec_type", "label": "--spec-type (是否开启MTP预测-需模型支持)", "default": "draft-mtp"},
            {"key": "spec_draft_n_max", "label": "--spec-draft-n-max (额外预测token数)", "default": "2"},
        ],
    },
    {
        "title": "模型量化参数",
        "note": "",
        "checked": True,
        "switches": [
            {"key": "cache_type_k", "label": "--cache-type-k (是否开启k量化)", "default": "q8_0"},
            {"key": "cache_type_v", "label": "--cache-type-v (是否开启v量化)", "default": "q8_0"},
        ],
    },
    {
        "title": "MOE 模型参数",
        "note": "如果你不清楚什么是MOE模型，则下列参数均保持默认就好",
        "checked": False,
        "switches": [
            {"key": "n_cpu_moe", "label": "--n-cpu-moe (分配cpu线程数，需小于等于cpu物理核心数)", "default": "", "show_input": True},
            {"key": "mmap", "label": "--mmap (启用内存映射，动态加载权重)", "default": "", "checked": True},
            {"key": "no_mmap_fallback", "label": "--no-mmap-fallback (禁用回退加载模式，但可能导致显存爆炸，可尝试开启)", "default": ""},
        ],
    },
]


def get_switch_default(key):
    """取参数默认值：优先用 Settings 中用户保存的值；为空或未设置时回退 CATEGORIES 内置默认。

    这样无配置时行为与历史硬编码默认一致；用户在设置对话框保存过值后，
    该值用于新建脚本对话框的预填（用户仍可在对话框中再次覆盖）。
    """
    value = getattr(Settings.get_instance(), key, None)
    if value:
        return value
    for cat in CATEGORIES:
        for sw in cat["switches"]:
            if sw["key"] == key:
                return sw.get("default", "")
    return ""


def extract_port(content, default=8080):
    """解析 .bat 内容中的 --port 参数值（端口预检用）；缺失或非法时返回默认 8080
    （llama.cpp 未指定 --port 时的监听端口）。

    同时兼容 `--port 8080` 与 `--port=8080` 两种写法（用户手改 .bat 可能用等号）。
    """
    m = re.search(r"--port[=\s]+(\d+)", content or "")
    if m:
        try:
            port = int(m.group(1))
            if 0 < port <= 65535:
                return port
        except ValueError:
            pass
    return default


def extract_host(content, default="127.0.0.1"):
    """解析 .bat 内容中的 --host 值（运行时记录 host 用）；缺失时返回默认 127.0.0.1
    （llama.cpp 未指定 --host 时的默认监听地址）。

    兼容 `--host x.x.x.x` 与 `--host=x.x.x.x` 两种写法。
    """
    m = re.search(r"--host[=\s]+([0-9a-fA-F.:]+)", content or "")
    if m:
        return m.group(1).strip()
    return default


def build_bat_content(exe_dir, model_path, config, visual_model_path=""):
    """根据配置生成 .bat 启动脚本内容。"""
    lines = [
        "@echo off",
        f'cd /d "{exe_dir}"',
        "llama-server.exe ^",
        f'-m "{model_path}" ^',
    ]

    parts = []

    if "gpu_layers" in config:
        parts.append(f"--gpu-layers {config['gpu_layers']} ^")
    if "port" in config:
        parts.append(f"--port {config['port']} ^")
    if "ctx_size" in config:
        parts.append(f"--ctx-size {config['ctx_size']} ^")
    if "alias" in config:
        parts.append(f'--alias "{config["alias"]}" ^')
    if "np" in config:
        parts.append(f"-np {config['np']} ^")
    if "b" in config:
        parts.append(f"-b {config['b']} ^")
    if "ub" in config:
        parts.append(f"-ub {config['ub']} ^")
    if "no_mmproj_offload" in config:
        parts.append("--no-mmproj-offload ^")
    if "mmproj" in config:
        if visual_model_path:
            parts.append(f'--mmproj "{visual_model_path}" ^')
    if "reasoning" in config:
        parts.append("--reasoning off ^")
    if "main_gpu" in config:
        parts.append(f"--main-gpu {config['main_gpu']} ^")
    if "ts" in config:
        parts.append(f"-ts {config['ts']} ^")
    if "spec_type" in config:
        parts.append(f"--spec-type {config['spec_type']} ^")
    if "spec_draft_n_max" in config:
        parts.append(f"--spec-draft-n-max {config['spec_draft_n_max']} ^")
    if "cache_type_k" in config:
        parts.append(f"--cache-type-k {config['cache_type_k']} ^")
    if "cache_type_v" in config:
        parts.append(f"--cache-type-v {config['cache_type_v']} ^")
    if "n_cpu_moe" in config:
        parts.append(f"--n-cpu-moe {config['n_cpu_moe']} ^")
    if "mmap" in config:
        parts.append("--mmap ^")
    if "no_mmap_fallback" in config:
        parts.append("--no-mmap-fallback ^")
    if "host" in config:
        parts.append(f"--host {config['host']}")

    if parts:
        parts[-1] = parts[-1].rstrip(" ^")
    lines.extend(parts)
    return "\n".join(lines)
