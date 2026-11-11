"""需求管理 API：飞书文档接入、结构化解析、变更历史与测试用例联动。

挂载于 /requirement-docs（backend/app/main.py），鉴权复用 testcase 权限组：
读 = testcase:read；写（同步/编辑/联动）= testcase:create/testcase:generate/testcase:update。
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select, desc

from app.deps import SessionDep, CurrentUser
from app.permissions import Permission
from db.models import RequirementDoc, RequirementRevision, RequirementTestcaseLink, RequirementPoint, TestCase, TestPoint, Session
from utils.base_response import Response
from app.services import requirement_doc as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requirement-docs", tags=["requirement-docs"])

# 联动修改用例的并发上限（避免长文档触发过大的并发 LLM 调用）
_APPLY_CONCURRENCY = 3


# ===== Pydantic 请求/响应模型 =====

class DocCreateRequest(BaseModel):
    url: str = Field(..., description="飞书文档链接")
    title: str = Field(default="", description="需求名称，留空自动取文档标题")
    session_id: Optional[int] = Field(default=None, description="关联的测试用例会话ID")


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


class LinkUpsertRequest(BaseModel):
    item_key: str = Field(..., description="结构化需求条目 key")
    testcase_id: int = Field(..., description="测试用例 ID")
    session_id: Optional[int] = Field(default=None, description="用例所在会话 ID")


class ApplyCasesRequest(BaseModel):
    case_ids: list[int] = Field(default_factory=list, description="要修改的测试用例 ID 列表")
    item_keys: list[str] = Field(default_factory=list, description="作为修改依据的需求条目 key，空=全部条目")
    apply: bool = Field(default=False, description="True=直接写回用例；False=仅生成修改建议")


def _require_user(current_user) -> str:
    user_id = getattr(current_user, "user_id", None) or ""
    if not user_id:
        raise HTTPException(status_code=401, detail="用户身份缺失")
    return user_id


def _get_doc(db, doc_id: int, user_id: str) -> RequirementDoc:
    doc = db.get(RequirementDoc, doc_id)
    if not doc or (doc.user_id or "") != user_id:
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


def _load_case(db, case_id: int):
    return db.get(TestCase, case_id)


async def _suggest_llm_if_available(diffs: list[dict], scored: list[dict]) -> None:
    """尽力而为：有可用模型配置时为受影响用例补充 LLM 建议，失败静默回退规则建议。"""
    try:
        from config import config_manager
        if config_manager.get_model_type() != "api":
            return
        api_key = config_manager.get_api_key()
        if not api_key:
            return
        blocks = []
        for item in scored:
            case = item.get("_case")
            if case is None:
                continue
            blocks.append({
                "testcase_id": item["testcase_id"],
                "block": svc.build_case_update_blocks(item.get("_items") or [], case),
            })
        suggestions = await svc.analyze_case_impact_with_llm(
            diffs, blocks,
            api_key, config_manager.get_api_base_url(), config_manager.get_api_model(),
        )
        by_id = {s.get("testcase_id"): s.get("suggestion") for s in suggestions if s.get("testcase_id")}
        for item in scored:
            llm_text = by_id.get(item["testcase_id"])
            if llm_text:
                item["suggestion"] = llm_text
                item["suggestion_source"] = "llm"
    except Exception as e:
        logger.info("LLM 影响分析跳过: %s", e)


# ===== 路由 =====

@router.get("", response_model=Response)
def list_requirement_docs(
    db: SessionDep,
    current_user: CurrentUser = None,
    source_type: Optional[str] = None,
    module_id: Optional[int] = None,
    session_id: Optional[int] = None,
):
    """当前用户的需求列表（含结构化数据）。

    可选过滤：source_type（feishu/generation/manual），module_id，session_id。
    generation 为生成自动保存的需求输入日志，manual 为手动录入文本需求。
    """
    user_id = _require_user(current_user)
    stmt = select(RequirementDoc).where(RequirementDoc.user_id == user_id)  # type: ignore[attr-defined]
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
            RequirementDoc.user_id == user_id,  # type: ignore[attr-defined]
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
        user_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 登记后立即尝试首次同步（失败不回滚登记，前端可手动重试）
    try:
        await _sync_doc_content(db, doc, user_id, source="sync")
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
    """删除需求文档及其变更历史与关联映射。"""
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
    await _sync_doc_content(db, doc, user_id, source="sync", fetch_feishu=req.fetch_feishu)
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
def update_content(doc_id: int, req: DocContentUpdateRequest, db: SessionDep, current_user: CurrentUser = None):
    """手动更新文档内容：重新解析 → 差异记录 → 版本 +1。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    old_items = doc.structured if isinstance(doc.structured, list) else []
    new_items = [it.to_dict() for it in svc.parse_markdown_to_items(req.content)]
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


@router.post("/{doc_id}/impact", response_model=Response)
async def impact_analysis(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """基于最近一次变更的差异，自动识别受影响的测试用例（关联映射 + 关键词匹配）。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    revs = db.exec(
        select(RequirementRevision)
        .where(RequirementRevision.doc_id == doc_id)  # type: ignore[attr-defined]
        .order_by(desc(RequirementRevision.version))
    ).all()
    if not revs:
        return Response(data={"affected": [], "has_diff": False})

    latest = revs[0]
    diffs = latest.differences if isinstance(latest.differences, list) else []
    if not diffs:
        return Response(data={"affected": [], "has_diff": False})

    # 用"上一版结构"与"当前结构"重建 diff 前后的条目集合
    prev_rev = revs[1] if len(revs) > 1 else None
    if prev_rev is not None:
        old_items = _structured_from_snapshot(prev_rev.content_snapshot)
    else:
        old_items = _structured_from_current_removed(diffs)
    new_items = doc.structured if isinstance(doc.structured, list) else []

    scored = svc.find_affected_testcases(db, doc.id, doc.session_id, old_items, new_items)
    # 附带用例对象供 LLM 建议构建上下文
    for item in scored:
        item["_case"] = _load_case(db, item["testcase_id"])
        if item["_case"] is not None:
            matched = item.get("matched_item_keys") or []
            item["_items"] = [it for it in new_items if it.get("key") in matched] or \
                [it for it in old_items if it.get("key") in matched]

    await _suggest_llm_if_available(diffs, scored)
    for item in scored:
        item.pop("_case", None)
        item.pop("_items", None)

    # 回写最近一次 revision 的受影响用例列表
    latest.affected_case_ids = [item["testcase_id"] for item in scored]
    db.add(latest)
    db.commit()
    return Response(data={"affected": scored, "has_diff": True, "version": latest.version})


def _structured_from_snapshot(snapshot: str) -> list[dict]:
    return [it.to_dict() for it in svc.parse_markdown_to_items(snapshot or "")]


def _structured_from_current_removed(diffs: list[dict]) -> list[dict]:
    """首个 revision：以差异中 removed/modified 的 before 内容还原旧条目。"""
    old_items: list[dict] = []
    for d in diffs:
        if d.get("type") in ("removed", "modified"):
            old_items.append({
                "key": d.get("key") or "",
                "section": d.get("section") or "",
                "content": d.get("before") or "",
                "title": "",
                "order_index": 0,
                "status": "active",
            })
    # 首版"新增"条目旧结构里不存在，不参与
    return old_items


@router.post("/{doc_id}/links", response_model=Response)
def upsert_link(doc_id: int, req: LinkUpsertRequest, db: SessionDep, current_user: CurrentUser = None):
    """登记/解除 需求条目 ↔ 测试用例 关联（testcase_id=0 表示解除）。"""
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    stmt = select(RequirementTestcaseLink).where(
        RequirementTestcaseLink.doc_id == doc.id,  # type: ignore[attr-defined]
        RequirementTestcaseLink.item_key == req.item_key,  # type: ignore[attr-defined]
    )
    if req.testcase_id != 0:
        stmt = stmt.where(RequirementTestcaseLink.testcase_id == req.testcase_id)  # type: ignore[attr-defined]
    existing = db.exec(stmt).first()
    if req.testcase_id == 0:
        if existing:
            db.delete(existing)
            db.commit()
        return Response(data={"linked": False})
    if existing:
        return Response(data={"linked": True, "id": existing.id})
    link = RequirementTestcaseLink(
        doc_id=doc.id,
        item_key=req.item_key,
        testcase_id=req.testcase_id,
        session_id=req.session_id or doc.session_id,
        user_id=user_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return Response(data={"linked": True, "id": link.id})


@router.get("/{doc_id}/links", response_model=Response)
def list_links(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """文档的条目-用例关联映射列表。"""
    user_id = _require_user(current_user)
    _get_doc(db, doc_id, user_id)
    links = db.exec(
        select(RequirementTestcaseLink).where(
            RequirementTestcaseLink.doc_id == doc_id)  # type: ignore[attr-defined]
    ).all()
    return Response(data=[{
        "id": l.id,
        "item_key": l.item_key,
        "testcase_id": l.testcase_id,
        "session_id": l.session_id,
    } for l in links])


@router.post("/{doc_id}/apply-cases", response_model=Response)
async def apply_cases(doc_id: int, req: ApplyCasesRequest, db: SessionDep, current_user: CurrentUser = None):
    """测试用例联动更新：按最新需求内容逐用例生成修改建议并可选直接应用。

    - use_llm=True：调用 LLM 按需求内容重写用例的预置条件/步骤/预期结果；
    - 仅建议模式：返回每条用例的建议文本与需求上下文。
    """
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    structured = doc.structured if isinstance(doc.structured, list) else []
    if not structured:
        raise HTTPException(status_code=400, detail="请先同步并解析需求文档内容")

    items = [it for it in structured if (not req.item_keys) or it.get("key") in set(req.item_keys)]
    if not items:
        raise HTTPException(status_code=400, detail="指定的需求条目不存在")

    from config import config_manager
    if config_manager.get_model_type() != "api" or not config_manager.get_api_key():
        raise HTTPException(status_code=400, detail="未配置 API 模型，无法执行用例联动更新")

    from utils.model_utils import _structured_output_robust
    from utils.models import FunctionalTestCase, FunctionalResponseFormat

    results: list[dict] = []
    case_ids = [c for c in (req.case_ids or []) if c]
    if not case_ids:
        raise HTTPException(status_code=400, detail="请选择要修改的测试用例")

    cases = [c for c in (_load_case(db, cid) for cid in case_ids) if c is not None]
    sem = asyncio.Semaphore(_APPLY_CONCURRENCY)

    async def run_one(case) -> None:
        async with sem:
            block = svc.build_case_update_blocks(items, case)
            messages = [
                {"role": "system", "content": (
                    "你是测试用例维护专家。请根据最新的需求内容，同步修改给定测试用例："
                    "保持用例结构与级别语义不变，仅调整与需求相关的预置条件、步骤和预期结果。"
                    "用例字段保持中文，输出 JSON。")},
                {"role": "user", "content": block +
                    "\n\n请输出修改后的完整测试用例（case_name/steps/preset_conditions/expected_results/case_level）。"},
            ]
            try:
                result = await _structured_output_robust(model, FunctionalResponseFormat, messages)
            except Exception as e:
                logger.warning("联动更新用例失败 testcase=%s: %s", case.id, e)
                results.append({"testcase_id": case.id, "status": "failed", "error": str(e)})
                return
            tc = None
            if isinstance(result, FunctionalResponseFormat):
                tc = result.response[0] if result.response else None
            elif isinstance(result, dict):
                arr = result.get("response") or []
                tc = arr[0] if arr else None
            if tc is None:
                results.append({"testcase_id": case.id, "status": "failed", "error": "模型返回为空"})
                return
            results.append({
                "testcase_id": case.id,
                "session_id": case.session_id,
                "case_name": case.case_name,
                "status": "updated" if req.apply else "suggested",
                "updated": {
                    "case_name": tc.case_name,
                    "steps": tc.steps,
                    "preset_conditions": tc.preset_conditions,
                    "expected_results": tc.expected_results,
                    "case_level": int(tc.case_level) if tc.case_level is not None else case.case_level,
                } if not isinstance(tc, dict) else {
                    "case_name": tc.get("case_name") or case.case_name,
                    "steps": tc.get("steps") or case.steps,
                    "preset_conditions": tc.get("preset_conditions") or case.preset_conditions,
                    "expected_results": tc.get("expected_results") or case.expected_results,
                    "case_level": tc.get("case_level") or case.case_level,
                },
            })

    from utils.llm_factory import build_chat_model
    model = build_chat_model(
        config_manager.get_api_key(),
        config_manager.get_api_base_url(),
        config_manager.get_api_model(),
        None,
        max_tokens=8192,
    )

    await asyncio.gather(*(run_one(c) for c in cases))

    if req.apply:
        for r in results:
            if r.get("status") != "updated":
                continue
            case = _load_case(db, r["testcase_id"])
            if case is None:
                continue
            upd = r.get("updated") or {}
            case.case_name = upd.get("case_name") or case.case_name
            case.steps = upd.get("steps") or case.steps
            case.preset_conditions = upd.get("preset_conditions") or case.preset_conditions
            case.expected_results = upd.get("expected_results") or case.expected_results
            if upd.get("case_level"):
                case.case_level = int(upd["case_level"])
            case.human_edited = True
            db.add(case)
        db.commit()

        # 记录一次联动回写历史（无正文差异，仅为追溯）
        rev = RequirementRevision(
            doc_id=doc.id,
            version=doc.version,
            source="apply_cases",
            summary=f"联动更新 {sum(1 for r in results if r.get('status') == 'updated')} 条测试用例",
            differences=[],
            content_snapshot=doc.content_snapshot,
            affected_case_ids=[r["testcase_id"] for r in results if r.get("status") == "updated"],
            user_id=user_id,
        )
        db.add(rev)
        db.commit()

    return Response(data={"results": results, "applied": bool(req.apply)})


# ===== P0:条目圈选 → 需求点 / 覆盖视图 / 条目直生用例 =====

class ToRequirementPointsRequest(BaseModel):
    session_id: int = Field(..., description="目标测试用例会话 ID")
    module_id: Optional[int] = Field(default=None, description="目标模块 ID（可选）")
    item_keys: list[str] = Field(..., description="要圈选的需求条目 key 列表")


class GenerateFromItemsRequest(BaseModel):
    session_id: int = Field(..., description="目标测试用例会话 ID")
    module_id: Optional[int] = Field(default=None, description="目标模块 ID（可选）")
    item_keys: list[str] = Field(..., description="要生成用例的需求条目 key 列表")


def _items_by_keys(doc: RequirementDoc, item_keys: list[str]) -> list[dict]:
    """按 key 取结构化条目，保持传入顺序；失效 key 直接报错提示重新同步。"""
    structured = doc.structured if isinstance(doc.structured, list) else []
    key_map = {it.get("key"): it for it in structured if isinstance(it, dict)}
    items = []
    missing = []
    for k in item_keys:
        it = key_map.get(k)
        if it is None:
            missing.append(k)
        else:
            items.append(it)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"以下需求条目已失效（文档已更新），请重新同步后再操作：{', '.join(missing[:5])}",
        )
    return items


@router.post("/{doc_id}/to-requirement-points", response_model=Response)
def to_requirement_points(doc_id: int, req: ToRequirementPointsRequest, db: SessionDep, current_user: CurrentUser = None):
    """把勾选的需求条目圈选为会话的需求点（RequirementPoint），幂等可重复提交。

    同一 doc_id + item_key 在同一会话已存在需求点时跳过；需求点带来源标记，
    「用例结构」页签与 4 阶段 pipeline 自然兼容。
    """
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    items = _items_by_keys(doc, req.item_keys)
    if not items:
        raise HTTPException(status_code=400, detail="请选择要圈选的需求条目")

    sess = db.get(Session, req.session_id)
    if not sess or (sess.user_id or "") != user_id:
        raise HTTPException(status_code=404, detail="目标会话不存在")

    existing_keys = {
        rp.item_key
        for rp in db.exec(
            select(RequirementPoint).where(
                RequirementPoint.doc_id == doc.id,  # type: ignore[attr-defined]
                RequirementPoint.item_key.in_(req.item_keys),  # type: ignore[attr-defined]
                RequirementPoint.session_id == req.session_id,  # type: ignore[attr-defined]
            )
        ).all()
        if rp.item_key
    }

    created = 0
    skipped = 0
    for it in items:
        if it.get("key") in existing_keys:
            skipped += 1
            continue
        rp = RequirementPoint(
            session_id=req.session_id,
            content=(it.get("content") or "").strip(),
            module_id=req.module_id,
            priority="medium",
            order_index=it.get("order_index") or 0,
            user_id=user_id,
            doc_id=doc.id,
            item_key=it.get("key"),
        )
        db.add(rp)
        created += 1
    db.commit()
    return Response(data={"created": created, "skipped": skipped, "total": len(items)})


@router.get("/{doc_id}/coverage", response_model=Response)
def coverage(doc_id: int, db: SessionDep, current_user: CurrentUser = None):
    """需求条目覆盖视图：三态（uncovered/pending/covered）+ 关联用例执行状态聚合。

    覆盖来源（并集）：RequirementTestcaseLink 显式关联；
    由条目圈选的需求点（doc_id+item_key）下经 TestPoint 关联的用例。
    实时计算不落库，用例删除后状态自然回落。
    """
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    structured = doc.structured if isinstance(doc.structured, list) else []

    # 1) 显式关联
    links = db.exec(
        select(RequirementTestcaseLink).where(RequirementTestcaseLink.doc_id == doc.id)  # type: ignore[attr-defined]
    ).all()
    linked_by_key: dict[str, set[int]] = {}
    for l in links:
        linked_by_key.setdefault(l.item_key, set()).add(l.testcase_id)

    # 2) 需求点来源链：doc 条目 → RequirementPoint → TestPoint → TestCase
    rps = db.exec(
        select(RequirementPoint).where(
            RequirementPoint.doc_id == doc.id,  # type: ignore[attr-defined]
            RequirementPoint.user_id == user_id,  # type: ignore[attr-defined]
        )
    ).all()
    rp_ids_by_key: dict[str, list[int]] = {}
    rp_sessions_by_key: dict[str, set[int]] = {}
    for rp in rps:
        if not rp.item_key:
            continue
        rp_ids_by_key.setdefault(rp.item_key, []).append(rp.id or 0)
        if rp.session_id:
            rp_sessions_by_key.setdefault(rp.item_key, set()).add(rp.session_id)

    tp_rows: list = []
    if rp_ids_by_key:
        all_rp_ids = [rid for ids in rp_ids_by_key.values() for rid in ids]
        tps = db.exec(
            select(TestPoint).where(TestPoint.requirement_point_id.in_(all_rp_ids))  # type: ignore[attr-defined]
        ).all()
        tp_rows = [(tp.id or 0, tp.requirement_point_id or 0) for tp in tps]

    tp_to_rp = {tp_id: rp_id for tp_id, rp_id in tp_rows}
    rp_id_to_key = {rid: k for k, ids in rp_ids_by_key.items() for rid in ids}
    tp_ids = list(tp_to_rp.keys())

    cases_from_tp: dict[int, list] = []
    if tp_ids:
        cases_from_tp = db.exec(
            select(TestCase).where(TestCase.test_point_id.in_(tp_ids))  # type: ignore[attr-defined]
        ).all()

    rp_case_ids: dict[str, set[int]] = {}
    for case in cases_from_tp:
        rp_id = case.test_point_id if case.test_point_id else 0
        key = rp_id_to_key.get(tp_to_rp.get(rp_id, 0))
        if key and case.id:
            rp_case_ids.setdefault(key, set()).add(case.id)

    # 3) 汇总每条目覆盖状态 + 执行状态聚合
    case_id_set = {cid for ids in linked_by_key.values() for cid in ids}
    case_id_set |= {cid for ids in rp_case_ids.values() for cid in ids}
    case_map = {}
    if case_id_set:
        for c in db.exec(select(TestCase).where(TestCase.id.in_(list(case_id_set)))).all():
            if c.id:
                case_map[c.id] = c

    def _status_agg(cids: set[int]) -> dict:
        agg = {"not_run": 0, "passed": 0, "failed": 0, "total": 0}
        for cid in cids:
            c = case_map.get(cid)
            if c is None:
                continue
            agg["total"] += 1
            status = (c.status or "NOT_RUN").upper()
            if status == "PASSED":
                agg["passed"] += 1
            elif status == "FAILED":
                agg["failed"] += 1
            else:
                agg["not_run"] += 1
        return agg

    items_out = []
    covered_count = 0
    for it in structured:
        key = it.get("key") or ""
        cids = set(linked_by_key.get(key, set())) | set(rp_case_ids.get(key, set()))
        has_rp = key in rp_ids_by_key
        if cids:
            coverage = "covered"
            covered_count += 1
        elif has_rp:
            coverage = "pending"
        else:
            coverage = "uncovered"
        items_out.append({
            "item_key": key,
            "section": it.get("section") or "",
            "title": it.get("title") or "",
            "coverage": coverage,
            "linked_case_ids": sorted(cids),
            "case_details": [
                {
                    "id": c.id,
                    "case_name": c.case_name,
                    "status": (c.status or "NOT_RUN").upper(),
                    "session_id": c.session_id,
                }
                for cid in sorted(cids)
                if (c := case_map.get(cid)) is not None
            ],
            "case_status": _status_agg(cids),
            "requirement_point_ids": sorted(rp_ids_by_key.get(key, [])),
        })

    return Response(data={
        "total": len(structured),
        "covered": covered_count,
        "pending": sum(1 for x in items_out if x["coverage"] == "pending"),
        "uncovered": sum(1 for x in items_out if x["coverage"] == "uncovered"),
        "items": items_out,
    })


@router.post("/{doc_id}/generate-from-items", response_model=Response)
async def generate_from_items(doc_id: int, req: GenerateFromItemsRequest, db: SessionDep, current_user: CurrentUser = None):
    """按勾选条目直接生成测试用例（单次 agent 路径），完成后自动建立条目↔用例关联。

    需求文本由勾选条目按章节拼接；每个生成的用例写入 RequirementTestcaseLink，
    后续影响分析走精确关联。
    """
    user_id = _require_user(current_user)
    doc = _get_doc(db, doc_id, user_id)
    items = _items_by_keys(doc, req.item_keys)
    if not items:
        raise HTTPException(status_code=400, detail="请选择要生成用例的需求条目")

    from config import config_manager
    if config_manager.get_model_type() != "api" or not config_manager.get_api_key():
        raise HTTPException(status_code=400, detail="未配置 API 模型，无法生成测试用例")

    # 条目 → 需求文本（按章节分组）
    sections: dict[str, list[str]] = {}
    for it in items:
        section = (it.get("section") or "需求").strip()
        sections.setdefault(section, []).append((it.get("content") or "").strip())
    requirement_text = "\n\n".join(
        f"【{sec}】\n" + "\n".join(bodies) for sec, bodies in sections.items()
    )

    from utils.model_utils import generate_testcases
    warnings: list[str] = []
    db_cases, _effective = await generate_testcases(
        session_id=req.session_id,
        module_id=req.module_id or 0,
        requirement=requirement_text,
        model_type="api",
        api_key=config_manager.get_api_key(),
        api_base_url=config_manager.get_api_base_url(),
        api_proxy_url=None,
        api_model=config_manager.get_api_model(),
        db_session=db,
        user_id=user_id,
        warnings_out=warnings,
    )

    # 自动建立条目↔用例关联（全文关键词匹配条目内容与用例文本）
    item_texts = {it.get("key") or "": svc.extract_keywords(
        (it.get("section") or "") + "\n" + (it.get("content") or "")) for it in items}
    linked = 0
    for case in db_cases:
        case_words = svc.extract_keywords(svc._case_text(case))
        best_key = ""
        best_score = 0
        for key, words in item_texts.items():
            score = svc._overlap_score(words, case_words)
            if score > best_score:
                best_score = score
                best_key = key
        if best_key and best_score > 0:
            exists = db.exec(
                select(RequirementTestcaseLink).where(
                    RequirementTestcaseLink.doc_id == doc.id,  # type: ignore[attr-defined]
                    RequirementTestcaseLink.item_key == best_key,  # type: ignore[attr-defined]
                    RequirementTestcaseLink.testcase_id == case.id,  # type: ignore[attr-defined]
                )
            ).first()
            if not exists:
                db.add(RequirementTestcaseLink(
                    doc_id=doc.id,
                    item_key=best_key,
                    testcase_id=case.id or 0,
                    session_id=req.session_id,
                    user_id=user_id,
                ))
                linked += 1
    db.commit()

    return Response(data={
        "generated": len(db_cases),
        "linked": linked,
        "case_ids": [c.id for c in db_cases],
        "case_names": [c.case_name for c in db_cases],
        "warnings": warnings,
    })



# ===== 内部工具 =====

async def _sync_doc_content(db, doc: RequirementDoc, user_id: str,
                            source: str = "sync", fetch_feishu: bool = True) -> None:
    """拉取（或复用快照）→ 解析 → 稳定 key 合并 → 差异记录 → 版本 +1。"""
    if fetch_feishu:
        content = await _fetch_feishu_content(doc.url, user_id)
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
    new_items = [it.to_dict() for it in svc.parse_markdown_to_items(content)]
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


async def _fetch_feishu_content(url: str, user_id: str) -> "str | None":
    """通过用户已启用的 MCP 配置读取飞书文档正文（与生成链路共用实现）。"""
    from app.services.mcp_loader import load_user_mcp_configs
    from utils.document_prefetch import fetch_feishu_document
    from app.deps import get_db
    from app.auth import UserInfo
    with next(get_db()) as session:
        mcp_configs = load_user_mcp_configs(session, UserInfo(user_id=user_id, username=""))
    if not mcp_configs:
        return None
    from config import config_manager
    if config_manager.get_model_type() == "api" and config_manager.get_api_key():
        vision_config = {
            "api_key": config_manager.get_api_key(),
            "api_base_url": config_manager.get_api_base_url(),
            "api_model": config_manager.get_api_model(),
            "api_proxy_url": None,
        }
    else:
        vision_config = None
    return await fetch_feishu_document(url, mcp_configs, vision_config=vision_config)