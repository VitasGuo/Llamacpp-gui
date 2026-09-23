"""启动脚本参数构建逻辑。"""
import os
import re
import struct

from config.config import Settings
from utils.path_utils import normalize_path

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


def find_mmproj(model_path):
    """在模型同目录查找视觉编码器 mmproj 文件（文件名含 mmproj，不区分大小写）。

    返回第一个匹配文件的完整路径；目录不存在或未找到时返回空串。
    llama-server 的 --mmproj 指定多模态模型的视觉编码器，通常与主模型同目录存放；
    命名有两种常见形式：`mmproj-<模型>.gguf` 或 `<模型>-mmproj-<精度>.gguf`。
    """
    if not model_path:
        return ""
    directory = os.path.dirname(model_path)
    try:
        for f in sorted(os.listdir(directory)):
            low = f.lower()
            if "mmproj" in low and low.endswith(".gguf"):
                return normalize_path(os.path.join(directory, f))
    except OSError:
        pass
    return ""


def build_bat_content(exe_dir, model_path, config, visual_model_path=""):
    """根据配置生成 .bat 启动脚本内容。

    所有路径统一规范化（正斜线 /）：cmd/llama-server 在 Windows 下兼容，
    JSON 存储与跨平台一致（见 utils/path_utils.py 约定）。
    """
    exe_dir = normalize_path(exe_dir)
    model_path = normalize_path(model_path)
    visual_model_path = normalize_path(visual_model_path)

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
        # 未手动指定视觉模型时，自动绑定同目录的 mmproj 文件，保证多模态能力开箱即用
        visual_model_path = visual_model_path or find_mmproj(model_path)
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
    if "no_mmap_fallback" in config:
        parts.append("--no-mmap-fallback ^")
    if "host" in config:
        parts.append(f"--host {config['host']}")

    if parts:
        parts[-1] = parts[-1].rstrip(" ^")
    lines.extend(parts)
    return "\n".join(lines)


# 参数 key → CLI 参数名 映射（镜像 build_bat_content 的写法，供反向解析/表单回填）
_CLI_VALUE_KEYS = [
    ("gpu_layers", "--gpu-layers"),
    ("port", "--port"),
    ("ctx_size", "--ctx-size"),
    ("alias", "--alias"),
    ("np", "-np"),
    ("b", "-b"),
    ("ub", "-ub"),
    ("reasoning", "--reasoning"),
    ("main_gpu", "--main-gpu"),
    ("ts", "-ts"),
    ("spec_type", "--spec-type"),
    ("spec_draft_n_max", "--spec-draft-n-max"),
    ("cache_type_k", "--cache-type-k"),
    ("cache_type_v", "--cache-type-v"),
    ("n_cpu_moe", "--n-cpu-moe"),
    ("host", "--host"),
]
# 无值开关参数（存在即视为勾选）
_CLI_SWITCH_KEYS = [
    ("no_mmproj_offload", "--no-mmproj-offload"),
    ("mmproj", "--mmproj"),          # 带值，但表单里作开关
    ("no_mmap_fallback", "--no-mmap-fallback"),
]

# ctx 挡位：文件大小启发分级（读不到 GGUF 元数据时的回退，<2.5G / <6G）
_CTX_TIERS = [((1 << 20) * 2500, 65536), ((1 << 20) * 6000, 32768)]
# 128K：模型上限 ≥ 此值时默认挡位取 128K（更大上限由用户手动选更高挡）
CTX_DEFAULT_CAP = 131072
# 基础挡位（8K ~ 1M，含 200K 细分挡），实际挡位按模型上限裁剪
_CTX_BASE_TIERS = [8192, 16384, 32768, 65536, 131072, 204800, 262144, 524288, 1048576]

# GGUF 元数据值类型 → 定长字节数（string/array 变长，单独处理）
_GGUF_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_GGUF_STRING, _GGUF_ARRAY = 8, 9
# 整数类型（UINT8~INT64），context_length 以这些类型存储
_GGUF_INT_TYPES = (0, 1, 2, 3, 4, 5, 10, 11)


def _read_gguf_string(f):
    """读一个 GGUF string（uint64 长度 + 字节）；异常返回 None。"""
    head = f.read(8)
    if len(head) < 8:
        return None
    n = struct.unpack("<Q", head)[0]
    if n > 4096:  # 元数据 key 不会这么长，防异常文件
        return None
    raw = f.read(n)
    if len(raw) < n:
        return None
    return raw.decode("utf-8", errors="ignore")


def _skip_gguf_value(f, vtype):
    """跳过一个 KV 值；无法安全跳过（嵌套数组/超大数组/未知类型）返回 False。"""
    if vtype in _GGUF_SCALAR_SIZES:
        f.seek(_GGUF_SCALAR_SIZES[vtype], 1)
        return True
    if vtype == _GGUF_STRING:
        return _read_gguf_string(f) is not None
    if vtype == _GGUF_ARRAY:
        head = f.read(12)
        if len(head) < 12:
            return False
        etype, count = struct.unpack("<IQ", head)
        # 嵌套数组/超大数组（如 15 万词条的 tokenizer）逐元素跳过太慢，直接放弃
        if etype == _GGUF_ARRAY or count > 100_000:
            return False
        if etype == _GGUF_STRING:
            return all(_read_gguf_string(f) is not None for _ in range(count))
        size = _GGUF_SCALAR_SIZES.get(etype)
        if size is None:
            return False
        f.seek(size * count, 1)
        return True
    return False


def read_gguf_context_length(model_path):
    """读 GGUF 元数据里的模型上下文上限（*.context_length KV，如 general.context_length）。

    只扫文件头部的 KV 区：general.*/llama.* 通常排在超大 tokenizer 数组之前，
    命中即停，读取开销极小；找不到/解析失败返回 None（调用方回退启发分级）。
    """
    try:
        with open(model_path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            version = struct.unpack("<I", f.read(4))[0]
            if version < 1 or version > 99:
                return None
            f.seek(8, 1)  # tensor_count（不需要）
            kv_count = struct.unpack("<Q", f.read(8))[0]
            if kv_count > 100_000:
                return None
            for _ in range(kv_count):
                key = _read_gguf_string(f)
                if key is None:
                    return None
                vtype = struct.unpack("<I", f.read(4))[0]
                if key.endswith("context_length") and vtype in _GGUF_INT_TYPES:
                    size = _GGUF_SCALAR_SIZES[vtype]
                    data = f.read(size)
                    if len(data) < size:
                        return None
                    value = int.from_bytes(data, "little", signed=vtype in (1, 3, 5, 11))
                    return value if value > 0 else None
                if not _skip_gguf_value(f, vtype):
                    return None
    except (OSError, struct.error):
        return None
    return None


def ctx_options_for(model_path):
    """按模型生成 ctx 挡位与默认值（表单下拉挡位 / 一键生成共用的事实源）。

    规则：读 GGUF 元数据上限 max_ctx ——
    - 上限 < 128K：默认 = 模型最大值（挡位含上限本身）；
    - 上限 ≥ 128K：默认 = 128K（更高挡位留给用户手选/手填）；
    - 读不到元数据：回退按文件大小启发（<2.5G→65536，<6G→32768，否则 16384）。
    挡位 = ≤上限的全部基础挡位（8K~1M）+ 上限本身（若非整挡）。
    返回 (挡位列表 asc, 默认值)。
    """
    max_ctx = read_gguf_context_length(model_path) if model_path else None
    if not max_ctx:
        try:
            size = os.path.getsize(model_path) if model_path else 0
        except OSError:
            size = 0
        max_ctx = 16384
        for _size, _ctx in _CTX_TIERS:
            if size < _size:
                max_ctx = _ctx
                break
    tiers = [t for t in _CTX_BASE_TIERS if t <= max_ctx]
    if not tiers:
        tiers = [max_ctx]
    elif max_ctx not in tiers:
        tiers = sorted(tiers + [max_ctx])
    default = max_ctx if max_ctx < CTX_DEFAULT_CAP else CTX_DEFAULT_CAP
    return tiers, default


def auto_generate_config(model_path):
    """按所选模型文件自动推算一份可用参数（基础模板）。

    规则：alias=模型文件名（去扩展名）；ctx 挡位按 GGUF 元数据上限
    （上限<128K 取最大值，≥128K 默认 128K，读不到回退文件大小分级）；
    KV 缓存 q8_0 量化；gpu-layers 默认 99；端口 8080，host 跟随用户设置；
    若有同目录 mmproj 则自动开 --mmproj。返回 dict（键同 CATEGORIES）。
    """
    cfg = {}
    base = os.path.splitext(os.path.basename(model_path or ""))[0]
    if base:
        cfg["alias"] = base
    _, default_ctx = ctx_options_for(model_path)
    cfg["ctx_size"] = str(default_ctx)
    cfg["cache_type_k"] = "q8_0"
    cfg["cache_type_v"] = "q8_0"
    cfg["gpu_layers"] = "99"
    cfg["port"] = get_switch_default("port") or "8080"
    host = get_switch_default("host") or "0.0.0.0"
    cfg["host"] = host
    if find_mmproj(model_path):
        cfg["mmproj"] = "on"
    return cfg


def parse_bat_params(content):
    """把 .bat 内容反解为参数字典（回填表单用）。

    返回 {key: value}，只含表单字段（不含模型路径/exe 目录）；无值参数存在
    则取值 "on"。解析不到的键不出现（保持优先级用 get_switch_default 兜底）。
    """
    content = content or ""
    params = {}
    for key, flag in _CLI_VALUE_KEYS:
        # 注意：bat 每行以 " ^" 续行，flag 前是 ^/换行（non-word），不能用 \b；
        # (?<![\w-]) 防短 flag（-b/-ts）误匹配模型路径等位置的同形子串；
        # 值兼容带引号（alias 等含空格，build 侧总是加引号）。
        m = re.search(rf"(?<![\w-]){re.escape(flag)}[=\s]+(\"[^\"]*\"|\S+)", content)
        if m:
            v = m.group(1).strip().strip('"').strip("'")
            params[key] = v
    for key, flag in _CLI_SWITCH_KEYS:
        if re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", content):
            params[key] = "on"
    return params


def parse_model_path_from_bat(content):
    """从 .bat 提取 -m "<模型路径>"，取不到返回空串。"""
    m = re.search(r"-m\s+\"([^\"]+)\"", content or "")
    return m.group(1).strip().replace("\\", "/") if m else ""
