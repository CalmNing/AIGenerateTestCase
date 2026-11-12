"""需求管理 API：飞书文档接入、需求内容结构化解析（严格按标题等级）、变更历史。

挂载于 /requirement-docs（backend/app/main.py），鉴权复用 testcase 权限组：
读 = testcase:read；写（同步/编辑）= testcase:create/testcase:update。
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select, desc

from app.deps import SessionDep, CurrentUser
from config import config_manager
from db.models import RequirementDoc, RequirementRevision, RequirementTestcaseLink
from utils.base_response import Response
from app.services import requirement_doc as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requirement-docs", tags=["requirement-docs"])



# ===== Pydantic 请求/响应模型 =====

class DocCreateRequest(BaseModel):
    url: str = Field(..., description="飞书文档链接")
    title: str = Field(default="", description="需求名称，留空自动取文档标题")
    session_id: Optional[int] = Field(default=None, description="关联的测试用例会话ID")
    module_id: Optional[int] = Field(default=None, description="所属模块ID")


class DocUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="需求名称")
    session_id: Optional[int] = Field(default=None, description="关联的测试用例会话ID")


class DocSyncRequest(BaseModel):
    fetch_feishu: bool = Field(default=True, description="是否从飞书拉取最新正文；False 时按已有快照重新解析")


class DocContentUpdateRequest(BaseModel):
    content: str = Field(..., description="更新后的文档 Markdown 正文")


class DocParseRequest(BaseModel):
    content: str = Field(..., description="待解析的文档正文")


class TextRequirementRequest(BaseModel):
    title: str = Field(default="", description="需求名称，留空取内容首行")
    content: str = Field(..., description="需求正文（Markdown 或纯文本）")
    session_id: Optional[int] = Field(default=None, description="关联会话 ID")
    module_id: Optional[int] = Field(default=None, description="所属模块 ID")


def _require_user(current_user) -> str:
    """返回当前用户 ID（仅用于记录创建人，不再用于数据隔离）。"""
    return getattr(current_user, "user_id", None) or ""


def _get_doc(db, doc_id: int, user_id: str = "") -> RequirementDoc:
    """按 ID 取需求文档（全局共享，不再按 user_id 校验归属）。"""
    doc = db.get(RequirementDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="需求文档不存在")
    return doc


def _doc_payload(doc: RequirementDoc, extra: Optional[dict] = None) -> dict:
    data = {
        "id": doc.id,
        "title": doc.title,
        "url": doc.url,
        "feishu_token": doc.feishu_token,
        "content_snapshot": doc.content_snapshot,
        "structured": doc.structured if isinstance(doc.structured, list) else [],
        "session_id": doc.session_id,
        "version": doc.version,
        "module_id": doc.module_id,
        "source_type": doc.source_type or "feishu",
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
    if extra:
        data.update(extra)
    return data


# ===== 路由 =====

@router.get("", response_model=Response)
def list_requirement_docs(
    db: SessionDep,
    current_user: CurrentUser = None,
    source_type: Optional[str] = None,
    module_id: Optional[int] = None,
    session_id: Optional[int] = None,
):
    """需求列表（全局共享，不按用户隔离；含结构化数据）。

    可选过滤：source_type（feishu/generation/manual），module_id，session_id。
    generation 为生成自动保存的需求输入日志，manual 为手动录入文本需求。
    """
    stmt = select(RequirementDoc)
    if source_type:
        stmt = stmt.where(RequirementDoc.source_type == source_type)  # type: ignore[attr-defined]
    if module_id is not None:
        stmt = stmt.where(RequirementDoc.module_id == module_id)  # type: ignore[attr-defined]
    if session_id is not None:
        stmt = stmt.where(RequirementDoc.session_id == session_id)  # type: ignore[attr-defined]
    docs = db.exec(stmt.order_by(desc(RequirementDoc.updated_at))).all()
    return Response(data=[_doc_payload(d) for d in docs])


@router.post("", response_model=Response)
async def create_requirement_doc(req: DocCreateRequest, db: SessionDep, current_user: CurrentUser = None):
    """登记飞书需求文档：解析链接并立即同步一次正文与结构化数据。"""
    user_id = _require_user(current_user)
    token = svc.parse_feishu_token(req.url)
    if not token:
        raise HTTPException(status_code=400, detail="无效的飞书文档链接，仅支持 wiki/docx 链接")

    dup = db.exec(
        select(RequirementDoc).where(
            RequirementDoc.feishu_token == token,  # type: ignore[attr-defined]
        )
    ).first()
    if dup:
        raise HTTPException(status_code=400, detail="该飞书文档已登记，无需重复添加")

    doc = RequirementDoc(
        title=req.title or "未命名需求",
        url=req.url.strip(),
        feishu_token=token,
        session_id=req.session_id,
        module_id=req.module_id,
        user_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 登记后立即尝试首次同步（失败不回滚登记，前端可手动重试）
    try:
        await _sync_doc_content(db, doc, user_id, current_user, source="sync")
        db.refresh(doc)
    except Exception as e:
        logger.warning("首次同步飞书文档失败 doc=%s: %s", doc.id, e)
    return Response(data=_doc_payload(doc))


@router.post("/text", response_model=Response)
def create_text_requirement(req: TextRequirementRequest, db: SessionDep, current_user: CurrentUser = None):
    """手动录入文本需求（不经过飞书）：解析为结构化条目并进入检索索引。"""
    user_id = _require_user(current_user)
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="需求内容不能为空")

    doc = RequirementDoc(
        title=(req.title or "").strip() or content.split("\n", 1)[0].strip()[:60] or "未命名需求",
        url="",
        feishu_token="",
        content_snapshot=content,
        structured=[it.to_dict() for it in svc.parse_markdown_to_items(content)],
        session_id=req.session_id,
        version=1,
        module_id=req.module_id,
        source_type="manual",
        user_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        from app.services.requirement_doc import _index_requirement_doc
        _index_requirement_doc(doc)
    except Exception as e:
        logger.warning("手动录入需求索引失败(已忽略): %s", e)

    rev = RequirementRevision(
        doc_id=doc.id,
        version=1,
        source="manual",
        summary="手动录入需求",
        differences=[],
        content_snapshot=content,
        user_id=user_id,
    )
    db.add(rev)
    db.commit()
    return Response(data=_doc_payload(doc))


@router.get("/{doc_id}", response_model=Response)
def get_requirement_doc(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """需求文档详情（含正文快照与结构化数据）。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    return Response(data=_doc_payload(doc))


@router.put("/{doc_id}", response_model=Response)
def update_requirement_doc(doc_id: int, req: DocUpdateRequest, db: SessionDep, current_user: CurrentUser = None):
    """更新需求名称/关联会话（不触发版本变更）。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    if req.title is not None:
        doc.title = req.title.strip() or doc.title
    if req.session_id is not None:
        doc.session_id = req.session_id
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return Response(data=_doc_payload(doc))


@router.delete("/{doc_id}", response_model=Response)
def delete_requirement_doc(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """删除需求文档及其变更历史与遗留关联记录。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    for rev in db.exec(select(RequirementRevision).where(RequirementRevision.doc_id == doc_id)).all():
        db.delete(rev)
    for link in db.exec(select(RequirementTestcaseLink).where(RequirementTestcaseLink.doc_id == doc_id)).all():
        db.delete(link)
    db.delete(doc)
    db.commit()
    return Response(data={"deleted": True})


@router.post("/{doc_id}/sync", response_model=Response)
async def sync_requirement_doc(doc_id: int, req: DocSyncRequest, db: SessionDep, current_user: CurrentUser = None):
    """从飞书拉取最新内容（或按已有快照重新解析）并生成差异记录。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    await _sync_doc_content(db, doc, user_id, current_user, source="sync", fetch_feishu=req.fetch_feishu)
    db.refresh(doc)
    diffs = _latest_diffs(db, doc_id)
    return Response(data=_doc_payload(doc, {"differences": diffs}))


@router.post("/parse", response_model=Response)
def parse_content(req: DocParseRequest, current_user: CurrentUser = None):
    """把文档正文解析为结构化需求数据（预览，不入库）。"""
    _require_user(current_user)
    items = svc.parse_markdown_to_items(req.content)
    return Response(data=[it.to_dict() for it in items])


@router.put("/{doc_id}/content", response_model=Response)
async def update_content(doc_id: int, req: DocContentUpdateRequest, db: SessionDep, current_user: CurrentUser = None):
    """手动更新文档内容：重新解析 → 差异记录 → 版本 +1。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    old_items = doc.structured if isinstance(doc.structured, list) else []

    strict = svc.has_markdown_headings(req.content)
    outline_levels = None
    if not strict:
        model_cfg = _get_effective_model_config(db)
        if model_cfg:
            outline_levels = await svc.parse_outline_levels_with_llm(
                req.content,
                model_cfg["api_key"],
                model_cfg["api_base_url"],
                model_cfg["api_model"],
            )
    new_items = [it.to_dict() for it in svc.parse_markdown_to_items(
        req.content, outline_levels=outline_levels, disable_short_heading=strict)]
    new_items = svc.merge_structure(old_items, new_items)
    diffs = svc.compute_diff(old_items, new_items)

    doc.content_snapshot = req.content
    doc.structured = new_items
    doc.version = (doc.version or 0) + 1
    db.add(doc)
    db.commit()
    db.refresh(doc)

    rev = RequirementRevision(
        doc_id=doc.id,
        version=doc.version,
        source="manual",
        summary="手动编辑正文",
        differences=diffs,
        content_snapshot=req.content,
        user_id=user_id,
    )
    db.add(rev)
    db.commit()
    return Response(data=_doc_payload(doc, {"differences": diffs}))


@router.get("/{doc_id}/revisions", response_model=Response)
def list_revisions(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """变更历史列表（差异明细 + 受影响用例）。"""
    user_id = _require_user(current_user)
    _get_doc(db, doc_id, user_id)
    revs = db.exec(
        select(RequirementRevision)
        .where(RequirementRevision.doc_id == doc_id)  # type: ignore[attr-defined]
        .order_by(desc(RequirementRevision.version))
    ).all()
    return Response(data=[{
        "id": r.id,
        "version": r.version,
        "source": r.source,
        "summary": r.summary,
        "differences": r.differences if isinstance(r.differences, list) else [],
        "affected_case_ids": r.affected_case_ids if isinstance(r.affected_case_ids, list) else [],
        "content_snapshot": r.content_snapshot,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in revs])


# ===== 内部工具 =====

async def _sync_doc_content(db, doc: RequirementDoc, user_id: str, current_user,
                            source: str = "sync", fetch_feishu: bool = True) -> None:
    """拉取（或复用快照）→ 解析 → 稳定 key 合并 → 差异记录 → 版本 +1。"""
    if fetch_feishu:
        content = await _fetch_feishu_content(doc.url, current_user)
        if not content:
            raise HTTPException(
                status_code=400,
                detail="飞书文档读取失败：未能通过 MCP 获取文档正文。"
                       "请检查 MCP 服务器配置、飞书应用权限或 access token 是否有效后重试。",
            )
    else:
        content = doc.content_snapshot
    if not content:
        raise HTTPException(status_code=400, detail="文档内容为空，请先从飞书同步")

    old_items = doc.structured if isinstance(doc.structured, list) else []

    # blocks 重建成功时,标题级别已在 # 前缀里(飞书官方),禁用短标题启发式,
    # 避免 bullet 正文行被误抬为章节;此时也无需 LLM 大纲判定。
    strict = svc.has_markdown_headings(content)
    outline_levels = None
    if not strict:
        model_cfg = _get_effective_model_config(db)
        if model_cfg:
            outline_levels = await svc.parse_outline_levels_with_llm(
                content,
                model_cfg["api_key"],
                model_cfg["api_base_url"],
                model_cfg["api_model"],
            )
    new_items = [it.to_dict() for it in svc.parse_markdown_to_items(
        content, outline_levels=outline_levels, disable_short_heading=strict)]
    new_items = svc.merge_structure(old_items, new_items)
    diffs = svc.compute_diff(old_items, new_items)

    doc.content_snapshot = content
    doc.structured = new_items
    doc.version = (doc.version or 0) + 1
    if not doc.title or doc.title == "未命名需求":
        for it in new_items:
            if it.get("section"):
                doc.title = it["section"]
                break
    db.add(doc)
    db.commit()
    db.refresh(doc)

    rev = RequirementRevision(
        doc_id=doc.id,
        version=doc.version,
        source=source,
        summary=f"{'飞书同步' if fetch_feishu else '重新解析'}：新增 {sum(1 for d in diffs if d['type'] == 'added')}，"
                f"修改 {sum(1 for d in diffs if d['type'] == 'modified')}，"
                f"删除 {sum(1 for d in diffs if d['type'] == 'removed')}",
        differences=diffs,
        content_snapshot=content,
        user_id=user_id,
    )
    db.add(rev)
    db.commit()


def _latest_diffs(db, doc_id: int) -> list[dict]:
    rev = db.exec(
        select(RequirementRevision)
        .where(RequirementRevision.doc_id == doc_id)  # type: ignore[attr-defined]
        .order_by(desc(RequirementRevision.version))
    ).first()
    return (rev.differences if rev and isinstance(rev.differences, list) else [])


def _get_effective_model_config(session) -> "dict | None":
    """与生成链路同源的模型配置：系统配置（设置页）优先，回退 data/config.json。

    仅 api 模式且配置了 api_key 时返回 {api_key, api_base_url, api_model}；
    未配置或 ollama 模式返回 None（飞书图片解析/大纲判定随之跳过）。
    """
    from app.services.system_config import get_shared_system_config
    shared = get_shared_system_config(session)
    if shared and (shared.model_type or "api") == "api" and (shared.api_key or "").strip():
        return {
            "api_key": (shared.api_key or "").strip(),
            "api_base_url": (shared.api_base_url or "").strip(),
            "api_model": (shared.api_model or "").strip(),
        }
    if config_manager.get_model_type() == "api" and config_manager.get_api_key():
        return {
            "api_key": config_manager.get_api_key(),
            "api_base_url": config_manager.get_api_base_url(),
            "api_model": config_manager.get_api_model(),
        }
    return None


async def _fetch_feishu_content(url: str, current_user) -> "str | None":
    """通过用户已启用的 MCP 配置读取飞书文档正文（与生成链路共用实现）。

    current_user 必须是带 roles 的完整 UserInfo（mcp:manage 权限判定依赖角色）。
    """
    from app.services.mcp_loader import load_user_mcp_configs
    from utils.document_prefetch import fetch_feishu_document
    from app.deps import get_db
    with next(get_db()) as session:
        mcp_configs = load_user_mcp_configs(session, current_user)
        model_cfg = _get_effective_model_config(session)
    vision_config = (
        {
            "api_key": model_cfg["api_key"],
            "api_base_url": model_cfg["api_base_url"],
            "api_model": model_cfg["api_model"],
            "api_proxy_url": None,
        }
        if model_cfg
        else None
    )
    return await fetch_feishu_document(url, mcp_configs, vision_config=vision_config)