"""日志与落库文本的 base64 脱敏。

IoT 推送链路里图片/文件常以 base64 或 data:image URL 出现在请求体、
变量表和前置步骤提取值里，直接打日志或落库会把记录撑到几十万字符。
这里把超长 base64 串统一替换成「保留前几个字符 + 长度」的占位符，
日志可读、长度可查，原始数据仍可从业务侧复现。

用法：
    from utils.log_redact import redact_base64

    logger.info("param_map: %s", redact_base64(param_map))
    entry["extracted"] = redact_base64(item.get("extracted") or {})
"""

import re

# 连续 base64 字符达到该长度才视为二进制数据（图片/文件 base64、JWT 等），
# 短于该阈值的串（普通 ID、短 token）原样保留
DEFAULT_MIN_CHARS = 120
# 占位符里保留开头字符数，方便肉眼辨认是哪张图
KEEP_HEAD = 8

# base64 标准/URL 安全字符集；结尾允许 1-2 个填充等号。
# 不含 \n，换行会自然截断长串（本项目的 base64 均为单行）。
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/\-_]{%d,}={0,2}" % DEFAULT_MIN_CHARS)


def _shorten(match: "re.Match") -> str:
    raw = match.group(0)
    if len(raw) < DEFAULT_MIN_CHARS:
        return raw
    return raw[:KEEP_HEAD] + "…<base64×%d, 已省略>" % len(raw)


def redact_base64(value, *, min_chars: int = DEFAULT_MIN_CHARS):
    """递归脱敏 str/dict/list 中的超长 base64 串，返回同结构新对象。

    - str：替换所有长度 ≥ min_chars 的连续 base64 串（data:image URL 的
      mime 前缀不在匹配范围内，会原样保留）；
    - dict / list：递归处理每个元素；
    - 其他类型原样返回。
    """
    if min_chars != DEFAULT_MIN_CHARS:
        pattern = re.compile(r"[A-Za-z0-9+/\-_]{%d,}={0,2}" % min_chars)
        sub = lambda m: m.group(0)[:KEEP_HEAD] + "…<base64×%d, 已省略>" % len(m.group(0))
    else:
        pattern = _BASE64_RUN
        sub = _shorten

    if isinstance(value, str):
        return pattern.sub(sub, value)
    if isinstance(value, dict):
        return {k: redact_base64(v, min_chars=min_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_base64(v, min_chars=min_chars) for v in value]
    return value
