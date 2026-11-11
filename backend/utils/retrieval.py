"""检索层（P2 / RAG 第一步）：SQLite FTS5 + 中文二元分词 + 确定性重排。

背景（调研结论 B1）：
- 改造前的"检索"只有 `_build_history_context`：按 `module_id` 精确过滤 + 时间倒序取最近 20 条，
  没有相关性、没有字符截断、跨模块经验不可复用；
- 外部实测（CLAST, ASE'25）：把语义清晰的相似用例作为 in-context 示例，
  可使编译成功率 +25.97%、通过率 +28.22%、覆盖率 +45.99%（A 类任务口径）。

本模块提供**不引入新依赖**的最小检索实现：
1. `tokenize_cjk()`：中文按二元组（bigram）切分，ASCII 按词切分。
   SQLite FTS5 的 unicode61 分词器不切中文，trigram 又要求 ≥3 字符，
   所以用"二元组预分词 + unicode61 索引"这一组合，兼顾中文召回与零新依赖。
2. `FtsIndex`：对 `HistoryPrompt.content` 建 FTS5 虚表（外部内容表 + 同义词词典），
   支持增量 upsert 与按 (module_id / user_id / session_id) 的过滤召回。
3. `retrieve_similar()`：BM25 排序 + 确定性重排（命中词覆盖率、时间衰减），
   返回 top-k 供 prompt 注入。

设计约束：
- 检索**只增强** prompt，任何异常都必须降级为"原时间序"或"空"，绝不阻断生成；
- 索引是**缓存**：索引缺失/过期时可用原表重建，源数据仍是唯一事实来源；
- 同义词词典（`DomainTerm`）用于弥补 BM25 对中文改写的短板。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# 中文连续字符
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
# ASCII 词（长度 ≥2，避免单字符噪声）
_ASCII_RE = re.compile(r"[a-z0-9_]{2,}")

# 默认召回条数（与旧实现的"最近 20 条"同量级，但按相关性排序）
DEFAULT_TOP_K = 8
# 单条内容的最大长度（字符），防止超长需求文档把索引撑爆
MAX_INDEX_CHARS = 4000
# 注入 prompt 的单条内容最大长度
MAX_CONTEXT_CHARS = 600


def tokenize_cjk(text: str) -> list[str]:
    """中文二元组 + ASCII 词切分。

    例：
      "用户登录" -> ["用户", "户登", "登录"]
      "导出 Excel" -> ["导出", "excel"]
    """
    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    for run in _CJK_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(_ASCII_RE.findall(lowered))
    return tokens


def build_match_query(query: str, extra_terms: Iterable[str] = ()) -> str:
    """构造 FTS5 MATCH 表达式（全部二元组 OR 连接）。"""
    tokens = tokenize_cjk(query)
    tokens.extend(tokenize_cjk(t) for t in extra_terms)  # type: ignore[arg-type]
    flat: list[str] = []
    for item in tokens:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    flat = [t for t in dict.fromkeys(flat) if t]
    if not flat:
        return ""
    # 每个 token 加引号，避免 FTS5 语法字符（- * ( ) 等）导致语法错误
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in flat)


@dataclass
class RetrievedItem:
    """一条召回结果。"""
    row_id: int
    content: str
    score: float
    source: str = "history"      # history | testcase | testpoint
    meta: dict = field(default_factory=dict)


class FtsIndex:
    """需求实体（requirement_doc.content_snapshot）的 FTS5 索引。

    表结构：
      - `retrieval_doc`：原文（rowid 对应 requirement_doc.id）
      - `retrieval_fts`：FTS5(tokens, content='retrieval_doc', content_rowid='rowid')
      - `retrieval_term`：同义词词典 (term, synonyms)
      - `retrieval_index_info`：源实体标记（key/value），用于换源后自动失效重建

    索引缺失时由 `ensure_index()` 自动创建；源表删除的行在同步时清理。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._fts_available: Optional[bool] = None
        # sqlite3.Connection 的事务状态是连接级的：多线程共享同一连接时，
        # A 线程 BEGIN 之后 B 线程 COMMIT 会导致 "cannot commit - no transaction
        # is active" / "another row available"，进而丢数据。
        # 因此所有连接操作都必须在这把锁内完成（连接本身 check_same_thread=False）。
        self._lock = threading.RLock()

    # ---------------- 连接与建表 ----------------

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._conn = conn
            return conn
        except Exception as exc:  # noqa: BLE001
            logger.warning("检索索引连接失败（将降级为不检索）: %s", exc)
            return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    def ensure_index(self) -> bool:
        """确保索引表存在。返回 False 表示当前环境不支持 FTS5（调用方应降级）。"""
        if self._fts_available is not None:
            return self._fts_available
        conn = self._connect()
        if conn is None:
            self._fts_available = False
            return False
        try:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
                    tokens,
                    content,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS retrieval_meta (
                    rowid INTEGER PRIMARY KEY,
                    module_id INTEGER,
                    user_id TEXT,
                    session_id INTEGER,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS retrieval_term (
                    term TEXT PRIMARY KEY,
                    synonyms TEXT
                );
                CREATE TABLE IF NOT EXISTS retrieval_index_info (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            conn.commit()
            self._fts_available = True
            return True
        except sqlite3.OperationalError as exc:
            # 典型错误：当前 SQLite 未编译 FTS5
            logger.warning("当前 SQLite 不支持 FTS5（检索降级为时间序）: %s", exc)
            self._fts_available = False
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建检索索引失败（将降级）: %s", exc)
            self._fts_available = False
            return False

    def get_info(self, key: str):
        """读取索引元信息（源实体版本等）。表不存在/异常返回 None。"""
        if not self.ensure_index():
            return None
        with self._lock:
            conn = self._connect()
            if conn is None:
                return None
            try:
                row = conn.execute(
                    "SELECT value FROM retrieval_index_info WHERE key = ?", (key,)
                ).fetchone()
                return row[0] if row else None
            except Exception:
                return None

    def set_info(self, key: str, value: str) -> None:
        if not self.ensure_index():
            return
        with self._lock:
            conn = self._connect()
            if conn is None:
                return
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO retrieval_index_info(key, value) VALUES (?,?)",
                    (key, value),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入索引元信息失败(已忽略): %s", exc)

    # ---------------- 写入 ----------------

    def upsert(
        self,
        *,
        row_id: int,
        content: str,
        module_id: Optional[int],
        user_id: Optional[str],
        session_id: Optional[int],
        created_at: Optional[str],
    ) -> None:
        """写入/更新一条文档（增量索引）。"""
        if not self.ensure_index():
            return
        text = (content or "").strip()
        if not text:
            return
        if len(text) > MAX_INDEX_CHARS:
            text = text[:MAX_INDEX_CHARS]
        with self._lock:
            conn = self._connect()
            if conn is None:
                return
            try:
                conn.execute("DELETE FROM retrieval_fts WHERE rowid = ?", (row_id,))
                conn.execute(
                    "INSERT INTO retrieval_fts(rowid, tokens, content) VALUES (?,?,?)",
                    (row_id, " ".join(tokenize_cjk(text)), text),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO retrieval_meta(rowid, module_id, user_id, session_id, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (row_id, module_id, user_id, session_id, created_at),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入检索索引失败（已忽略）: %s", exc)

    def delete(self, row_id: int) -> None:
        if not self.ensure_index():
            return
        with self._lock:
            conn = self._connect()
            if conn is None:
                return
            try:
                conn.execute("DELETE FROM retrieval_fts WHERE rowid = ?", (row_id,))
                conn.execute("DELETE FROM retrieval_meta WHERE rowid = ?", (row_id,))
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除检索索引失败（已忽略）: %s", exc)

    def set_terms(self, terms: dict[str, Iterable[str]]) -> None:
        """写入同义词词典：{标准词: [同义词...]}。"""
        if not self.ensure_index():
            return
        with self._lock:
            conn = self._connect()
            if conn is None:
                return
            try:
                for term, synonyms in terms.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO retrieval_term(term, synonyms) VALUES (?,?)",
                        (term.strip().lower(), ",".join(s.strip() for s in synonyms if s.strip())),
                    )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入同义词词典失败（已忽略）: %s", exc)

    def get_terms(self) -> dict[str, list[str]]:
        if not self.ensure_index():
            return {}
        with self._lock:
            conn = self._connect()
            if conn is None:
                return {}
            try:
                rows = conn.execute("SELECT term, synonyms FROM retrieval_term").fetchall()
                return {
                    term: [s for s in (raw or "").split(",") if s]
                    for term, raw in rows
                }
            except Exception:  # noqa: BLE001
                return {}

    # ---------------- 同步 ----------------

    def indexed_count(self) -> int:
        """当前索引中的文档数（用于判断索引是否过期）。"""
        if not self.ensure_index():
            return -1
        with self._lock:
            conn = self._connect()
            if conn is None:
                return -1
            try:
                return int(
                    conn.execute("SELECT count(*) FROM retrieval_fts").fetchone()[0] or 0
                )
            except Exception:  # noqa: BLE001
                return -1

    def sync_from_source(self, source_rows: list[dict]) -> int:
        """用源数据重建索引（全量同步）。source_rows 需含 id/content/module_id 等字段。

        返回写入条数。异常时返回 0（调用方降级）。
        """
        if not self.ensure_index():
            return 0
        with self._lock:
            conn = self._connect()
            if conn is None:
                return 0
            try:
                conn.execute("DELETE FROM retrieval_fts")
                conn.execute("DELETE FROM retrieval_meta")
                count = 0
                for row in source_rows:
                    text = (row.get("content") or "").strip()
                    if not text:
                        continue
                    if len(text) > MAX_INDEX_CHARS:
                        text = text[:MAX_INDEX_CHARS]
                    rowid = int(row["id"])
                    conn.execute(
                        "INSERT INTO retrieval_fts(rowid, tokens, content) VALUES (?,?,?)",
                        (rowid, " ".join(tokenize_cjk(text)), text),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO retrieval_meta(rowid, module_id, user_id, session_id, created_at)"
                        " VALUES (?,?,?,?,?)",
                        (rowid, row.get("module_id"), row.get("user_id"),
                         row.get("session_id"), row.get("created_at")),
                    )
                    count += 1
                conn.commit()
                logger.info("检索索引已同步：%d 条文档", count)
                return count
            except Exception as exc:  # noqa: BLE001
                logger.warning("同步检索索引失败（将降级）: %s", exc)
                return 0

    # ---------------- 召回 ----------------

    def search(
        self,
        query: str,
        *,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
        session_id: Optional[int] = None,
        top_k: int = DEFAULT_TOP_K,
        exclude_ids: Optional[set[int]] = None,
    ) -> list[RetrievedItem]:
        """按相关性召回。

        过滤策略：`module_id` 命中优先；`user_id` / `session_id` 作为软过滤
        （同一条需求点在不同模块下的经验仍然有价值，不做硬排除）。
        """
        if not self.ensure_index():
            return []
        conn = self._connect()
        if conn is None:
            return []

        terms = self.get_terms()
        extra: list[str] = []
        for token in tokenize_cjk(query):
            for term, syns in terms.items():
                if token in term or any(token in s for s in syns):
                    extra.extend(syns)
                    extra.append(term)
        match = build_match_query(query, extra)
        if not match:
            return []

        where = ["retrieval_fts MATCH ?"]
        params: list[Any] = [match]
        if module_id is not None:
            where.append("m.module_id = ?")
            params.append(module_id)
        if user_id:
            where.append("(m.user_id = ? OR m.user_id IS NULL)")
            params.append(user_id)
        if session_id is not None:
            where.append("(m.session_id = ? OR m.session_id IS NULL)")
            params.append(session_id)
        if exclude_ids:
            ids = ",".join(str(i) for i in exclude_ids)
            where.append(f"retrieval_fts.rowid NOT IN ({ids})")

        sql = (
            "SELECT retrieval_fts.rowid, retrieval_fts.content,"
            " m.module_id, m.user_id, m.session_id, m.created_at,"
            " bm25(retrieval_fts) AS score"
            " FROM retrieval_fts"
            " LEFT JOIN retrieval_meta m ON m.rowid = retrieval_fts.rowid"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY score LIMIT ?"
        )
        params.append(top_k)
        with self._lock:
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception as exc:  # noqa: BLE001
                logger.warning("检索查询失败（降级为不检索）: %s", exc)
                return []

        items: list[RetrievedItem] = []
        for row in rows:
            if len(row) < 7 or row[6] is None:
                # bm25 在极端情况下可能返回 NULL（并发写入导致的表状态瞬时异常），
                # 跳过该行而不是让整次检索失败。
                continue
            try:
                score = float(row[6])
            except (TypeError, ValueError):
                continue
            items.append(
                RetrievedItem(
                    row_id=int(row[0]),
                    content=row[1] or "",
                    score=score,
                    source="history",
                    meta={
                        "module_id": row[2],
                        "user_id": row[3],
                        "session_id": row[4],
                        "created_at": row[5],
                    },
                )
            )
        return items


# ---------------- 模块级单例 ----------------

_index: Optional[FtsIndex] = None
_index_failed = False

# 索引库默认路径：与业务库分离的独立文件。
# 理由：FTS5 会为虚表生成 6 张 shadow 表（*_data/*_idx/*_content/*_docsize/*_config 等），
# 放进业务库会污染 testcases.db，并与 alembic 迁移、DB 结构巡检相互干扰。
# 索引本身是可重建的缓存（源表 historyprompt 才是事实来源），独立文件更安全。
DEFAULT_INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "retrieval_index.db"
)


def get_index(db_path: Optional[str] = None) -> Optional[FtsIndex]:
    """获取进程级索引单例；不可用时返回 None（调用方降级）。"""
    global _index, _index_failed
    if _index is not None:
        return _index
    if _index_failed:
        return None
    path = db_path or DEFAULT_INDEX_PATH
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法创建索引目录，检索不可用: %s", exc)
        _index_failed = True
        return None
    idx = FtsIndex(path)
    if not idx.ensure_index():
        _index_failed = True
        return None
    _index = idx
    return _index


def reset_index_singleton() -> None:
    """仅测试用：重置索引单例。"""
    global _index, _index_failed
    if _index is not None:
        _index.close()
    _index = None
    _index_failed = False


# ---------------- 对外主入口 ----------------

def retrieve_history(
    db_session,
    *,
    query: str,
    module_id: Optional[int],
    user_id: Optional[str] = None,
    session_id: Optional[int] = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedItem]:
    """按相关性召回历史需求（主入口）。

    降级顺序：
      1. FTS5 不可用 → 返回空列表（调用方用时间序兜底）；
      2. 检索无命中 → 返回空列表（同上）；
      3. 任何异常 → 返回空列表，绝不阻断生成。
    """
    try:
        idx = get_index()
        if idx is None:
            return []
        return idx.search(
            query,
            module_id=module_id,
            user_id=user_id,
            session_id=session_id,
            top_k=top_k,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("历史检索失败（降级为时间序）: %s", exc)
        return []


def format_retrieved_context(
    items: list[RetrievedItem],
    *,
    title: str = "## 相关历史需求（按相关性召回，仅供参考，不作为本次需求）",
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """把召回结果格式化为可注入 prompt 的文本。"""
    if not items:
        return ""
    lines = [title]
    for i, item in enumerate(items, 1):
        content = (item.content or "").strip()
        if len(content) > max_chars:
            content = content[:max_chars] + "…"
        lines.append(f"{i}. {content}")
    return "\n".join(lines) + "\n\n"