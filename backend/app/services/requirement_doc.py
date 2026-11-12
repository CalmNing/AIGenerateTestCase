"""需求管理核心服务：结构化解析（严格按标题等级）、差异计算。

供 app.routes.requirement_doc 路由调用，全部为纯函数/无状态逻辑，
不依赖网络（飞书读取在路由层完成），可离线单测。

结构化模型：
- 飞书文档 rawContent 返回纯文本（标题无 # 前缀、无表格线），
  Markdown 文档（含 # 标题、| 表格）同样支持；
- 带标题标记的正文进入严格标题等级模式：仅标题行定义章节并按等级嵌套；
- 解析结果为章节（heading 路径）+ 条目（非空文本块）两级结构；
- 条目 key = 章节路径 + 内容 hash，内容不变则 key 稳定。
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


_ATTACH_HEADING_RE = re.compile(r'^#{0,6}\s*(文档图片|附件|附图|配图|截图)\s*[（(]', re.IGNORECASE)
_ATTACH_IMAGE_HEADING_RE = re.compile(r'^#{1,6}\s*图片\s*\d+')
_IMAGE_LINE_RE = re.compile(r'^(image\d*\.(png|jpe?g|gif|bmp|webp)|图片\d*\.png)$', re.IGNORECASE)

# 短标题启发式的上限(与 parse 主循环保持一致)
_SHORT_HEADING_MAX_LEN = 20


def _candidate_outline_lines(content: str) -> list[dict]:
    """抽出候选大纲行（短、无句末标点、非 image/表格/纯数字），供 LLM 判定层级。

    返回 [{line_no(1-based 原文行号), text}]。
    """
    lines = content.replace("\r\n", "\n").split("\n")
    candidates: list[dict] = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or len(s) > _SHORT_HEADING_MAX_LEN:
            continue
        if _IMAGE_LINE_RE.match(s):
            continue
        if re.fullmatch(r'\d+(\.\d+)*', s):
            continue
        if re.search(r'[。！？；，、：]', s):
            continue
        if s.startswith("|") or s.startswith("#"):
            continue
        # 飞书编号标题由编号启发式处理，不进 LLM 判定池
        if re.match(r'^(第[一二三四五六七八九十百0-9]+[章节篇部分]|[一二三四五六七八九十百]+[、.]|\d+(?:\.\d+){0,4}+\.?)', s):
            continue
        candidates.append({"line_no": i + 1, "text": s})
    return candidates


async def parse_outline_levels_with_llm(content: str, api_key: str, api_base_url: str,
                                        api_model: str, api_proxy_url=None) -> "dict[int, int] | None":
    """用 LLM 判定候选大纲行的层级（1-based 深度），失败返回 None（降级启发式）。

    只发送候选行（非全文），成本可控；输出 {"line_no": level} 映射。
    """
    if not api_key:
        return None
    candidates = _candidate_outline_lines(content)
    if len(candidates) < 2:
        return None  # 候选太少，启发式足够

    try:
        from utils.llm_factory import build_chat_model
        from utils.models import OutlineLevelList
        from utils.model_utils import _structured_output_robust

        cand_text = "\n".join(f"{c['line_no']}: {c['text']}" for c in candidates)
        model = build_chat_model(api_key, api_base_url, api_model, api_proxy_url, max_tokens=4096)
        result = await _structured_output_robust(model, OutlineLevelList, [
            {"role": "system", "content": (
                "你是文档结构分析专家。输入是从需求文档抽出的候选标题行（带行号），"
                "请根据标题间的语义包含关系推断每行的层级深度（1=顶级章节，2=其子章节，以此类推）。"
                "判断依据：标题的含义从属关系（如「待办」是「工作台」下的场景则层级更深）、"
                "上下级包含词（如「撤回之后数据怎么显示」隶属于「撤回」）。"
                "同级并列的标题层级相同。只输出 JSON。")},
            {"role": "user", "content": (
                "候选标题行：\n" + cand_text +
                "\n\n请为每行输出层级深度（1 起始），JSON 数组格式：[{\"line_no\": 行号, \"level\": 层级}, ...]，"
                "必须覆盖所有候选行。")},
        ])
        rows = None
        if isinstance(result, OutlineLevelList):
            rows = [{"line_no": r.line_no, "level": r.level} for r in result.levels]
        elif isinstance(result, dict):
            rows = result.get("levels") or []
        if not rows:
            return None

        mapping: dict[int, int] = {}
        for r in rows:
            try:
                ln = int(r.get("line_no"))
                lv = int(r.get("level"))
            except (TypeError, ValueError):
                continue
            if ln > 0 and 1 <= lv <= 6:
                mapping[ln] = lv
        # 覆盖率检查：至少覆盖 80% 候选行才算可用
        if len(mapping) < int(len(candidates) * 0.8):
            return None
        return mapping
    except Exception as e:
        logger.warning("LLM 大纲层级判定失败（降级启发式）: %s", e)
        return None


def _strip_attachment_appendix(content: str) -> str:
    """剥离文档尾部的附件附录（图片清单等），只保留正文需求内容。

    飞书同步会在正文后追加 "## 文档图片（共 N 张…）" 附录块，其中只有图片
    标题与 image.png 引用行，不携带任何需求语义；混进结构化条目会稀释覆盖视图。
    """
    lines = content.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        s = line.strip()
        if _ATTACH_HEADING_RE.match(s) or _ATTACH_IMAGE_HEADING_RE.match(s):
            skipping = True
            continue
        if skipping:
            continue
        out.append(line)
    return "\n".join(out)


def _is_image_reference(line: str) -> bool:
    """裸图片引用行（image.png 等），不承载需求语义。"""
    s = line.strip()
    if not s or len(s) > 60:
        return False
    return bool(_IMAGE_LINE_RE.match(s))


def has_markdown_headings(content: str) -> bool:
    """正文是否携带 Markdown 标题标记（# 前缀行）。

    飞书 blocks API 重建成功时，标题级别在 # 前缀里（飞书官方权威）。
    此时解析必须严格按标题等级分类：仅 # 标题行定义章节并按等级嵌套，
    编号/短标题启发式全部禁用，避免正文行被误抬为章节、打乱真实层级。
    """
    for line in (content or "").replace("\r\n", "\n").split("\n"):
        if _MD_HEADING_RE.match(line.strip()):
            return True
    return False


def parse_markdown_to_items(content: str, outline_levels: "dict[int, int] | None" = None,
                            disable_short_heading: bool = False) -> list[RequirementItem]:
    """把飞书正文（rawContent 纯文本或 Markdown）解析为结构化需求条目。

    - Markdown # 标题 → 章节路径（按标题层级拼接，同级替换末段）；
    - 严格标题等级模式（正文带 # 标题标记，或 disable_short_heading=True）：
      仅 # 标题行定义章节并严格按标题等级嵌套（1/2/3…级对应章节路径深度），
      编号/短标题启发式全部禁用——正文行（含"对我司而言/知识范围"等短行）
      不再被抬为章节，全部归属其上方最近的标题章节；
    - 飞书 rawContent 纯文本使用中文编号启发式识别章节（一、/1.1/第N章…）；
    - 短标题启发式：无编号但"短、无句末标点、后跟正文"的独立行视为子标题
      （飞书 rawContent 的大纲行如"底层逻辑/台账页面/工作台"）；
    - outline_levels：LLM 判定的候选大纲行层级映射（原文行号 1-based → 深度），
      提供时短标题层级以此为准；缺省用启发式深度；
    - 其余非空文本行聚合为该章节下的条目；
    - 表格（Markdown | 行 / 飞书文本制表符分隔行）整块合并为一个条目；
    - 附件附录（图片清单）与裸 image 行被过滤，不产生条目。
    """
    content = _strip_attachment_appendix(content or "")
    # 严格标题等级模式：正文自带 # 标题标记（飞书 blocks 重建）时只信标记
    strict_levels = bool(disable_short_heading) or has_markdown_headings(content)
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
        # 纯数字行是数据值（如 A/Q 问答里的"200"），不是标题
        if re.fullmatch(r'\d+(\.\d+)*', s):
            return False
        if _MD_HEADING_RE.match(s):
            return False
        if is_table_line(s):
            return False
        # 数字编号标题：编号后须有 ./、/空格 分隔，且整行是短标题形态
        # （避免把以数字开头的正文句当标题，如"2 个子能力共享同一张…"）
        mnum = re.match(r'^(\d+(?:\.\d+){0,4})[.、]?\s+(\S.*)$', s)
        if mnum:
            title_part = mnum.group(2)
            if len(title_part) <= 40 and not re.search(r'[。]', s):
                return True
            return False
        # 1.1.1 / 1.1 紧贴文本（无空格分隔）的短标题
        if re.match(r'^\d+(?:\.\d+){1,4}[^\s.]', s) and len(s) <= 40 and not re.search(r'[。]', s):
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

    def is_short_heading(line: str, next_line: str) -> bool:
        """短标题启发式：无编号短行 + 后跟较长正文行 → 视为子标题。

        飞书 rawContent 的大纲行（如"底层逻辑/台账页面/工作台"）没有编号前缀，
        特征：短（≤20字）、无句末标点、独立成行、下一行是明显更长的正文。
        A/Q 问答的值行（如"根据选择进行显示"后跟"200"）不满足"更长"条件。
        """
        s = line.strip()
        if not s or len(s) > 20:
            return False
        if is_table_line(s) or _IMAGE_LINE_RE.match(s):
            return False
        if _MD_HEADING_RE.match(s):
            return False
        if is_plain_number_heading(s):
            return False
        # 含句末标点/冒号/顿号的行更像正文或引导语，不当标题
        if re.search(r'[。！？；，、：]', s):
            return False
        n = (next_line or "").strip()
        if not n:
            return False
        # 下一行必须是明显更长的正文（差值 ≥4 字），避免 A/Q 值行误判
        if len(n) < len(s) + 4:
            return False
        return True

    # 预扫描下一非空行,供短标题判断
    all_lines = (content or "").replace("\r\n", "\n").split("\n")
    next_nonempty: dict[int, str] = {}
    for i, raw in enumerate(all_lines):
        for j in range(i + 1, len(all_lines)):
            if all_lines[j].strip():
                next_nonempty[i] = all_lines[j]
                break

    for li, raw_line in enumerate(all_lines):
        line = raw_line.rstrip()
        stripped = line.strip()
        # 兼容旧快照:剥掉历史版本给 bullet 行加的 "- " 前缀
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()

        if _is_image_reference(stripped):
            # 裸图片引用行：不产生条目
            continue

        md = _MD_HEADING_RE.match(stripped)
        if md:
            flush()
            set_section(len(md.group(1)), md.group(2).strip())
            continue

        if not strict_levels and is_plain_number_heading(line):
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

        if stripped == "":
            flush()
            continue

        if not strict_levels and is_short_heading(stripped, next_nonempty.get(li, "")):
            flush()
            llm_level = (outline_levels or {}).get(li + 1)
            if llm_level:
                set_section(llm_level, stripped)
            else:
                set_section(len(section_path) + 1 if section_path else 1, stripped)
            continue

        buf.append(stripped)

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
    # 数据全局共享：去重不再按 user_id 过滤（user_id 仅作创建人记录）

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
