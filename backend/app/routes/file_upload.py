"""文件上传代理路由。

浏览器直连业务系统的预签名地址（对象存储）会触发跨域，且预签名 URL 会过期，
因此上传链路整体收敛到后端：

    前端 -> POST /api/file/upload (multipart) -> 后端 -> 业务系统
                                              -> 业务系统 -> 预签名 URL (PUT)

后端负责：计算 MD5、申请预签名地址、必要时把文件 PUT 到对象存储、返回 fileId。
前端只与本平台后端通信，不再跨域。
"""
import hashlib
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlmodel import Session

from app.deps import CurrentUser, SessionDep
from app.permissions import Permission, get_user_permissions
from app.routes.proxy import build_param_map
from app.services.image_base64 import (
    FORMAT_RAW,
    MAX_IMAGE_BYTES,
    encode_image,
    format_output,
    sniff_image_mime,
)
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/file", tags=["file-upload"])

# 业务系统申请预签名地址的接口（相对环境参数里的 baseUrl）
UPLOAD_URL_API = "/builder-base/file/upload-url"

# 后端硬上限：兜底防止超大文件打爆内存。前端另有 maxSize(MB，默认 50) 的体验层校验。
MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# 申请预签名地址 / 上传对象存储的超时
_UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def _safe_json(response: httpx.Response) -> Optional[dict]:
    """尽力把响应体解析成 dict；非 JSON 时返回 None。"""
    try:
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_env_config(db: Session, environment_id: Optional[int], user) -> tuple[str, str]:
    """从环境参数里取 baseUrl 与 access-token。

    与 proxy.forward_request 保持一致：无 global_parameter:manage 权限时忽略
    传入的 environment_id，降级到默认环境。
    """
    if environment_id and Permission.GLOBAL_PARAMETER_MANAGE not in get_user_permissions(user):
        environment_id = None

    param_map = build_param_map(db, environment_id, [])
    base_url = str(param_map.get("baseUrl") or "").strip().rstrip("/")
    access_token = str(param_map.get("access-token") or "").strip()
    return base_url, access_token


def _extract_upload_info(payload: dict) -> dict:
    """从业务系统响应里取出预签名信息。"""
    if payload.get("code") != 200:
        message = payload.get("msg") or payload.get("message") or "业务系统返回异常"
        raise HTTPException(status_code=502, detail=f"获取上传地址失败: {message}")

    items = payload.get("data") or []
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise HTTPException(status_code=502, detail="获取上传地址失败: 响应缺少 data")

    return items[0]


@router.post("/upload", response_model=Response)
async def upload_file(
    session: SessionDep,
    user: CurrentUser,
    file: UploadFile = File(...),
    environment_id: Optional[int] = Form(None),
):
    """接收前端文件，由后端代理完成上传，返回 fileId。"""
    base_url, access_token = _resolve_env_config(session, environment_id, user)
    if not base_url:
        raise HTTPException(status_code=400, detail="当前环境未定义 baseUrl，请在全局参数管理中配置")
    if not access_token:
        raise HTTPException(status_code=400, detail="当前环境未定义 access-token，请在全局参数管理中配置")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_SIZE:
        limit_mb = MAX_UPLOAD_SIZE // 1024 // 1024
        raise HTTPException(status_code=413, detail=f"文件大小超过 {limit_mb}MB 限制")

    file_name = file.filename or "unnamed"
    file_size = len(content)
    file_md5 = hashlib.md5(content).hexdigest()
    content_type = file.content_type or "application/octet-stream"

    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
        # 1. 申请预签名地址（业务系统的秒传判定依赖 fileMd5）
        try:
            meta_response = await client.post(
                f"{base_url}{UPLOAD_URL_API}",
                json=[
                    {
                        "fileName": file_name,
                        "fileMd5": file_md5,
                        "fileSize": file_size,
                        "isPublicVisit": False,
                    }
                ],
                headers={"access-token": access_token},
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 502 对外
            logger.warning("申请上传地址失败: %s", exc)
            raise HTTPException(status_code=502, detail=f"获取上传地址失败: {exc}")

        payload = _safe_json(meta_response)
        if payload is None:
            raise HTTPException(
                status_code=502,
                detail=f"获取上传地址失败: HTTP {meta_response.status_code} 非 JSON 响应",
            )

        upload_info = _extract_upload_info(payload)

        # 2. 秒传命中：无需再 PUT 文件
        if not upload_info.get("fastUploadFinish"):
            upload_url = upload_info.get("uploadUrl")
            if not upload_url:
                raise HTTPException(status_code=502, detail="获取上传地址失败: 响应缺少 uploadUrl")

            try:
                put_response = await client.put(
                    upload_url,
                    content=content,
                    headers={"Content-Type": content_type},
                )
            except Exception as exc:  # noqa: BLE001 - 统一转成 502 对外
                logger.warning("上传到对象存储失败: %s", exc)
                raise HTTPException(status_code=502, detail=f"上传文件失败: {exc}")

            if put_response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"上传文件失败: 对象存储返回 HTTP {put_response.status_code}",
                )

    file_id = upload_info.get("fileId")
    if not file_id:
        raise HTTPException(status_code=502, detail="上传失败: 业务系统未返回 fileId")

    return Response(
        code=200,
        message="Success",
        data={
            "fileName": upload_info.get("fileName") or file_name,
            "fileId": file_id,
            "fileSize": file_size,
        },
    )


@router.post("/image-base64", response_model=Response)
async def image_to_base64(
    file: UploadFile = File(...),
    compress: bool = Form(True),
    format: str = Form(FORMAT_RAW),
):
    """把本地图片转成 base64 变量值。

    前置步骤里需要用本地图片当变量时走这里：图片不落业务系统，
    只转成 base64 字符串返回，前端存进前置步骤变量再引用。

    - compress=true：Pillow 压缩并转 JPEG，体积小，适合放进请求体
    - format=raw：纯 base64 串；format=data_url：完整 data:image/... URL
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="图片文件为空")
    if len(content) > MAX_IMAGE_BYTES:
        limit_mb = MAX_IMAGE_BYTES // 1024 // 1024
        raise HTTPException(status_code=413, detail=f"图片大小超过 {limit_mb}MB 限制")

    if not sniff_image_mime(content):
        raise HTTPException(status_code=400, detail="文件不是可识别的图片格式")

    b64, mime = encode_image(content, compress=compress)
    if b64 is None:
        raise HTTPException(status_code=400, detail="图片编码失败")

    value = format_output(b64, mime, format)
    return Response(
        code=200,
        message="Success",
        data={
            "fileName": file.filename or "image",
            "mime": mime,
            "base64": value,
            "fileSize": len(content),
            "compressed": bool(compress),
            "format": format,
        },
    )
