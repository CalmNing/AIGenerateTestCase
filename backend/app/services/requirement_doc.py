"""需求管理核心服务：结构化解析、差异计算、影响分析、用例联动文本块构建。

供 app.routes.requirement_doc 路由调用，全部为纯函数/无状态逻辑，
不依赖网络（飞书读取在路由层完成），可离线单测。

结构化模型：
- 飞书文档 rawContent 返回纯文本（标题无 # 前缀、无表格线），
  Markdown 文档（含 # 标题、| 表格）同样支持；
- 解析结果为章节（heading 路径）+ 条目（非空文本块）两级结构；
- 条目 key = 章节路径 + 内容 hash，内容不变则 key 稳定，
  因此需求条目 ↔ 测试用例 的关联映射在重新解析后自动保持。
"""
import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 飞书 wiki / docx 链接 → token（与 utils.document_prefetch 保持一致）
FEISHU_URL_RE = re.compile(
    r'https?://[a-zA-Z0-9-]+\.feishu\.cn/(?:wiki|docx)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)

_MD_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
# 飞书 rawContent 的纯文本标题：一、/（一）/1. / 1.1 / 1.1.1 / 第N章 开头且整行较短
_CN_NUM_HEAD_RE = re.compile(
    r'^(第[一二三四五六七八九十百0-9]+[章节篇部分]|[一二三四五六七八九十百]+[、.]\s*|'
    r'[一二三四五六七八九十百]+\s*[^\d\s、.，。]{0,12}$|'
    r'\d+(?:\.\d+){0,4}+\.?\s*\S.*)$'
)

# 中文/字母数字分词（中文连续片段 + 英数词），用于需求文本 ↔ 用例文本的词元匹配
_TOKEN_RE = re.compile(r'[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?')

_STOPWORDS = {
    "的", "了", "和", "与", "或", "在", "是", "为", "对", "从", "到", "中", "等", "需",
    "支持", "用户", "系统", "页面", "进行", "可以", "能够", "并且", "然后", "当", "时",
    "并", "该", "此", "其", "按", "被", "把", "请", "应", "将", "要求", "相关", "情况",
    "数据", "信息", "内容", "操作", "使用", "如果", "则", "即", "如", "见", "同", "个",
    "一", "二", "三", "点", "条", "项", "及", "且", "后", "前", "上", "下", "出", "入",
}


class RequirementItem:
    """一条结构化需求条目（章节下的一个文本块）。"""

    __slots__ = ("key", "section", "title", "content", "order_index", "status")

    def __init__(self, key: str, section: str, title: str, content: str,
                 order_index: int = 0, status: str = "active"):
        self.key = key
        self.section = section
        self.title = title
        self.content = content
        self.order_index = order_index
        self.status = status

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "section": self.section,
            "title": self.title,
            "content": self.content,
            "order_index": self.order_index,
            "status": self.status,
        }


class DiffEntry:
    """一条差异明细：added / modified / removed。"""

    __slots__ = ("type", "key", "section", "before", "after")

    def __init__(self, type: str, key: str, section: str,
                 before: str = "", after: str = ""):
        self.type = type
        self.key = key
        self.section = section
        self.before = before
        self.after = after

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "key": self.key,
            "section": self.section,
            "before": self.before,
            "after": self.after,
        }


def parse_feishu_token(url: str) -> Optional[str]:
    """从飞书 wiki/docx 链接中提取文档 token，非飞书链接返回 None。"""
    m = FEISHU_URL_RE.search(url or "")
    return m.group(1) if m else None


def parse_markdown_to_items(content: str) -> list[RequirementItem]:
    """把飞书正文（rawContent 纯文本或 Markdown）解析为结构化需求条目。

    - Markdown # 标题 → 章节路径（按标题层级拼接，同级替换末段）；
    - 飞书 rawContent 纯文本使用中文编号启发式识别章节（一、/1.1/第N章…）；
    - 其余非空文本行聚合为该章节下的条目；
    - 表格（Markdown | 行 / 飞书文本制表符分隔行）整块合并为一个条目。
    """
    items: list[RequirementItem] = []
    section_path: dict[int, str] = {}
    buf: list[str] = []
    order = 0

    def is_table_line(line: str) -> bool:
        s = line.strip()
        if s.startswith("|") and s.count("|") >= 2:
            return True
        if "\t" in s and len(s.split("\t")) >= 2 and len(s) <= 200:
            return True
        return False

    def is_plain_number_heading(line: str) -> bool:
        s = line.strip()
        if not s or len(s) > 60:
            return False
        if _MD_HEADING_RE.match(s):
            return False
        if is_table_line(s):
            return False
        # 1. / 1.1 / 1.1.1 开头
        if re.match(r'^\d+(?:\.\d+){0,4}+\.?\s*\S', s):
            return True
        # 一、 / （一） / 第一章
        if re.match(r'^(第[一二三四五六七八九十百0-9]+[章节篇部分])', s):
            return True
        if re.match(r'^[一二三四五六七八九十百]+[、.]\s*\S', s):
            return True
        return False

    def set_section(level: int, title: str) -> None:
        """设置章节路径：同级标题替换末段，深级标题收起其子路径后重设。"""
        for k in [k for k in section_path if k > level]:
            del section_path[k]
        section_path[level] = title

    def current_section() -> str:
        return " / ".join(section_path[k] for k in sorted(section_path))

    def flush():
        nonlocal buf, order
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        order += 1
        items.append(_make_item(current_section(), text, order))

    for raw_line in (content or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()

        md = _MD_HEADING_RE.match(line.strip())
        if md:
            flush()
            set_section(len(md.group(1)), md.group(2).strip())
            continue

        if is_plain_number_heading(line):
            flush()
            title = line.strip()
            # 数字编号按点数确定层级（1.1 → 二级）；中文编号视为一级
            num = re.match(r'^(\d+(?:\.\d+)*)', title)
            level = num.group(1).count(".") + 1 if num else 1
            set_section(level, title)
            continue

        if is_table_line(line) and buf and is_table_line(buf[-1]):
            # 表格连续行并入同一缓冲
            buf.append(line.strip())
            continue

        if line.strip() == "":
            flush()
            continue

        buf.append(line.strip())

    flush()
    return items


def _make_item(section: str, content: str, order_index: int) -> RequirementItem:
    key = _item_key(section, content)
    title = _first_line(content)
    return RequirementItem(key=key, section=section, title=title,
                           content=content, order_index=order_index)


def _item_key(section: str, content: str) -> str:
    digest = hashlib.sha1(f"{section}||{content}".encode("utf-8")).hexdigest()[:12]
    return f"it_{digest}"


def _first_line(text: str, limit: int = 40) -> str:
    first = (text or "").strip().split("\n", 1)[0].strip()
    return first[:limit]


def merge_structure(old_items: list[dict], new_items: list[dict]) -> list[dict]:
    """把旧结构中的条目 key 映射回新结构，保证关联映射稳定。

    规则：
    1. 章节与内容完全一致的条目 → 精确复用旧 key；
    2. 内容变化但章节不变的条目 → 按同章节内的出现顺序复用旧 key，
       使「修改」语义在差异对比中可识别，且关联映射不被打断；
    3. 找不到可复用旧 key 的条目 → 保留新 key（视为新增）。
    """
    old_by_identity: dict[tuple, str] = {}
    old_keys_by_section: dict[str, list[str]] = {}
    for it in old_items or []:
        if not it.get("key"):
            continue
        section = (it.get("section") or "").strip()
        content = (it.get("content") or "").strip()
        if section and content:
            old_by_identity[(section, content)] = it["key"]
        old_keys_by_section.setdefault(section, []).append(it["key"])

    used_keys: set[str] = set()
    merged: list[dict] = []
    pending: list[dict] = []

    for it in new_items or []:
        item = dict(it)
        identity = ((item.get("section") or "").strip(), (item.get("content") or "").strip())
        old_key = old_by_identity.get(identity)
        if old_key and old_key not in used_keys:
            item["key"] = old_key
            used_keys.add(old_key)
        else:
            pending.append(item)
        merged.append(item)

    for item in pending:
        section = (item.get("section") or "").strip()
        for old_key in old_keys_by_section.get(section, []):
            if old_key not in used_keys:
                item["key"] = old_key
                used_keys.add(old_key)
                break
    return merged


def compute_diff(old_items: list[dict], new_items: list[dict]) -> list[dict]:
    """按条目 key 对比新旧结构，生成 added / modified / removed 差异明细。"""
    old_map = {it.get("key"): it for it in (old_items or []) if it.get("key")}
    new_map = {it.get("key"): it for it in (new_items or []) if it.get("key")}

    diffs: list[dict] = []
    for key, old in old_map.items():
        if key not in new_map:
            diffs.append(DiffEntry("removed", key, old.get("section") or "",
                                   before=_compact(old.get("content") or "")).to_dict())
        else:
            new = new_map[key]
            if (old.get("content") or "").strip() != (new.get("content") or "").strip():
                diffs.append(DiffEntry(
                    "modified", key, new.get("section") or old.get("section") or "",
                    before=_compact(old.get("content") or ""),
                    after=_compact(new.get("content") or ""),
                ).to_dict())
    for key, new in new_map.items():
        if key not in old_map:
            diffs.append(DiffEntry("added", key, new.get("section") or "",
                                   after=_compact(new.get("content") or "")).to_dict())

    # 稳定排序：removed → modified → added，组内按章节
    type_rank = {"removed": 0, "modified": 1, "added": 2}
    diffs.sort(key=lambda d: (type_rank.get(d["type"], 9), d.get("section") or "", d.get("key") or ""))
    return diffs


def _compact(text: str, limit: int = 500) -> str:
    t = (text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def extract_keywords(text: str) -> set[str]:
    """需求文本关键词：中文字符 2-gram + 英数词，去停用词。"""
    tokens = _TOKEN_RE.findall((text or "").lower())
    words: set[str] = set()
    for tok in tokens:
        if re.fullmatch(r'[a-z][a-z0-9_]*', tok) or re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', tok):
            if tok not in _STOPWORDS:
                words.add(tok)
            continue
        # 中文：连续片段按单字切分后组 2-gram（单字区分度太低）
        buf: list[str] = []
        for ch in tok:
            if ch in _STOPWORDS:
                if buf:
                    _add_grams(words, buf)
                    buf = []
                continue
            buf.append(ch)
        if buf:
            _add_grams(words, buf)
    return words


def _add_grams(words: set[str], chars: list[str]) -> None:
    if len(chars) == 1:
        words.add(chars[0])
        return
    for i in range(len(chars) - 1):
        words.add(chars[i] + chars[i + 1])


def _item_text(it: dict) -> str:
    return f"{it.get('section') or ''}\n{it.get('content') or ''}"


def _case_text(case) -> str:
    """测试用例的可匹配文本（预置条件/步骤/预期结果为 JSON 数组）。"""
    parts = [case.case_name or ""]
    for field in (case.preset_conditions, case.steps, case.expected_results):
        if isinstance(field, list):
            parts.extend(str(x) for x in field)
        elif field:
            parts.append(str(field))
    return "\n".join(parts)


def _overlap_score(item_words: set[str], case_words: set[str]) -> int:
    if not item_words or not case_words:
        return 0
    return len(item_words & case_words)


def find_affected_testcases(db, doc_id: int, session_id: Optional[int],
                            old_items: list[dict], new_items: list[dict],
                            case_ids: Optional[list[int]] = None) -> list[dict]:
    """根据差异自动识别受影响的测试用例。

    匹配策略（可解释、无网络依赖）：
    1. 关联映射优先：RequirementTestcaseLink 记录了条目 key ↔ 用例；
    2. 关键词重合兜底：条目文本与用例名称/步骤/预期结果的词元重合度排序。

    返回 [{testcase_id, session_id, case_name, matched_item_keys, keywords,
           suggestion}]，按重合度降序。
    """
    from sqlmodel import select
    from db.models import TestCase, RequirementTestcaseLink

    old_map = {it.get("key"): it for it in (old_items or []) if it.get("key")}
    new_map = {it.get("key"): it for it in (new_items or []) if it.get("key")}
    changed = [k for k, old in old_map.items()
               if k not in new_map
               or (old_map[k].get("content") or "").strip() != (new_map[k].get("content") or "").strip()]
    if not changed:
        return []

    # 1) 关联映射
    stmt = select(RequirementTestcaseLink).where(
        RequirementTestcaseLink.doc_id == doc_id,
        RequirementTestcaseLink.item_key.in_(changed),  # type: ignore[attr-defined]
    )
    link_map: dict[int, set[str]] = {}
    for link in db.exec(stmt).all():
        link_map.setdefault(link.testcase_id, set()).add(link.item_key)

    # 2) 关键词重合
    changed_items = [new_map[k] for k in changed if k in new_map]
    removed_items = [old_map[k] for k in changed if k not in new_map]
    match_pool = changed_items + removed_items
    item_words = {it["key"]: extract_keywords(_item_text(it)) for it in match_pool}

    q = select(TestCase)
    if session_id:
        q = q.where(TestCase.session_id == session_id)  # type: ignore[attr-defined]
    if case_ids:
        q = q.where(TestCase.id.in_(case_ids))  # type: ignore[attr-defined]
    cases = db.exec(q).all()

    scored: dict[int, dict] = {}
    for case in cases:
        keys_by_link = link_map.get(case.id or 0, set())
        case_words = extract_keywords(_case_text(case))
        best_score = 0
        matched_keys: list[str] = []
        for it in match_pool:
            score = _overlap_score(item_words.get(it["key"], set()), case_words)
            if it["key"] in keys_by_link:
                score += 2  # 显式关联加权
            if score > 0:
                matched_keys.append(it["key"])
            best_score = max(best_score, score)
        if best_score <= 0 and not keys_by_link:
            continue
        removed_keys = [it["key"] for it in removed_items if it["key"] in matched_keys]
        suggestion = build_case_suggestion(changed, removed_keys, keys_by_link)
        scored[case.id] = {
            "testcase_id": case.id,
            "session_id": case.session_id,
            "case_name": case.case_name,
            "matched_item_keys": matched_keys,
            "keywords": sorted(list(item_words.get(matched_keys[0], set()) & case_words))[:8] if matched_keys else [],
            "score": best_score,
            "suggestion": suggestion,
        }

    results = sorted(scored.values(), key=lambda x: -x["score"])
    for r in results:
        r.pop("score", None)
    return results


def build_case_suggestion(changed_keys: list[str], removed_keys: list[str],
                          linked_keys: set[str]) -> str:
    """为受影响用例生成一句修改建议。"""
    if removed_keys:
        return "关联的需求条目已删除，建议确认用例是否需要移除或合并到其他用例。"
    return "关联的需求条目内容发生变更，建议核对用例步骤与预期结果并同步修改。"


def build_case_update_blocks(items: list[dict], case) -> str:
    """构建用于 LLM 修改测试用例的需求上下文文本块。"""
    lines: list[str] = []
    for it in items or []:
        section = (it.get("section") or "").strip()
        content = (it.get("content") or "").strip()
        lines.append(f"【{section}】" if section else "【需求】")
        lines.append(content)
    lines.append("")
    lines.append(f"当前测试用例「{case.case_name}」：")
    if isinstance(case.preset_conditions, list) and case.preset_conditions:
        lines.append("预置条件：" + "；".join(str(x) for x in case.preset_conditions))
    if isinstance(case.steps, list) and case.steps:
        lines.append("测试步骤：")
        for i, s in enumerate(case.steps, 1):
            lines.append(f"  {i}. {s}")
    if isinstance(case.expected_results, list) and case.expected_results:
        lines.append("预期结果：" + "；".join(str(x) for x in case.expected_results))
    return "\n".join(lines)


async def analyze_case_impact_with_llm(diffs: list[dict], blocks: list[dict],
                                       api_key: str, api_base_url: str,
                                       api_model: str, api_proxy_url: Optional[str] = None) -> list[dict]:
    """用 LLM 逐用例生成基于差异的修改建议（失败时回退到规则建议，不抛错）。"""
    if not blocks:
        return []
    try:
        from utils.llm_factory import build_chat_model
        from utils.models import CaseImpactSuggestionList
        from utils.model_utils import _structured_output_robust

        model = build_chat_model(api_key, api_base_url, api_model, api_proxy_url, max_tokens=4096)
        diff_lines = []
        for d in diffs or []:
            diff_lines.append(f"- [{d.get('type')}] {d.get('section') or ''} "
                              f"{_compact(d.get('after') or d.get('before') or '', 200)}")
        result = await _structured_output_robust(model, CaseImpactSuggestionList, [
            {"role": "system", "content": (
                "你是测试用例维护专家。用户给出需求文档的差异明细和若干受影响的测试用例，"
                "请为每个用例给出简短的中文修改建议（1~2 句，说明应如何同步修改步骤或预期结果）。"
                "按顺序为每个用例输出建议。")},
            {"role": "user", "content": (
                "## 需求差异\n" + "\n".join(diff_lines)
                + "\n\n## 受影响的测试用例\n" + "\n\n".join(b["block"] for b in blocks))},
        ])
        if isinstance(result, CaseImpactSuggestionList):
            return [s.model_dump() for s in result.suggestions]
        if isinstance(result, dict):
            return result.get("suggestions", [])
        return []
    except Exception as e:
        logger.warning("LLM 影响分析失败，回退规则建议: %s", e)
        return []



# ===== 实体合一:生成输入日志统一入口 =====

def upsert_generation_entry(db, *, content, module_id, session_id, user_id=None):
    """把一次生成的需求输入写入需求实体（source_type='generation'）。

    取代原 HistoryPrompt 落库链路：复用同一套内容清洗与 (session, module, content)
    去重语义；写入即进入 FTS 检索索引（rowid = requirement_doc.id）。
    返回 RequirementDoc（已存在时为既有行），无有效内容时返回 None。
    """
    from utils.helpers import _clean_history_prompt_content, _normalize_history_module_id

    cleaned = _clean_history_prompt_content(content)
    if not cleaned or db is None:
        return None
    normalized_module_id = _normalize_history_module_id(module_id)

    from sqlmodel import select
    from db.models import RequirementDoc

    filters = [
        RequirementDoc.content_snapshot == cleaned,
        RequirementDoc.source_type == "generation",  # type: ignore[attr-defined]
        RequirementDoc.session_id == session_id,  # type: ignore[attr-defined]
    ]
    if normalized_module_id is None:
        filters.append(RequirementDoc.module_id.is_(None))  # type: ignore[attr-defined]
    else:
        filters.append(RequirementDoc.module_id == normalized_module_id)  # type: ignore[attr-defined]
    if user_id is None:
        filters.append(RequirementDoc.user_id.is_(None))  # type: ignore[attr-defined]
    else:
        filters.append(RequirementDoc.user_id == user_id)  # type: ignore[attr-defined]

    existing = db.exec(select(RequirementDoc).where(*filters)).first()
    if existing:
        _index_requirement_doc(existing)
        return existing

    import re as _re
    first_line = _re.split(r"\r?\n", cleaned, 1)[0].strip()
    doc = RequirementDoc(
        title=first_line[:60] or "未命名需求",
        url="",
        feishu_token="",
        content_snapshot=cleaned,
        structured=[it.to_dict() for it in parse_markdown_to_items(cleaned)],
        session_id=session_id,
        version=0,
        module_id=normalized_module_id,
        source_type="generation",
        user_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _index_requirement_doc(doc)
    return doc


def _index_requirement_doc(doc) -> None:
    """写入即索引（失败静默,不影响生成主流程）。"""
    try:
        from utils.retrieval import get_index

        idx = get_index()
        if idx is None:
            return
        idx.upsert(
            row_id=doc.id or 0,
            content=doc.content_snapshot or "",
            module_id=doc.module_id,
            user_id=doc.user_id,
            session_id=doc.session_id,
            created_at=doc.created_at.isoformat() if doc.created_at else None,
        )
    except Exception as e:
        logger.warning("需求实体写入检索索引失败(已忽略): %s", e)
