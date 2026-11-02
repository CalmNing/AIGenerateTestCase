"""P5 上下文工程（token 预算 / 裁剪）测试。

重点验证：
- **混合口径估算**：中文 1 字≈1 token（不是 chars/4 的 1/4），
  否则中文场景下预算会低估 2~3 倍、形同虚设；
- api_context 分级裁剪：核心接口保全文，辅助接口压骨架；
- 长文档分层压缩：保留含关键词的段落，而不是直接砍尾部；
- 预算充足时**不做任何裁剪**（行为与改造前一致）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.token_budget import (
    TokenBudget,
    TruncationReport,
    compact_api_context,
    compact_document,
    estimate_schema_tokens,
    estimate_tokens,
    split_api_sections,
    split_document_sections,
    truncate_text,
)


# --------------------------------------------------------------- 估算

def test_estimate_tokens_chinese_is_one_per_char():
    """中文 100 字 ≈ 100 token（chars/4 口径会严重低估）。"""
    assert estimate_tokens("测" * 100) == 100


def test_estimate_tokens_ascii_is_four_per_token():
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_mixed():
    # "用户登录" 4 中文字 + " login " 6 个 ASCII → 4 + 6/4 = 5
    assert estimate_tokens("用户登录 login ") == 5


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_none_safe():
    assert estimate_tokens(None) == 0


def test_estimate_schema_tokens():
    from utils.models import ResponseFormat

    t = estimate_schema_tokens(ResponseFormat)
    assert t > 0


def test_estimate_schema_tokens_none():
    assert estimate_schema_tokens(None) == 0


# --------------------------------------------------------------- 截断

def test_truncate_text_noop_when_short():
    assert truncate_text("abc", 100) == "abc"


def test_truncate_text_marks_truncation():
    out = truncate_text("x" * 500, 100)
    assert len(out) < 200
    assert "截断" in out


def test_truncate_text_prefers_line_boundary():
    text = "a" * 50 + "\n" + "b" * 500
    out = truncate_text(text, 100)
    # 应在换行处断开，而不是切在中间
    assert out.count("b") < 100


def test_truncate_text_empty():
    assert truncate_text("", 100) == ""
    assert truncate_text("abc", 0) == ""


# --------------------------------------------------------------- 预算

def test_budget_total_is_window_minus_reserved():
    b = TokenBudget(model_window=32768, reserved_output=8192, safety_factor=1.0)
    assert b.total_input_budget == 32768 - 8192


def test_budget_remaining_decreases_after_reserve():
    b = TokenBudget()
    before = b.remaining
    b.reserve("x", 1000)
    assert b.remaining == before - 1000


def test_budget_not_over_when_within():
    b = TokenBudget(model_window=10000, reserved_output=1000)
    b.reserve("a", 100)
    assert b.over_budget() is False


def test_budget_over_when_exhausted():
    b = TokenBudget(model_window=10000, reserved_output=1000)
    b.reserve("a", 99999)
    assert b.over_budget() is True


def test_budget_allocate_returns_available():
    b = TokenBudget(model_window=10000, reserved_output=1000, safety_factor=1.0)
    got = b.allocate("x", 500)
    assert got == 500


def test_budget_allocate_truncates_when_insufficient():
    b = TokenBudget(model_window=10000, reserved_output=1000, safety_factor=1.0)
    b.reserve("fixed", 8500)
    got = b.allocate("x", 5000)
    assert got < 5000


def test_budget_priority_zero_is_not_truncated():
    """priority<=0 表示不可裁剪内容（应挤占其他内容）。"""
    b = TokenBudget(model_window=10000, reserved_output=1000, safety_factor=1.0)
    b.reserve("fixed", 9000)
    got = b.allocate("important", 500, priority=0)
    assert got == 500


def test_truncation_report_accumulates():
    rep = TruncationReport()
    rep.add("a", 100, 40)
    rep.add("b", 50, 50)
    assert rep.any_truncated is True
    assert rep.saved_tokens == 60
    assert len(rep.items) == 2


def test_truncation_report_empty_is_not_truncated():
    rep = TruncationReport()
    assert rep.any_truncated is False
    assert rep.to_dict()["truncated"] is False


# --------------------------------------------------------------- api_context 分级

def _make_api_context(count=5, schema_fields=10):
    sections = []
    for i in range(1, count + 1):
        schema = {
            "properties": {f"f{j}": {"type": "string", "description": "x" * 40}
                           for j in range(schema_fields)},
        }
        sections.append(
            f"===== [{i}] 接口{i} =====\n"
            f"接口描述: 描述{i}\n"
            f"请求方法: POST\n"
            f"请求路径: /api/{i}\n"
            f"请求 Schema:\n" + json.dumps(schema, ensure_ascii=False, indent=2)
        )
    return "===== 以下 API 接口信息 =====\n\n" + "\n\n".join(sections)


def test_split_api_sections():
    ctx = _make_api_context(3)
    parts = split_api_sections(ctx)
    assert len(parts) >= 3
    assert any(idx == 1 for idx, _ in parts)


def test_split_api_sections_empty():
    assert split_api_sections("") == []
    assert split_api_sections("没有任何结构") == []


def test_compact_api_context_noop_when_short():
    ctx = _make_api_context(2, schema_fields=2)
    out, report = compact_api_context(ctx, core_endpoint_ids={1}, max_chars=100000)
    assert out == ctx
    assert report["truncated"] is False


def test_compact_api_context_keeps_core_full():
    """核心接口必须保留完整 Schema。"""
    ctx = _make_api_context(4, schema_fields=20)
    out, report = compact_api_context(ctx, core_endpoint_ids={1}, max_chars=1200)
    assert report["truncated"] is True
    # 核心接口的完整 schema 还在
    assert "===== [1] 接口1 =====" in out
    assert "请求 Schema" in out.split("===== [2]")[0]
    # 辅助接口被压缩（骨架化或省略，但必须明确标注，让模型/user 知道内容不全）
    assert ("已省略完整 Schema" in out) or ("因 token 预算被省略" in out)
    # 辅助接口的接口名仍在（模型知道存在这些接口）
    assert "===== [2] 接口2 =====" in out


def test_compact_api_context_never_cuts_core():
    ctx = _make_api_context(3, schema_fields=20)
    out, _ = compact_api_context(ctx, core_endpoint_ids={1, 2, 3}, max_chars=800)
    for i in (1, 2, 3):
        assert f"===== [{i}] 接口{i} =====" in out


def test_compact_api_context_reports_saving():
    ctx = _make_api_context(5, schema_fields=30)
    out, report = compact_api_context(ctx, core_endpoint_ids={1}, max_chars=1000)
    assert report["saved_tokens"] > 0
    assert len(out) < len(ctx)


# --------------------------------------------------------------- 文档分层

DOC = """# 需求文档

## 1. 登录功能
用户可以使用账号密码登录系统。

## 2. 订单导出
""" + ("订单支持批量导出Excel文件。" * 50) + """

## 3. 密码找回
""" + ("密码找回需要邮箱验证码。" * 50)


def test_split_document_sections():
    sections = split_document_sections(DOC)
    assert len(sections) >= 3


def test_split_document_sections_no_heading():
    assert len(split_document_sections("纯文本没有标题")) == 1


def test_compact_document_keeps_focus_section():
    out, report = compact_document(DOC, max_chars=600, focus_keywords=["登录"])
    assert report["truncated"] is True
    assert "账号密码登录" in out


def test_compact_document_noop_when_short():
    out, report = compact_document("短文档", max_chars=1000)
    assert out == "短文档"
    assert report["truncated"] is False


def test_compact_document_marks_compressed_sections():
    out, _ = compact_document(DOC, max_chars=400, focus_keywords=["登录"])
    assert "已压缩" in out or "省略" in out
