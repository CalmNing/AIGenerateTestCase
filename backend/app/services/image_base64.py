# -*- coding: utf-8 -*-
"""图片 base64 处理工具。

前置步骤里可能需要把图片变成 base64 变量：

- 从响应 JSON 里取到的是 base64 串或 data:image URL -> 规范化后存成变量；
- 上传本地图片 -> 解码 -> （可选压缩）-> 重新编码成 base64 变量。

输出格式有两种：

- ``raw``   纯 base64 串（不带 data: 前缀），适合放进请求体字段；
- ``data_url`` 完整的 ``data:image/png;base64,...``，适合整体引用。
"""
import base64
import binascii
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 输出格式
FORMAT_RAW = "raw"
FORMAT_DATA_URL = "data_url"

# 原图上限（8MB），与 document_prefetch 的 _IMAGE_MAX_BYTES 保持一致
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# 未压缩直传的上限（2MB），与 _IMAGE_RAW_MAX_BYTES 一致
MAX_RAW_BYTES = 2 * 1024 * 1024

# 压缩后的最长边
MAX_SIDE = 1568

# 压缩质量
JPEG_QUALITY = 82


def sniff_image_mime(data: bytes) -> Optional[str]:
    """按魔术字节判断图片类型，不是图片返回 None。"""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def split_data_url(value: str) -> tuple[Optional[str], str]:
    """拆 data:image/png;base64,XXXX -> ("image/png", "XXXX")。

    不是 data URL 时返回 (None, 原样)。
    """
    if not isinstance(value, str):
        return None, ""
    text = value.strip()
    if not text.lower().startswith("data:"):
        return None, text
    head, _sep, payload = text.partition(",")
    if not _sep:
        return None, text
    # head 形如 "data:image/png;base64"
    meta = head[len("data:"):]
    mime = meta.split(";", 1)[0].strip() or None
    return mime, payload.strip()


def decode_base64_image(value: str) -> tuple[Optional[bytes], Optional[str]]:
    """把 base64 串或 data:image URL 解成图片字节。

    返回 (字节, mime)。不是有效图片时返回 (None, None)。
    """
    mime_hint, payload = split_data_url(value or "")
    if not payload:
        return None, None
    # 兼容带空白/换行的 base64
    compact = "".join(payload.split())
    try:
        raw = base64.b64decode(compact, validate=False)
    except (binascii.Error, ValueError):
        return None, None
    if not raw:
        return None, None
    mime = sniff_image_mime(raw) or mime_hint
    return raw, mime


def encode_image(data: bytes, compress: bool = True) -> tuple[Optional[str], Optional[str]]:
    """把图片字节编码成 base64。

    compress=True 时用 Pillow 压缩并转 JPEG（体积小，适合请求体）；
    compress=False 时保持原始字节。

    返回 (base64, mime)。无法处理时返回 (None, None)。
    """
    if not data:
        return None, None

    if compress:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                if image.mode in ("RGBA", "LA", "P"):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (255, 255, 255))
                    background.paste(rgba, mask=rgba.split()[-1])
                    image = background
                else:
                    image = image.convert("RGB")
                image.thumbnail((MAX_SIDE, MAX_SIDE))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            return base64.b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"
        except Exception:  # noqa: BLE001 - Pillow 缺失或图片损坏时退回原样
            logger.warning("图片压缩失败，退回原始字节编码", exc_info=True)

    mime = sniff_image_mime(data)
    if not mime:
        return None, None
    if len(data) > MAX_RAW_BYTES:
        logger.warning("原图 %d 字节超过未压缩上限 %d", len(data), MAX_RAW_BYTES)
        return None, None
    return base64.b64encode(data).decode("ascii"), mime


def format_output(b64: str, mime: Optional[str], fmt: str) -> str:
    """按输出格式包装 base64：纯串 或 data:image/...;base64,...。"""
    if fmt == FORMAT_DATA_URL:
        return f"data:{mime or 'image/png'};base64,{b64}"
    return b64


def to_base64_variable(value: str, compress: bool = True, fmt: str = FORMAT_RAW) -> Optional[str]:
    """把一个 base64/data-URL 图片值转成可直接放进变量的字符串。

    - 输入是 data:image URL：先剥出纯 base64，按需压缩，再按格式包装；
    - 输入是纯 base64：同上；
    - 不是有效图片：返回 None。
    """
    raw, mime = decode_base64_image(value)
    if raw is None:
        return None
    b64, out_mime = encode_image(raw, compress=compress)
    if b64 is None:
        return None
    return format_output(b64, out_mime or mime, fmt)
