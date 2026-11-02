"""P2 检索层：并发安全与索引隔离回归测试。

这两点是自检时发现的真实缺陷，锁进测试以防回归：
1. 共享 sqlite 连接在多线程并发写时事务交错 → "cannot commit - no transaction is active"
   → 索引丢数据（实测 400 条只写入 283 条）。
2. 索引最初被建在业务库 testcases.db 里，FTS5 会生成 6 张 shadow 表污染业务库。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from utils import retrieval as retrieval_mod
from utils.retrieval import DEFAULT_INDEX_PATH, FtsIndex


@pytest.fixture()
def idx(tmp_path):
    index = FtsIndex(str(tmp_path / "idx.db"))
    assert index.ensure_index() is True
    yield index
    index.close()


def test_concurrent_upsert_loses_no_rows(idx):
    """并发写入不得丢数据（回归：修复前 400 条只写入 283 条）。"""
    errors: list[str] = []
    threads_count = 8
    per_thread = 40

    def worker(n: int):
        try:
            for i in range(per_thread):
                idx.upsert(
                    row_id=n * 1000 + i,
                    content=f"并发测试内容 {n} {i}",
                    module_id=1,
                    user_id="u1",
                    session_id=1,
                    created_at=None,
                )
                idx.search("并发", module_id=1, top_k=3)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写不应抛错，实际：{errors[:3]}"
    conn = idx._connect()
    count = conn.execute("SELECT count(*) FROM retrieval_fts").fetchone()[0]
    assert count == threads_count * per_thread


def test_concurrent_search_and_upsert_is_safe(idx):
    """读写并发不应抛错（bm25 在表状态瞬时异常时可能返回 NULL/短行）。"""
    errors: list[str] = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                idx.search("测试", module_id=1, top_k=5)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    def writer(n: int):
        try:
            for i in range(30):
                idx.upsert(
                    row_id=n * 1000 + i,
                    content=f"读写并发 {n} {i}",
                    module_id=1,
                    user_id="u1",
                    session_id=1,
                    created_at=None,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    readers = [threading.Thread(target=reader) for _ in range(3)]
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert not errors, f"读写并发不应抛错，实际：{errors[:3]}"


def test_index_lives_in_separate_file_from_business_db():
    """索引必须在独立文件，不能污染业务库 testcases.db。

    FTS5 虚表会生成 6 张 shadow 表（*_data/*_idx/*_content/*_docsize/*_config 等）。
    """
    assert "testcases.db" not in DEFAULT_INDEX_PATH
    assert DEFAULT_INDEX_PATH.endswith("retrieval_index.db")

    try:
        from db.db import DB_PATH
    except Exception:  # noqa: BLE001
        pytest.skip("业务库路径不可用")
    assert os.path.dirname(os.path.abspath(DEFAULT_INDEX_PATH)) == os.path.dirname(
        os.path.abspath(DB_PATH)
    )
    assert os.path.abspath(DEFAULT_INDEX_PATH) != os.path.abspath(DB_PATH)


def test_indexed_count_tracks_documents(idx):
    assert idx.indexed_count() == 0
    idx.upsert(row_id=1, content="一条", module_id=1, user_id="u", session_id=1, created_at=None)
    assert idx.indexed_count() == 1
    idx.delete(1)
    assert idx.indexed_count() == 0


def test_business_db_has_no_retrieval_tables():
    """业务库里不应残留检索表（历史遗留需清理）。"""
    import sqlite3

    try:
        from db.db import DB_PATH
    except Exception:  # noqa: BLE001
        pytest.skip("业务库路径不可用")
    if not os.path.exists(DB_PATH):
        pytest.skip("业务库不存在")
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'retrieval%'"
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        pytest.skip("业务库不可读")
    assert not rows, f"业务库残留检索表：{[r[0] for r in rows]}"


def test_reset_index_singleton_clears_state():
    retrieval_mod.reset_index_singleton()
    assert retrieval_mod._index is None
    assert retrieval_mod._index_failed is False
