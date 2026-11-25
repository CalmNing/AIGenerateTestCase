"""AI 用例对话写操作工具

供 /api/chat 在 testcase_optimize 模式下挂载的 LangChain 工具：
- get_testcase_detail  按名称查询用例详情（读，testcase:read）
- update_testcase      更新用例内容字段（写，testcase:update）
- delete_testcases     批量删除用例（写，testcase:delete）

权限在工具内部自检（chat 路由注册时无权限依赖）：无权限时返回中文拒绝文案，
让模型向用户解释，而不是抛 403 中断 SSE 流。

与 PUT /api/testcases 路由的差异：工具更新走字段白名单，不触碰
human_edited/reviewed_at —— AI 的修改不算人工高质量正样本（B7）。
删除沿用表格删除的业务保护：已执行（非 NOT_RUN）的用例不允许删除，
批量删除任一条不满足即整批拒绝。
"""
import logging
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel, Field
from sqlmodel import Session, select

from db.db import engine
from db.models import TestCase, StatusValue

logger = logging.getLogger(__name__)

_LEVEL_LABELS = {1: "功能", 2: "边界", 3: "异常", 4: "场景"}

_UPDATE_ALLOWED_FIELDS = {
    "case_name",
    "case_level",
    "preset_conditions",
    "steps",
    "expected_results",
    "module_id",
}

_LIST_FIELDS = {"preset_conditions", "steps", "expected_results"}


def _has_permission(current_user, permission: str) -> bool:
    if current_user is None:
        return False
    from app.permissions import get_user_permissions

    return permission in get_user_permissions(current_user)


def _deny(permission: str) -> str:
    return f"权限不足：执行该操作需要「{permission}」权限，请联系管理员开通。"


def _normalize_list_field(value) -> Optional[list]:
    """列表字段归一化：元素统一转字符串；非法输入返回 None。"""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            action = str(item.get("action") or item.get("step") or "").strip()
            expected = str(item.get("expected") or item.get("result") or "").strip()
            if action and expected:
                result.append(f"{action}（预期：{expected}）")
            elif action:
                result.append(action)
            else:
                result.append(str(item))
        elif item is None:
            result.append("")
        else:
            result.append(str(item))
    return result


def _format_testcase(tc: TestCase, with_detail: bool = False) -> str:
    level = _LEVEL_LABELS.get(tc.case_level, tc.case_level)
    status_label = {
        StatusValue.NOT_RUN.value: "未执行",
        StatusValue.PASSED.value: "已通过",
        StatusValue.FAILED.value: "未通过",
    }.get(tc.status, tc.status)
    head = f"[id={tc.id}] {tc.case_name}（级别：{level}；状态：{status_label}）"
    if not with_detail:
        return head
    lines = [head]
    if tc.preset_conditions:
        lines.append("前置条件：" + "；".join(str(i) for i in tc.preset_conditions))
    lines.append("测试步骤：")
    for idx, step in enumerate(tc.steps, start=1):
        lines.append(f"  {idx}. {step}")
    lines.append("预期结果：")
    for idx, item in enumerate(tc.expected_results, start=1):
        lines.append(f"  {idx}. {item}")
    return "\n".join(lines)


def build_testcase_tools(
    testcase_session_id: int,
    current_user,
    testcase_module_id: Optional[int] = None,
) -> list:
    """构建 AI 用例对话写操作工具。

    工具闭包捕获当前会话 ID、当前模块 ID（新增用例的归属）、当前用户（权限主体）。
    testcase_module_id 为 None 表示前端处于「全部模块」视图；指定模块时
    create_testcase 的 module_id 入参必须与之一致，防止用例落到当前筛选之外。
    """
    from langchain_core.tools import StructuredTool

    def _get_testcase_detail(testcase_name: str) -> str:
        try:
            with Session(engine) as db:
                statement = select(TestCase).where(TestCase.session_id == testcase_session_id)
                candidates = db.exec(statement).all()
                exact = [tc for tc in candidates if tc.case_name == testcase_name]
                if exact:
                    return _format_testcase(exact[0], with_detail=True)
                fuzzy = [tc for tc in candidates if testcase_name in tc.case_name or tc.case_name in testcase_name]
                if fuzzy:
                    listing = "\n".join(_format_testcase(tc) for tc in fuzzy[:10])
                    return "未找到精确匹配，相近用例：\n" + listing
                return f"当前会话中不存在名称包含「{testcase_name}」的用例。"
        except Exception as e:  # noqa: BLE001
            logger.error("查询用例详情失败: %s", e)
            return "查询用例详情失败，请稍后重试。"

    def _update_testcase(testcase_id: int, patch: dict) -> str:
        if not _has_permission(current_user, "testcase:update"):
            return _deny("testcase:update")
        fields = {k: v for k, v in (patch or {}).items() if k in _UPDATE_ALLOWED_FIELDS}
        if not fields:
            return "未提供有效修改字段。可修改：case_name（名称）、case_level（1=功能 2=边界 3=异常 4=场景）、preset_conditions、steps、expected_results、module_id。"
        if "case_level" in fields:
            level = fields["case_level"]
            if not isinstance(level, int) or not 1 <= level <= 4:
                return "case_level 必须是 1~4 的整数（1=功能 2=边界 3=异常 4=场景）。"
        for field in _LIST_FIELDS & set(fields):
            normalized = _normalize_list_field(fields[field])
            if normalized is None:
                return f"{field} 必须是字符串数组。"
            fields[field] = normalized
        if "module_id" in fields:
            new_module = fields["module_id"]
            if new_module is not None and not isinstance(new_module, int):
                return "module_id 必须是整数或 null。"
            if (
                testcase_module_id is not None
                and new_module is not None
                and new_module != testcase_module_id
            ):
                return (
                    f"module_id={new_module} 与当前上下文模块 id={testcase_module_id} 不一致；"
                    "不能把用例移出当前筛选的模块。"
                )
        if "case_name" in fields and not str(fields["case_name"]).strip():
            return "case_name 不能为空。"
        try:
            with Session(engine) as db:
                tc = db.get(TestCase, testcase_id)
                if not tc or tc.session_id != testcase_session_id:
                    return f"用例 id={testcase_id} 不存在或不属于当前会话，请先用 get_testcase_detail 确认 id。"
                changed = []
                for key, value in fields.items():
                    old = getattr(tc, key)
                    if old != value:
                        changed.append(f"{key}: {old!r} → {value!r}")
                        setattr(tc, key, value)
                if not changed:
                    return "内容没有任何变化，未执行更新。"
                db.add(tc)
                db.commit()
                logger.info("AI 对话更新用例 id=%s by=%s: %s", testcase_id,
                            getattr(current_user, "user_id", None), "; ".join(changed))
                return f"已更新用例 [id={testcase_id}] {tc.case_name}：\n" + "\n".join(changed)
        except Exception as e:  # noqa: BLE001
            logger.error("AI 更新用例失败 id=%s: %s", testcase_id, e)
            return "更新用例失败，请稍后重试。"

    def _delete_testcases(testcase_ids: list[int]) -> str:
        if not _has_permission(current_user, "testcase:delete"):
            return _deny("testcase:delete")
        ids = [i for i in (testcase_ids or []) if isinstance(i, int)]
        if not ids:
            return "请提供要删除的用例 id 列表。"
        try:
            with Session(engine) as db:
                found = db.exec(
                    select(TestCase).where(
                        TestCase.id.in_(ids),
                        TestCase.session_id == testcase_session_id,
                    )
                ).all()
                found_ids = {tc.id for tc in found}
                missing = [i for i in ids if i not in found_ids]
                if missing:
                    return f"以下用例不存在或不属于当前会话，整批未删除：{missing}。"
                executed = [tc for tc in found if tc.status != StatusValue.NOT_RUN.value]
                if executed:
                    names = "、".join(f"[id={tc.id}] {tc.case_name}（{tc.status}）" for tc in executed)
                    return f"存在已执行的用例，整批未删除：{names}。已执行用例不允许删除。"
                for tc in found:
                    db.delete(tc)
                db.commit()
                logger.info("AI 对话删除用例 ids=%s by=%s", ids, getattr(current_user, "user_id", None))
                names = "、".join(f"[id={tc.id}] {tc.case_name}" for tc in found)
                return f"已删除 {len(found)} 条用例：{names}。"
        except Exception as e:  # noqa: BLE001
            logger.error("AI 删除用例失败 ids=%s: %s", ids, e)
            return "删除用例失败，请稍后重试。"

    class GetTestcaseDetailArgs(PydanticBaseModel):
        testcase_name: str = Field(description="用例名称（支持模糊匹配）")

    class UpdateTestcaseArgs(PydanticBaseModel):
        testcase_id: int = Field(description="要修改的用例 id")
        patch: dict = Field(description="要修改的字段字典：case_name/case_level/preset_conditions/steps/expected_results/module_id")

    class DeleteTestcasesArgs(PydanticBaseModel):
        testcase_ids: list[int] = Field(description="要删除的用例 id 数组")

    class CreateTestcaseArgs(PydanticBaseModel):
        case_name: str = Field(description="用例名称")
        steps: list[str] = Field(description="测试步骤（有序字符串数组）")
        expected_results: list[str] = Field(description="预期结果（字符串数组，与步骤对应）")
        preset_conditions: Optional[list[str]] = Field(default=None, description="前置条件（可为空）")
        case_level: int = Field(default=1, description="用例级别：1=功能 2=边界 3=异常 4=场景")
        module_id: int = Field(description="所属模块 id（必填）。上下文【模块范围】标注了 [module=N] 时必须传 N")

    def _create_testcase(
        case_name: str,
        steps: list[str],
        expected_results: list[str],
        module_id: Optional[int] = None,
        preset_conditions: Optional[list[str]] = None,
        case_level: int = 1,
    ) -> str:
        if not _has_permission(current_user, "testcase:create"):
            return _deny("testcase:create")
        if not str(case_name).strip():
            return "case_name 不能为空。"
        if not isinstance(case_level, int) or not 1 <= case_level <= 4:
            return "case_level 必须是 1~4 的整数（1=功能 2=边界 3=异常 4=场景）。"
        # module_id 必填且必须与上下文模块一致（前端处于「全部模块」视图时无此约束）
        if not isinstance(module_id, int) or module_id <= 0:
            return (
                "module_id 必填且必须是正整数。"
                + (f"当前上下文已限定模块 id={testcase_module_id}，直接传该值。" if testcase_module_id else "")
            )
        if testcase_module_id is not None and module_id != testcase_module_id:
            return (
                f"module_id={module_id} 与当前上下文模块 id={testcase_module_id} 不一致；"
                "新增用例必须落在当前筛选的模块内，请传当前模块的 id。"
            )
        norm_steps = _normalize_list_field(steps)
        norm_expected = _normalize_list_field(expected_results)
        norm_pre = _normalize_list_field(preset_conditions if preset_conditions is not None else [])
        if norm_steps is None or not norm_steps:
            return "steps 必须是非空字符串数组。"
        if norm_expected is None or not norm_expected:
            return "expected_results 必须是非空字符串数组。"
        try:
            with Session(engine) as db:
                tc = TestCase(
                    case_name=str(case_name).strip(),
                    case_level=case_level,
                    preset_conditions=norm_pre,
                    steps=norm_steps,
                    expected_results=norm_expected,
                    session_id=testcase_session_id,
                    module_id=module_id,
                    status=StatusValue.NOT_RUN.value,
                )
                db.add(tc)
                db.commit()
                db.refresh(tc)
                logger.info("AI 对话新增用例 id=%s name=%s by=%s", tc.id, tc.case_name,
                            getattr(current_user, "user_id", None))
                return (
                    f"已创建用例 [id={tc.id}] {tc.case_name}（级别：{_LEVEL_LABELS.get(tc.case_level, tc.case_level)}；状态：未执行）。"
                    f"包含 {len(tc.steps)} 个步骤、{len(tc.expected_results)} 个预期结果。"
                )
        except Exception as e:  # noqa: BLE001
            logger.error("AI 新增用例失败 name=%s: %s", case_name, e)
            return "新增用例失败，请稍后重试。"

    return [
        StructuredTool(
            name="get_testcase_detail",
            description="按用例名称查询当前会话内测试用例的详情（id、级别、状态、前置条件、步骤、预期结果）。修改或删除用例前必须先调用它确认用例 id。",
            func=_get_testcase_detail,
            args_schema=GetTestcaseDetailArgs,
        ),
        StructuredTool(
            name="update_testcase",
            description="更新当前会话内一条测试用例的内容字段。参数：testcase_id（用例 id）、patch（要修改的字段字典，可含 case_name/case_level/preset_conditions/steps/expected_results/module_id）。仅修改用户明确要求的内容。",
            func=_update_testcase,
            args_schema=UpdateTestcaseArgs,
        ),
        StructuredTool(
            name="create_testcase",
            description="在当前会话内新增一条测试用例。参数：case_name、steps（步骤数组）、expected_results（预期数组）、preset_conditions（可选）、case_level（1=功能 2=边界 3=异常 4=场景，默认 1）、module_id（必填，传上下文【模块范围】标注的 [module=N]）。用于把讨论中确定的补充用例直接落库；同一用例内容也可以走『### 建议：』格式由用户一键引入。",
            func=_create_testcase,
            args_schema=CreateTestcaseArgs,
        ),
        StructuredTool(
            name="delete_testcases",
            description="批量删除当前会话内未执行（NOT_RUN）的测试用例。参数：testcase_ids（用例 id 数组）。已执行用例无法删除；必须用户明确要求删除时才可调用。",
            func=_delete_testcases,
            args_schema=DeleteTestcasesArgs,
        ),
    ]


def load_testcase_write_tools(
    mode: str,
    testcase_session_id: Optional[int],
    current_user,
    testcase_module_id: Optional[int] = None,
) -> list:
    """按对话模式门控加载用例写工具。

    仅 testcase_optimize 模式且携带会话 ID 时挂载；其他模式返回空列表。
    testcase_module_id 是前端当前模块筛选，绑定给 create/update 工具做归属约束。
    """
    if mode != "testcase_optimize" or not testcase_session_id:
        return []
    tools = build_testcase_tools(int(testcase_session_id), current_user, testcase_module_id)
    if tools:
        logger.info(
            "已挂载 %d 个用例操作工具（session=%s module=%s）",
            len(tools), testcase_session_id, testcase_module_id,
        )
    return tools
