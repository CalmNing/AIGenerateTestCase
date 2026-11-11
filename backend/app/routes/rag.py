"""RAG 检索层查看与管理 API（调试/观测用）。

提供：
- GET  /rag/stats      索引统计（文档数、同义词数、路径）
- POST /rag/search     召回试查（与生成链路同一套逻辑）
- GET  /rag/terms      同义词词典
- PUT  /rag/terms      更新同义词
- POST /rag/reindex    手动全量重建索引

权限：读走 SKILLS_READ（检索是生成质量的可视化），写走 SKILLS_MANAGE。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import SessionDep, CurrentUser
from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


def _get_index():
    from utils.retrieval import get_index

    idx = get_index()
    if idx is None:
        raise HTTPException(status_code=503, detail="检索索引不可用（FTS5 未启用或初始化失败）")
    return idx


@router.get("/stats")
def get_rag_stats(session: SessionDep, user: CurrentUser):
    """索引统计：文档数、同义词数、索引路径、最近文档。"""
    idx = _get_index()
    stats = {
        "indexed_count": idx.indexed_count(),
        "db_path": idx.db_path,
        "terms": idx.get_terms(),
    }
    try:
        conn = idx._connect()
        # 按 created_at 优先（更新的文档排在最前，upsert 也能看见变化），
        # 无时间戳时按 rowid 兜底；标注来源类型便于区分历史需求/示例
        rows = conn.execute(
            """
            SELECT f.rowid, f.content, m.created_at,
                   CASE WHEN f.rowid >= 10000000 THEN 'example' ELSE 'requirement' END AS kind
            FROM retrieval_fts f
            LEFT JOIN retrieval_meta m ON m.rowid = f.rowid
            ORDER BY (m.created_at IS NULL), m.created_at DESC, f.rowid DESC
            LIMIT 10
            """
        ).fetchall()
        stats["recent_docs"] = [
            {
                "rowid": r[0],
                "content": (r[1] or "")[:80],
                "created_at": r[2],
                "kind": r[3],
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取最近文档失败: %s", exc)
        stats["recent_docs"] = []
    return Response(data=stats)


class SearchPayload(BaseModel):
    query: str
    module_id: int | None = None
    session_id: int | None = None
    top_k: int = 8


@router.post("/search")
def search_rag(payload: SearchPayload, session: SessionDep, user: CurrentUser):
    """召回试查：与生成链路完全同一套逻辑，用于调参/验证。"""
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    idx = _get_index()
    hits = idx.search(
        payload.query,
        module_id=payload.module_id,
        user_id=None,
        session_id=payload.session_id,
        top_k=max(1, min(20, payload.top_k)),
    )
    return Response(data={
        "query": payload.query,
        "count": len(hits),
        "hits": [
            {"rowid": h.row_id, "score": h.score, "content": h.content[:200]}
            for h in hits
        ],
    })


class TermsPayload(BaseModel):
    terms: dict[str, list[str]]


@router.get("/terms")
def get_terms(session: SessionDep, user: CurrentUser):
    idx = _get_index()
    return Response(data=idx.get_terms())


@router.put("/terms")
def update_terms(payload: TermsPayload, session: SessionDep, user: CurrentUser):
    """更新同义词词典（整表替换）。"""
    idx = _get_index()
    idx.set_terms(payload.terms)
    return Response(data=idx.get_terms(), message="同义词已更新")


@router.get("/few-shot")
def get_few_shot_pool(
    session: SessionDep,
    user: CurrentUser,
    query: str = "",
    module_id: int | None = None,
):
    """few-shot 池查看：当前可被生成器引用的正样本与驳回规避清单。"""
    from app.services.human_feedback import (
        format_examples_for_prompt,
        format_rejection_for_prompt,
        get_positive_examples,
        get_rejection_guidance,
        get_relevant_positive_examples,
        POSITIVE_FALLBACK_THRESHOLD,
    )

    total = 0
    try:
        from db.models import TestCase
        from sqlmodel import select, or_

        total = len(db.exec(
            select(TestCase.id).where(
                or_(
                    TestCase.status == "PASSED",
                    TestCase.validation_status == "passed",
                    TestCase.human_edited.is_(True),
                )
            )
        ).all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("统计正样本总数失败: %s", exc)

    if query.strip():
        examples = get_relevant_positive_examples(
            session, query=query, module_id=module_id, limit=5,
        )
        mode = "semantic"
    else:
        examples = get_positive_examples(
            session, module_id=module_id, limit=5,
        )
        mode = "recent"
    guidance = get_rejection_guidance(session, module_id=module_id)

    return Response(data={
        "pool_size": total,
        "threshold": POSITIVE_FALLBACK_THRESHOLD,
        "mode": mode,
        "examples": examples,
        "examples_prompt": format_examples_for_prompt(examples),
        "rejection_guidance": format_rejection_for_prompt(guidance),
    })


@router.post("/reindex")
def reindex(session: SessionDep, user: CurrentUser):
    """手动全量重建历史需求索引。"""
    from db.db import _rebuild_retrieval_index
    from db.db import engine as app_engine

    _rebuild_retrieval_index(app_engine)
    idx = _get_index()
    return Response(data={"indexed_count": idx.indexed_count()}, message="索引已重建")