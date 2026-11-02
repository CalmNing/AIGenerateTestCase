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
        rows = conn.execute(
            "SELECT rowid, content FROM retrieval_fts ORDER BY rowid DESC LIMIT 10"
        ).fetchall()
        stats["recent_docs"] = [
            {"rowid": r[0], "content": (r[1] or "")[:80]} for r in rows
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


@router.post("/reindex")
def reindex(session: SessionDep, user: CurrentUser):
    """手动全量重建历史需求索引。"""
    from db.db import _rebuild_retrieval_index
    from db.db import engine as app_engine

    _rebuild_retrieval_index(app_engine)
    idx = _get_index()
    return Response(data={"indexed_count": idx.indexed_count()}, message="索引已重建")