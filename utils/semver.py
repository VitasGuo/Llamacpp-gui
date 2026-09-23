"""语义化版本比较工具（semver）。

去 v 前缀、按数字段比较、预发布（alpha/beta/rc）低于正式版。
"""
import re

# 预发布标识（按字典序 alpha < beta < rc）
_PRERELEASE_TAGS = ("alpha", "beta", "rc")


def _parse_version(version):
    """拆分版本串为 (数字段元组, 预发布串或 None)。

    - 去 v/V 前缀
    - 预发布标识 alpha/beta/rc（可带序号，如 rc1、beta.2）
    - 数字部分按 '.' 分段取前导数字，非数字段记 0
    """
    v = version.strip().lower()
    if v.startswith("v"):
        v = v[1:]
    pre = None
    for tag in _PRERELEASE_TAGS:
        idx = v.find(tag)
        if idx > 0:
            rest = v[idx + len(tag):]
            if rest == "" or rest[0].isdigit() or rest[0] in ".-+_":
                pre = v[idx:]
                v = v[:idx]
                break
    segments = []
    for part in v.split("."):
        m = re.match(r"\d+", part)
        segments.append(int(m.group(0)) if m else 0)
    return tuple(segments), pre


def _prerelease_key(pre):
    """预发布排序键：(标识, 序号)，如 alpha < beta < rc、rc1 < rc2。"""
    m = re.match(r"([a-z]+)[.-]?(\d+)?", pre)
    if not m:
        return (pre, 0)
    return (m.group(1), int(m.group(2) or 0))


def compare_semver(a, b):
    """比较两个版本串，返回 -1/0/1（a 相对 b）。

    规则：忽略 v/V 前缀；按 '.' 分段的数字逐段比较（段数不足按 0 补齐）；
    数字段相同时，预发布（rc/beta/alpha）低于正式版；同为预发布时
    比较标识与序号（alpha < beta < rc，rc1 < rc2）。
    """
    seg_a, pre_a = _parse_version(a)
    seg_b, pre_b = _parse_version(b)
    n = max(len(seg_a), len(seg_b))
    seg_a += (0,) * (n - len(seg_a))
    seg_b += (0,) * (n - len(seg_b))
    if seg_a != seg_b:
        return -1 if seg_a < seg_b else 1
    if (pre_a is None) == (pre_b is None):
        if pre_a is None:
            return 0
        key_a, key_b = _prerelease_key(pre_a), _prerelease_key(pre_b)
        return -1 if key_a < key_b else (1 if key_a > key_b else 0)
    return -1 if pre_a is not None else 1
