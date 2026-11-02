"""Token 预算与上下文裁剪（P5：上下文工程）。

背景（调研结论 B2）：
- 现状有正确的"system/human 拆分"（`_build_generation_messages`），但**没有预算**：
  - `api_context` 逐接口全量拼两段 JSON Schema，**无截断**；
  - 历史需求 20 条**无字符截断**；
  - 长文档（飞书/蓝湖/上传文件）**只截断不压缩**；
  - schema 作为工具定义**每次调用都付**固定税（ResponseFormat ≈1265 token）。
- 后果：接口多 + 历史长 + 文档长叠加时**隐性超窗**（表现为 API 报错或静默截断），
  且成本随数据线性放大。

本模块提供：
1. `estimate_tokens()`：**混合口径**估算（CJK 1 字≈1 token，ASCII 4 字符≈1 token），
   比"一律 chars/4"更贴近真实（中文场景下 chars/4 会严重低估）。
2. `TokenBudget`：按优先级分配预算并裁剪，每块内容有明确的优先级与裁剪策略。
3. `truncate_text()`：按字符+行边界安全截断（不切碎 JSON 结构）。
4. `compact_api_context()`：api_context 分级裁剪——核心接口保全文，
   依赖补全接口只保留骨架（方法/路径/必填字段）。

设计原则：
- **预算只在超限时才裁剪**，正常输入行为完全不变；
- 裁剪必须**可见**（返回裁剪报告），让用户知道内容被截断了；
- 裁剪优先级由低到高：历史上下文 → 依赖接口骨架 → 文档非目标章节 → api_context 尾部。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# CJK 字符（按 1 字符 ≈ 1 token 估）
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
# 其余 ASCII/西文（按 4 字符 ≈ 1 token 估）
_ASCII_PER_TOKEN = 4.0

# 默认模型窗口（保守取 32K，DeepSeek-chat 上下文 64K，这里留足输出与工具余量）
DEFAULT_MODEL_WINDOW = 32768
# 预留给输出的 token（max_tokens=16384 时要留够）
DEFAULT_RESERVED_OUTPUT = 8192
# 输入预算的默认安全系数（在窗口里再留 15% 余量）
DEFAULT_SAFETY_FACTOR = 0.85


def estimate_tokens(text: Optional[str]) -> int:
    """估算文本 token 数（混合口径）。

    CJK 字符按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。
    对比"一律 chars/4"：中文场景下会低估 2~3 倍，导致预算形同虚设。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk + other / _ASCII_PER_TOKEN)


def estimate_schema_tokens(schema: Any) -> int:
    """估算 Pydantic schema 作为工具定义时的 token 占用。"""
    if schema is None:
        return 0
    try:
        return estimate_tokens(json.dumps(schema.model_json_schema(), ensure_ascii=False))
    except Exception:  # noqa: BLE001
        try:
            return estimate_tokens(str(schema))
        except Exception:  # noqa: BLE001
            return 0


def split_document_sections(text: str) -> list[tuple[str, str]]:
    """把长文档按标题/编号切成 [(标题, 正文)]。

    用于长文档分层压缩：超预算时先压缩非关键段，而不是直接砍掉尾部
    （旧实现直接截尾，关键需求在末尾时会**静默丢失且无告警**）。
    """
    if not text:
        return []
    # 匹配 Markdown 标题、一级编号标题、"第X章"
    pattern = re.compile(
        r"(?m)^(?=(?:#{1,6}\s+)|(?:[一二三四五六七八九十百]+[、.．]\s*)|"
        r"(?:第[0-9一二三四五六七八九十百]+[章节部分]\s*)|(?:\d+(?:\.\d+)*[、.．]\s*))"
    )
    marks = [m.start() for m in pattern.finditer(text)]
    if not marks:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    if marks[0] > 0:
        sections.append(("", text[: marks[0]]))
    for i, start in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(text)
        chunk = text[start:end]
        first_line = chunk.split("\n", 1)[0].strip()
        sections.append((first_line[:80], chunk))
    return sections


def compact_document(
    text: str,
    *,
    max_chars: int,
    focus_keywords: Optional[list[str]] = None,
) -> tuple[str, dict]:
    """长文档分层压缩：按段裁剪，保留含关键词的段落。

    与 `truncate_text` 的区别：不砍尾部，而是**逐段压缩**——
    含关键词（本次需求关键词）的段落保全文，其他段落压成首行摘要。

    返回 (压缩后文本, 报告)。
    """
    report = TruncationReport()
    if not text or len(text) <= max_chars:
        return text, report.to_dict()

    before_tokens = estimate_tokens(text)
    sections = split_document_sections(text)
    if len(sections) <= 1:
        new_text = truncate_text(text, max_chars)
        report.add("document", before_tokens, estimate_tokens(new_text),
                   "文档无法按标题分段，已截断")
        return new_text, report.to_dict()

    keywords = [k.lower() for k in (focus_keywords or []) if k and k.strip()]

    def _is_focus(title: str, body: str) -> bool:
        if not keywords:
            return False
        hay = (title + " " + body[:2000]).lower()
        return any(k in hay for k in keywords)

    kept: list[str] = []
    used = 0
    for title, body in sections:
        if used >= max_chars:
            break
        if _is_focus(title, body) or len(body) <= 400:
            kept.append(body)
            used += len(body)
        else:
            # 非重点段：只保留首行 + 前 200 字
            compact = body[:200].rstrip() + "\n（该段已压缩，完整内容因 token 预算被省略）\n"
            kept.append(compact)
            used += len(compact)

    out = "".join(kept)
    if len(out) > max_chars:
        out = truncate_text(out, max_chars)
    report.add("document", before_tokens, estimate_tokens(out),
               f"文档超出预算，已按段落压缩（{len(sections)} 段）")
    return out, report.to_dict()


def truncate_text(text: str, max_chars: int, *, marker: str = "…（内容已截断）") -> str:
    """按字符截断，尽量在行边界断开（避免切碎 JSON/结构化内容）。"""
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    # 优先在换行处断开（最后 20% 区间内找）
    cut = head.rfind("\n", int(max_chars * 0.8))
    if cut > 0:
        head = head[:cut]
    return head + marker


@dataclass
class TruncationReport:
    """裁剪报告（让被裁剪的内容可见）。"""
    items: list[dict] = field(default_factory=list)
    saved_tokens: int = 0

    def add(self, name: str, before_tokens: int, after_tokens: int, reason: str = "") -> None:
        self.items.append({
            "name": name,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(0, before_tokens - after_tokens),
            "reason": reason,
        })
        self.saved_tokens += max(0, before_tokens - after_tokens)

    @property
    def any_truncated(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict:
        return {
            "truncated": self.any_truncated,
            "saved_tokens": self.saved_tokens,
            "items": self.items,
        }


class TokenBudget:
    """按优先级分配 token 预算并裁剪内容块。

    ```
    budget = TokenBudget(model_window=32768, reserved_output=8192)
    budget.reserve("schema", estimate_schema_tokens(schema))
    budget.reserve("system", estimate_tokens(system_text))
    left = budget.remaining
    ```

    裁剪策略按 `priority` 升序（数字小的先被裁）。
    """

    def __init__(
        self,
        *,
        model_window: int = DEFAULT_MODEL_WINDOW,
        reserved_output: int = DEFAULT_RESERVED_OUTPUT,
        safety_factor: float = DEFAULT_SAFETY_FACTOR,
    ):
        self.model_window = model_window
        self.reserved_output = reserved_output
        self.safety_factor = safety_factor
        self._reserved: dict[str, int] = {}
        self.report = TruncationReport()

    @property
    def total_input_budget(self) -> int:
        """可用于输入内容的 token 预算。"""
        usable = int(self.model_window * self.safety_factor) - self.reserved_output
        return max(0, usable)

    @property
    def remaining(self) -> int:
        """当前剩余可分配预算。"""
        return max(0, self.total_input_budget - sum(self._reserved.values()))

    def reserve(self, name: str, tokens: int) -> None:
        """登记一块已固定的内容（如 system 前缀、schema）。"""
        self._reserved[name] = max(0, int(tokens))

    def reserved(self) -> dict[str, int]:
        return dict(self._reserved)

    def over_budget(self) -> bool:
        return self.remaining <= 0

    def allocate(self, name: str, tokens: int, *, priority: int = 50) -> int:
        """为一块内容分配预算。

        返回实际可用的 token 数（可能小于请求值，表示已被裁剪）。
        """
        want = max(0, int(tokens))
        left = self.remaining
        if want <= left:
            self.reserve(name, want)
            return want
        # 预算不足：按优先级决定谁被裁
        if priority <= 0:
            # 不可裁剪的内容：挤占其他内容
            self.reserve(name, want)
            return want
        self.reserve(name, left)
        return left

    def note_truncation(self, name: str, before: str, after: str, reason: str = "") -> None:
        self.report.add(
            name,
            estimate_tokens(before),
            estimate_tokens(after),
            reason,
        )


# ---------------- api_context 分级裁剪 ----------------

_API_SECTION_SPLIT_RE = re.compile(r"(?m)^===== \[(\d+)\] ")


def split_api_sections(api_context: str) -> list[tuple[int, str]]:
    """把 api_context 拆成 [(接口编号, 段落文本)]。"""
    if not api_context:
        return []
    parts: list[tuple[int, str]] = []
    matches = list(_API_SECTION_SPLIT_RE.finditer(api_context))
    if not matches:
        return []
    head = api_context[: matches[0].start()]
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(api_context)
        try:
            idx = int(m.group(1))
        except (TypeError, ValueError):
            continue
        parts.append((idx, api_context[m.start():end]))
    if head.strip():
        parts.insert(0, (0, head))
    return parts


def compact_api_section(section: str, max_chars: int) -> str:
    """把一个接口段落压缩成骨架：方法/路径/必填字段/响应字段名。

    用于"依赖补全接口"这类辅助接口——它们只需要让模型知道"有这么个接口"，
    不需要完整 schema（完整 schema 才是 token 大头）。
    """
    if not section or len(section) <= max_chars:
        return section

    kept: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        # 保留：接口名、描述、方法、路径、必填字段、请求骨架的必填行
        if (
            stripped.startswith("===== [")
            or stripped.startswith("接口描述:")
            or stripped.startswith("请求方法:")
            or stripped.startswith("请求路径:")
            or stripped.startswith("必填字段:")
            or stripped.startswith("请求骨架:")
        ):
            kept.append(line)
    # 补一行说明被压缩
    out = "\n".join(kept)
    if len(out) > max_chars:
        out = truncate_text(out, max_chars, marker="…（该接口规格已压缩，仅保留骨架）")
    return out + "\n（该接口为依赖补全接口，已省略完整 Schema，仅保留骨架）"


def compact_api_context(
    api_context: str,
    *,
    core_endpoint_ids: Optional[set[int]] = None,
    max_chars: int,
) -> tuple[str, dict]:
    """按"核心接口保全文、其余压骨架"裁剪 api_context。

    `core_endpoint_ids`：用户显式选中的接口（序号，1-based，与 prompt 中的编号一致）。
    不在该集合里的接口视为"辅助/依赖补全接口"，压缩为骨架。

    返回 (裁剪后的文本, 裁剪报告)。
    """
    report = TruncationReport()
    if not api_context:
        return "", report.to_dict()

    before_tokens = estimate_tokens(api_context)
    if len(api_context) <= max_chars:
        return api_context, report.to_dict()

    sections = split_api_sections(api_context)
    if not sections:
        # 拆不出结构 → 直接截断
        new_text = truncate_text(api_context, max_chars)
        report.add("api_context", before_tokens, estimate_tokens(new_text),
                   "接口上下文超出预算，已截断")
        return new_text, report.to_dict()

    core = core_endpoint_ids or set()
    head_parts: list[str] = []
    core_parts: list[str] = []
    aux_parts: list[str] = []
    used = 0

    for idx, text in sections:
        if idx == 0:
            head_parts.append(text)
            used += len(text)
            continue
        if not core or idx in core:
            core_parts.append(text)
            used += len(text)
        else:
            aux_parts.append(text)

    # 预算不够时，先压辅助接口（辅助接口可以压到骨架，核心接口不动）
    if used > max_chars and aux_parts:
        # 核心接口必须完整保留 → 辅助接口只能占用"剩余"额度
        core_len = sum(len(p) for p in core_parts)
        leftover = max_chars - core_len
        if leftover < 0:
            # 连核心接口都放不下：这是不可调和的冲突，只能整体截断
            # （记录告警，让调用方知道内容不完整）
            leftover = 0
        per_aux = max(200, leftover // max(1, len(aux_parts))) if leftover else 0
        compacted = []
        for text in aux_parts:
            if per_aux <= 0:
                # 完全放不下 → 只保留接口名一行，让模型知道存在这个接口
                head_line = text.split("\n", 1)[0]
                compacted.append(head_line + "\n（该接口规格因 token 预算被省略）\n")
                continue
            c = compact_api_section(text, per_aux)
            compacted.append(c)
        aux_parts = compacted
        used = sum(len(p) for p in head_parts + core_parts + aux_parts)

    # 组装：核心接口在前（保证不被尾部截断波及），辅助接口在后
    core_full = "".join(head_parts + core_parts)
    out = core_full + "".join(aux_parts)
    if len(out) > max_chars:
        # 核心接口是用户明确要求覆盖的范围 → **保全文**（哪怕超预算），
        # 只裁辅助接口。若核心接口本身就超预算，记录告警说明"预算不足"，
        # 由上层决定是否提示用户（此时裁剪核心接口会直接丢失用户要的接口）。
        room = max_chars - len(core_full)
        if room > 0:
            out = core_full + truncate_text("".join(aux_parts), room)
        else:
            # 核心接口本身就超预算：辅助接口每个只留接口名一行，
            # 让模型知道"还有这些接口"，同时明确标注规格被省略。
            aux_head = "".join(
                p.split("\n", 1)[0] + "\n（该接口规格因 token 预算被省略）\n"
                for p in aux_parts
            )
            out = core_full + aux_head
        report.add(
            "api_context", before_tokens, estimate_tokens(out),
            (
                f"接口上下文超出预算（{max_chars} 字符），已裁剪辅助接口"
                + ("；核心接口本身已超预算，未做裁剪" if room <= 0 else "")
            ),
        )
    elif aux_parts and any("已省略完整 Schema" in p or "因 token 预算被省略" in p for p in aux_parts):
        report.add(
            "api_context", before_tokens, estimate_tokens(out),
            "接口上下文超出预算，辅助接口已压缩为骨架",
        )
    return out, report.to_dict()
