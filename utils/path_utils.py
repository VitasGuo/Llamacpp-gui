"""路径规范化工具：统一分隔符为正斜线 /（跨平台规范形式）。

背景：Windows 下 Qt（QFileDialog）返回正斜线、os.path 系列返回反斜线、
历史脚本又两者混用，导致 GUI 显示与 .bat 内容里路径分隔符不统一
（如 `C:/modelscope\\models\\...` 混合写法）。Windows 文件 API 两种都接受，
功能不受影响，但观感混乱、字符串比较脆弱。

约定：本项目所有路径以正斜线 / 为唯一规范形式——
POSIX 原生、Windows 文件 API / cmd / llama-server 完全兼容、
JSON 存储免转义、Qt 原生返回值即正斜线。凡写入配置、脚本、日志的路径
都必须先过 normalize_path()。
"""


def normalize_path(path):
    """把路径分隔符统一为正斜线 /；None/空串原样返回（幂等，可重复调用）。"""
    if not path:
        return path
    return path.replace("\\", "/")
