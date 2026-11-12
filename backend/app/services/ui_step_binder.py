"""UI 步骤绑定服务:把自然语言测试步骤编译为 PageObject 绑定计划。

流程:
  1. 按 target_url 匹配单个 PageObject(单页面先行)
  2. 组装元素摘要 prompt,LLM 结构化输出每步的 element_key/action/confidence
  3. 确定性后处理:校验 element_key、划分 L1/L2/L3、编译 expected_results 断言
  4. 任一环节失败 → compile_status=llm_failed/no_page_object,全部降 L3

设计要点:
  - compile 是纯预览,不落库;前端修正后经 /ui-execution/run 的 ui_binding 携带
  - 不做跨页面编排;多个候选 PO 时取路径最长匹配,再并列取 id 最小
  - LLM 不参与 level 划分,assign_level 是确定性规则
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, List, Optional
from urllib.parse import urlsplit

from sqlmodel import Session, select

from app.services.element_tree import flatten_elements
from app.services.ui_binding_schemas import (
    ASSERT_ACTIONS,
    BOUND_ACTIONS,
    LEVEL_L1,
    LEVEL_L3,
    UICompileResult,
    UIBoundAssert,
    UIBoundStep,
    ElementOption,
    LLMAssertResult,
    LLMCompileResult,
    LLMBoundStep,
    assign_level,
)
from app.services.ui_steps import normalize_ui_steps, normalize_url
from db.models import PageObject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PageObject 匹配(单页面)
# ---------------------------------------------------------------------------

def _origin_of(url: str) -> str:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _path_of(url: str) -> str:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    path = parts.path or ""
    if parts.fragment:
        path = f"{path}#{parts.fragment}" if path and path != "/" else f"#{parts.fragment}"
    return path.rstrip("/") or "/"


def _po_origin(po: PageObject) -> str:
    origin = (po.site_origin or "").strip().rstrip("/")
    if origin:
        return origin
    return _origin_of(po.url or "")


def _po_path(po: PageObject) -> str:
    path = (po.page_path or "").strip()
    if path:
        if path.startswith("#"):
            return path
        if path.startswith("/"):
            return path
        return f"/{path}"
    return _path_of(po.url or "")


def match_page_object(page_objects: Iterable[PageObject], target_url: str) -> Optional[PageObject]:
    """按 origin + path 匹配单个 PageObject;路径最长优先,再按 id 最小。"""
    target_origin = _origin_of(target_url).rstrip("/")
    target_path = _path_of(target_url)
    if not target_origin:
        return None

    candidates: list[tuple[int, int, PageObject]] = []
    for po in page_objects or []:
        origin = _po_origin(po).rstrip("/")
        if origin and origin != target_origin:
            continue
        if not origin:
            # 仅有 url 的旧数据:用 url origin 再比一次
            if _origin_of(po.url or "").rstrip("/") != target_origin:
                continue
        path = _po_path(po)
        # 路径为空 = 整站对象,弱匹配
        if not path or path == "/":
            score = 0
        elif target_path == path or target_path.endswith(path) or path.endswith(target_path):
            score = len(path)
        elif path.rstrip("/") and (path.rstrip("/") in target_path or target_path in path.rstrip("/")):
            score = len(path.rstrip("/")) // 2
        else:
            continue
        candidates.append((score, -(po.id or 0), po))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def load_user_page_objects(db: Session, user_id: Optional[str]) -> list[PageObject]:
    """全部 PageObject(数据全局共享,不按用户隔离;user_id 参数保留仅为兼容)。"""
    return list(db.exec(select(PageObject).order_by("id")).all())


# ---------------------------------------------------------------------------
# 元素摘要 / 校验
# ---------------------------------------------------------------------------

def element_options(po: PageObject) -> list[ElementOption]:
    out: list[ElementOption] = []
    for el in flatten_elements(po.elements or []):
        if not isinstance(el, dict):
            continue
        if el.get("enabled") is False:
            continue
        key = str(el.get("key") or "")
        if not key or key.startswith("trigger_group/"):
            continue
        out.append(ElementOption(
            key=key,
            name=str(el.get("name") or ""),
            type=str(el.get("type") or ""),
        ))
    return out


def element_keys(po: PageObject) -> set[str]:
    return {e.key for e in element_options(po)}


def build_element_summary(po: PageObject) -> str:
    """紧凑元素摘要(每行一条),控制 token。"""
    lines: list[str] = []
    for el in flatten_elements(po.elements or []):
        if not isinstance(el, dict) or el.get("enabled") is False:
            continue
        key = str(el.get("key") or "")
        if not key or key.startswith("trigger_group/"):
            continue
        locs = [l for l in (el.get("locators") or []) if isinstance(l, dict)]
        primary = ""
        if locs:
            primary = f"{locs[0].get('strategy', '')}={locs[0].get('value', '')}"
        desc = str(el.get("description") or "").replace("\n", " ")[:80]
        lines.append(
            f"{key} | {el.get('name') or ''} | {el.get('type') or ''} | {desc} | {primary}"
        )
    return "\n".join(lines)


def normalize_nl_actions(steps: list) -> list[str]:
    actions, _ = normalize_ui_steps(steps or [])
    return actions


# ---------------------------------------------------------------------------
# 确定性后处理
# ---------------------------------------------------------------------------

def _read_int(item: Any, field: str, default: int = 0) -> int:
    raw: Any
    if isinstance(item, dict):
        raw = item.get(field)
    else:
        raw = getattr(item, field, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _to_bound_steps(
    nl_actions: list[str],
    llm_steps: list[Any],
    valid_keys: set[str],
) -> tuple[list[UIBoundStep], list[str]]:
    """校验 LLM 输出并划分 level;step_no 必须与 NL 步骤一一对齐,否则整单失败。"""
    by_no: dict[int, Any] = {}
    for item in llm_steps or []:
        no = _read_int(item, "step_no", 0)
        if no > 0:
            by_no[no] = item

    if len(nl_actions) == 0:
        return [], []
    # 数量/序号对不齐 → 由调用方判 llm_failed
    if set(by_no.keys()) != set(range(1, len(nl_actions) + 1)):
        return [], ["LLM 输出步骤与用例步骤数量/序号不一致"]

    bound: list[UIBoundStep] = []
    unmatched: list[str] = []
    for i, nl in enumerate(nl_actions, 1):
        item = by_no[i]
        if isinstance(item, dict):
            action = str(item.get("action") or "agent").strip().lower()
            ekey = item.get("element_key")
            ekey = str(ekey) if ekey else None
            try:
                conf = float(item.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            reason = str(item.get("reason") or "")
            value = item.get("value")
            value = str(value) if value is not None else None
        else:
            action = str(getattr(item, "action", "agent") or "agent").strip().lower()
            ekey_raw = getattr(item, "element_key", None)
            ekey = str(ekey_raw) if ekey_raw else None
            try:
                conf = float(getattr(item, "confidence", 0) or 0)
            except (TypeError, ValueError):
                conf = 0.0
            reason = str(getattr(item, "reason", "") or "")
            val_raw = getattr(item, "value", None)
            value = str(val_raw) if val_raw is not None else None

        if action == "navigate":
            # navigate 不绑元素
            bound.append(UIBoundStep(
                step_no=i, nl=nl, action="navigate", element_key=None,
                value=value, confidence=1.0, level=LEVEL_L1, reason="",
            ))
            continue

        if action not in BOUND_ACTIONS:
            reason = reason or f"不支持的动作已降级为 agent: {action}"
            action = "agent"

        if ekey and ekey not in valid_keys:
            unmatched.append(ekey)
            reason = (reason + "; " if reason else "") + f"元素 key 不存在: {ekey}"
            ekey = None
            conf = 0.0
            action = "agent"

        level, level_reason = assign_level(action, ekey, conf)
        if level == LEVEL_L3 and not ekey:
            action = "agent"
        bound.append(UIBoundStep(
            step_no=i,
            nl=nl,
            action=action,
            element_key=ekey if level != LEVEL_L3 else ekey,
            value=value,
            confidence=max(0.0, min(1.0, conf)),
            level=level,
            reason=reason or level_reason,
        ))
    return bound, unmatched


def _all_l3(nl_actions: list[str], reason: str) -> list[UIBoundStep]:
    return [
        UIBoundStep(
            step_no=i, nl=nl, action="agent", element_key=None,
            value=None, confidence=0.0, level=LEVEL_L3, reason=reason,
        )
        for i, nl in enumerate(nl_actions, 1)
    ]


def build_asserts_from_expected(
    expected: list,
    valid_keys: set[str],
    llm_asserts: Optional[list[Any]] = None,
) -> list[UIBoundAssert]:
    """把 expected_results 编成结构化断言;无法编的一律 L3(执行期默认跳过)。"""
    by_index: dict[int, Any] = {}
    for item in llm_asserts or []:
        idx = _read_int(item, "index", 0)
        if idx > 0:
            by_index[idx] = item

    out: list[UIBoundAssert] = []
    expected_list = [str(e) for e in (expected or []) if e is not None and str(e).strip()]
    for i, nl in enumerate(expected_list, 1):
        item = by_index.get(i)
        action = "skip"
        ekey: Optional[str] = None
        value: Optional[str] = None
        conf = 0.0
        reason = "未识别为结构化断言(默认跳过)"
        if item is not None:
            if isinstance(item, dict):
                action = str(item.get("action") or "skip").strip().lower()
                ek = item.get("element_key")
                ekey = str(ek) if ek else None
                val = item.get("value")
                value = str(val) if val is not None else None
                try:
                    conf = float(item.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0.0
                reason = str(item.get("reason") or reason)
            else:
                action = str(getattr(item, "action", "skip") or "skip").strip().lower()
                ek = getattr(item, "element_key", None)
                ekey = str(ek) if ek else None
                val = getattr(item, "value", None)
                value = str(val) if val is not None else None
                try:
                    conf = float(getattr(item, "confidence", 0) or 0)
                except (TypeError, ValueError):
                    conf = 0.0
                reason = str(getattr(item, "reason", reason) or reason)

        if action == "skip" or action not in ASSERT_ACTIONS:
            out.append(UIBoundAssert(
                index=i, nl=nl, action="skip", element_key=None, value=None,
                confidence=0.0, level=LEVEL_L3,
                reason=reason if action == "skip" else f"不支持的断言动作: {action}",
            ))
            continue
        if ekey and ekey not in valid_keys:
            out.append(UIBoundAssert(
                index=i, nl=nl, action="skip", element_key=None, value=None,
                confidence=0.0, level=LEVEL_L3, reason=f"元素 key 不存在: {ekey}",
            ))
            continue
        if not ekey and action == "assert_visible":
            # 页面级可见断言没有元素 → 跳过
            out.append(UIBoundAssert(
                index=i, nl=nl, action="skip", element_key=None, value=value,
                confidence=0.0, level=LEVEL_L3, reason="assert_visible 需要元素",
            ))
            continue
        # assert_text 允许无元素(页面级 contains 由 value 承载,element_key 可空) → 但执行层要求元素时降级
        level, level_reason = assign_level(action, ekey or "page", conf)
        if action == "assert_text" and not ekey:
            # 页面级文本断言:仍标 L1,执行时用 page.content 匹配
            level = LEVEL_L1 if conf >= 0.75 else (level if conf >= 0.5 else LEVEL_L3)
        if conf < 0.5:
            level = LEVEL_L3
        out.append(UIBoundAssert(
            index=i, nl=nl, action=action, element_key=ekey, value=value,
            confidence=max(0.0, min(1.0, conf)),
            level=level,
            reason="" if level != LEVEL_L3 else (reason if reason != "未识别为结构化断言(默认跳过)" else level_reason),
        ))
    return out


def _fallback_compile(
    nl_actions: list[str],
    expected: list,
    *,
    status: str,
    message: str,
    site_origin: str = "",
    page_path: str = "",
    po: Optional[PageObject] = None,
) -> UICompileResult:
    return UICompileResult(
        page_object_id=po.id if po else None,
        page_object_name=(po.name if po else ""),
        elements=element_options(po) if po else [],
        steps=_all_l3(nl_actions, message),
        asserts=[
            UIBoundAssert(
                index=i, nl=str(e), action="skip", level=LEVEL_L3,
                reason="编译未完成,默认跳过",
            )
            for i, e in enumerate([str(x) for x in (expected or []) if str(x).strip()], 1)
        ],
        unmatched_elements=[],
        compile_status=status,
        site_origin=site_origin,
        page_path=page_path,
        message=message,
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

COMPILE_SYSTEM_PROMPT = """你是 UI 测试绑定专家。把自然语言测试步骤映射到给定页面的元素上。

规则:
1. element_key 必须严格来自「可用元素」列表的 key,一个字都不能改;匹配不到就填 null。
2. action 只能是: click / fill / check / uncheck / select_option / press / wait_for / navigate / agent。
   - 打开页面/导航类步骤用 navigate(element_key=null)
   - 输入类需要 value;select_option 需要 value
   - 无法可靠绑定时 action 必须是 agent, element_key=null
3. confidence 表示你对该绑定的把握(0~1);模糊匹配给 0.5~0.7,完全确定给 0.8~1.0。
4. step_no 必须从 1 开始连续,与输入步骤一一对应,数量一致。
5. 若步骤提到的业务元素不在可用列表,把该描述写入 unmatched_elements。
6. value 中的字面量从步骤文本提取(如「输入 admin」→ value="admin」);提不出就 null。
7. 简洁输出,不要解释。"""

COMPILE_USER_PROMPT = """## 可用元素(key | name | type | description | 主定位器)
{elements}

## 测试步骤(JSON 数组,1-based step_no 对应下标+1)
{steps}

## 预期结果(仅参考,不输出)
{expected}

请输出每一步的绑定结果。"""

ASSERT_SYSTEM_PROMPT = """你是 UI 测试断言专家。把预期结果映射到页面元素上的结构化断言。

规则:
1. 可输出 action: assert_text / assert_visible / skip。
2. assert_visible 必须给出存在的 element_key;assert_text 可绑元素,也可 element_key=null 表示页面级文本包含(value 填期望文本)。
3. 看不懂/无法自动化/与 UI 无关的预期 → action=skip。
4. index 必须与预期结果 1-based 序号一致,数量一致。
5. confidence 0~1;value 为期望文本(尽量用预期里的原文片段)。
6. element_key 必须来自可用元素列表,否则 null 并 skip。"""

ASSERT_USER_PROMPT = """## 可用元素(key | name | type | description | 主定位器)
{elements}

## 预期结果(JSON 数组)
{expected}

请输出断言绑定结果。"""


# ---------------------------------------------------------------------------
# 编译入口
# ---------------------------------------------------------------------------

def _build_llm():
    from config import config_manager
    from utils.llm_factory import build_chat_model

    return build_chat_model(
        api_key=config_manager.get_api_key(),
        api_base_url=config_manager.config.get("api_base_url"),
        api_model=config_manager.config.get("api_model"),
        max_tokens=8192,
        temperature=0,
    )


async def _llm_compile_steps(
    nl_actions: list[str],
    expected: list,
    element_summary: str,
    model=None,
) -> tuple[Optional[LLMCompileResult], Optional[LLMAssertResult], str]:
    """一次编译调用(操作步)+ 一次断言调用;返回 (steps, asserts, error)。"""
    from utils.model_utils import _structured_output_robust

    model = model or _build_llm()
    user = COMPILE_USER_PROMPT.format(
        elements=element_summary or "(无可用元素)",
        steps=json.dumps(
            [{"step_no": i, "text": t} for i, t in enumerate(nl_actions, 1)],
            ensure_ascii=False,
            indent=2,
        ),
        expected=json.dumps([str(e) for e in (expected or [])], ensure_ascii=False),
    )
    messages = [
        {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    raw_steps = await _structured_output_robust(model, LLMCompileResult, messages)
    if raw_steps is None:
        return None, None, "步骤绑定 LLM 调用失败"

    result: LLMCompileResult
    if isinstance(raw_steps, LLMCompileResult):
        result = raw_steps
    else:
        try:
            result = LLMCompileResult.model_validate(raw_steps)
        except Exception as exc:  # noqa: BLE001
            return None, None, f"步骤绑定输出解析失败: {exc}"

    assert_result: Optional[LLMAssertResult] = None
    expected_list = [str(e) for e in (expected or []) if str(e).strip()]
    if expected_list:
        try:
            amessages = [
                {"role": "system", "content": ASSERT_SYSTEM_PROMPT},
                {"role": "user", "content": ASSERT_USER_PROMPT.format(
                    elements=element_summary or "(无可用元素)",
                    expected=json.dumps(
                        [{"index": i, "text": t} for i, t in enumerate(expected_list, 1)],
                        ensure_ascii=False, indent=2,
                    ),
                )},
            ]
            raw_a = await _structured_output_robust(model, LLMAssertResult, amessages)
            if isinstance(raw_a, LLMAssertResult):
                assert_result = raw_a
            elif isinstance(raw_a, dict):
                assert_result = LLMAssertResult.model_validate(raw_a)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[UIBind] 断言编译失败(跳过结构化断言): {exc}")

    return result, assert_result, ""


async def compile_ui_binding(
    db: Session,
    *,
    site_origin: str,
    page_path: str,
    testcase_steps: list,
    expected_results: list,
    user_id: Optional[str] = None,
    model=None,
) -> UICompileResult:
    """编译入口:匹配 PO → LLM 绑定 → 确定性后处理。纯预览,不写库。"""
    from app.services.ui_steps import build_target_url

    origin = (site_origin or "").strip()
    path = (page_path or "").strip()
    target_url = normalize_url(build_target_url(origin, path)) if origin else ""

    nl_actions = normalize_nl_actions(testcase_steps)
    if not nl_actions:
        return _fallback_compile(
            [], expected_results, status="llm_failed",
            message="用例无可执行的 UI 步骤",
            site_origin=origin, page_path=path,
        )

    if not target_url:
        return _fallback_compile(
            nl_actions, expected_results, status="no_page_object",
            message="站点地址为空,无法匹配页面对象;将整单 Agent 执行",
            site_origin=origin, page_path=path,
        )

    po = match_page_object(load_user_page_objects(db, user_id), target_url)
    if po is None:
        return _fallback_compile(
            nl_actions, expected_results, status="no_page_object",
            message="未匹配到页面对象,将整单 Agent 执行",
            site_origin=origin, page_path=path,
        )

    valid_keys = element_keys(po)
    summary = build_element_summary(po)

    try:
        steps_llm, asserts_llm, err = await _llm_compile_steps(
            nl_actions, expected_results, summary, model=model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[UIBind] 编译异常: {exc}")
        return _fallback_compile(
            nl_actions, expected_results, status="llm_failed",
            message=f"绑定编译失败: {exc}",
            site_origin=origin, page_path=path, po=po,
        )

    if steps_llm is None:
        return _fallback_compile(
            nl_actions, expected_results, status="llm_failed",
            message=err or "绑定编译失败",
            site_origin=origin, page_path=path, po=po,
        )

    llm_step_list = list(steps_llm.steps or [])
    bound, unmatched = _to_bound_steps(nl_actions, llm_step_list, valid_keys)
    if len(bound) != len(nl_actions):
        return _fallback_compile(
            nl_actions, expected_results, status="llm_failed",
            message=(unmatched[0] if unmatched else "绑定结果与步骤数量不一致"),
            site_origin=origin, page_path=path, po=po,
        )

    llm_assert_list = list((asserts_llm.asserts if asserts_llm else []) or [])
    asserts = build_asserts_from_expected(expected_results, valid_keys, llm_assert_list)

    return UICompileResult(
        page_object_id=po.id,
        page_object_name=po.name or "",
        elements=element_options(po),
        steps=bound,
        asserts=asserts,
        unmatched_elements=sorted(set(unmatched)),
        compile_status="ok",
        site_origin=origin,
        page_path=path,
        message=f"已匹配页面对象「{po.name or po.id}」,共 {len(bound)} 步",
    )


def validate_binding_element_keys(binding: dict, po: PageObject) -> list[str]:
    """校验前端回传绑定的 element_key 均存在于 PO;返回错误列表(空=通过)。"""
    valid = element_keys(po)
    errors: list[str] = []
    for group in ("steps", "asserts"):
        for item in (binding.get(group) or []):
            if not isinstance(item, dict):
                errors.append(f"{group} 中存在非法项")
                continue
            ekey = item.get("element_key")
            if ekey and str(ekey) not in valid:
                errors.append(f"元素不存在: {ekey}")
    return errors


def normalize_incoming_binding(raw: Any) -> dict:
    """把前端/请求里的绑定归一为可落库 dict。"""
    if not isinstance(raw, dict):
        return {}
    steps = []
    for s in raw.get("steps") or []:
        if isinstance(s, dict):
            steps.append(UIBoundStep.model_validate(s).model_dump())
    asserts = []
    for a in raw.get("asserts") or []:
        if isinstance(a, dict):
            asserts.append(UIBoundAssert.model_validate(a).model_dump())
    out = {
        "page_object_id": raw.get("page_object_id"),
        "steps": steps,
        "asserts": asserts,
        "compile_status": str(raw.get("compile_status") or "ok"),
        "site_origin": str(raw.get("site_origin") or ""),
        "page_path": str(raw.get("page_path") or ""),
        "message": str(raw.get("message") or ""),
    }
    return out
