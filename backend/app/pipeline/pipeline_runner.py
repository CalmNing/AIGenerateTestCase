"""
Pipeline 编排器 — 串联 4 个阶段，管理进度和错误处理
支持功能测试和接口测试两种模式
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable

from sqlmodel import Session, select

from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from app.pipeline.test_case_optimizer import TestCaseOptimizer
from app.routes.skills import get_skill_body
from app.pipeline.schemas import RequirementDedupResult
from utils.document_prefetch import (
    FEISHU_URL_RE as _FEISHU_URL_RE,
    LANHU_URL_RE as _LANHU_URL_RE,
    prefetch_document,
)

logger = logging.getLogger(__name__)

from app.services.api_consistency import check_testcase_consistency, score_confidence
from utils.helpers import _upsert_history_prompt
from utils.model_utils import _structured_output_robust


def _compute_accuracy(case_data: dict, endpoint_index_to_id: dict, session) -> "tuple[dict, float, str]":
    """计算用例一致性与置信度，返回 (report_dict, score, level)。无 LLM。"""
    from db.models import ApiEndpoint
    endpoints = {}
    for ordinal, eid in endpoint_index_to_id.items():
        ep = session.get(ApiEndpoint, eid)
        if ep is not None:
            endpoints[eid] = ep
    report = check_testcase_consistency(case_data, endpoint_index_to_id, endpoints)
    score, level = score_confidence(report, case_data)
    return report, score, level


def _resolve_keep_indices(keep_indices: Any, n: int) -> List[int]:
    """把 LLM 返回的保留编号解析为合法的 0-based 去重索引列表（自动去重、升序）。

    约定模型以 0-based 返回（提示词已显式标注编号从 0 开始），此处仅做范围校验。
    """
    if not keep_indices:
        return []
    resolved: set = set()
    for i in keep_indices:
        if isinstance(i, int) and 0 <= i < n:
            resolved.add(i)
    return sorted(resolved)


def _assign_requirement_order(
    new_points: List[dict],
    existing_points: List[Any],
    module_id: Optional[int],
) -> List[dict]:
    """为待新建需求点分配 order_index（在已有最大 order_index 基础上续接）。"""
    max_order = -1
    for p in existing_points:
        oi = getattr(p, "order_index", None)
        if oi is not None and oi > max_order:
            max_order = oi
    result: List[dict] = []
    for idx, rp in enumerate(new_points):
        result.append({
            "content": rp.get("content", ""),
            "priority": rp.get("priority", "medium"),
            "order_index": max_order + 1 + idx,
            "module_id": module_id,
        })
    return result


async def _dedup_requirement_points_by_llm(
    new_points: List[dict],
    existing_points: List[Any],
    model: Any,
    module_id: Optional[int],
) -> List[dict]:
    """基于 LLM 语义比对，返回当前模块下需要新建的需求点列表。

    - 范围：同一 module_id（调用方已按 session + module 过滤 existing_points）
    - 由 LLM 判断新候选是否与已有需求点语义重复，仅保留未覆盖的
    - LLM 异常/返回空时保守保留全部新候选，避免误删导致覆盖丢失
    """
    # 无已有需求点则全部保留
    if not existing_points:
        return _assign_requirement_order(new_points, existing_points, module_id)

    existing_contents = [
        getattr(p, "content", "") for p in existing_points
        if getattr(p, "content", None)
    ]
    candidates = [rp.get("content", "") for rp in new_points]
    # 候选全部为空则无需创建
    if not any(candidates):
        return []

    existing_text = "\n".join(
        f"{i + 1}. {c}" for i, c in enumerate(existing_contents)
    ) or "（无，当前模块暂无需求点）"
    candidates_text = "\n".join(
        f"[{i}] {c}" for i, c in enumerate(candidates)
    )

    system_prompt = _REQ_DEDUP_SYSTEM
    user_prompt = (
        f"## 当前模块已有需求点（共 {len(existing_contents)} 条，已存在，无需再次创建）\n"
        f"{existing_text}\n\n"
        f"## 本次新生成的需求点候选（共 {len(candidates)} 条，编号从 0 开始）\n"
        f"{candidates_text}\n\n"
        "请返回需要【保留】的候选编号列表（0-based，即上面 [0]、[1] 对应的数字）。"
        "若候选全部都是已有需求点的重复，返回空列表。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        result = await _structured_output_robust(
            model, RequirementDedupResult, messages, config={}
        )
    except Exception as e:
        logger.warning("需求点 LLM 去重异常，保守保留全部新需求点: %s", e)
        return _assign_requirement_order(new_points, existing_points, module_id)

    if result is None:
        logger.warning("需求点 LLM 去重返回空，保守保留全部新需求点")
        return _assign_requirement_order(new_points, existing_points, module_id)

    keep_indices = getattr(result, "keep_indices", None) or []
    valid = _resolve_keep_indices(keep_indices, len(new_points))
    if not valid and keep_indices:
        # 模型返回了索引但全部越界（异常），保守保留全部
        logger.warning("需求点 LLM 去重：返回索引全部越界，保守保留全部新需求点")
        return _assign_requirement_order(new_points, existing_points, module_id)

    to_keep = [new_points[i] for i in valid]
    return _assign_requirement_order(to_keep, existing_points, module_id)


_REQ_DEDUP_SYSTEM = """你是一名资深的测试用例需求去重专家。给定【当前模块已有需求点】与【本次新生成的需求点候选】，请判断每个候选需求点是否在语义上与已有需求点重复。

判定标准：
- 若候选与任一已有需求点含义相同、或只是换了一种说法、或已被已有需求点完全覆盖，则视为重复，不应保留。
- 若候选是已有需求点未覆盖的全新内容，则应保留。
- 候选之间若彼此语义重复，也只保留其中一个（取最先出现的编号）。

只依据给出的文本判断，不要臆造。返回需要保留的候选编号（0-based）。"""


# 内存中的 pipeline 任务状态存储
_pipeline_tasks: Dict[str, Dict[str, Any]] = {}

# 并发度：同时进行的 LLM 调用上限，避免 API 限流
_PIPELINE_CONCURRENCY = 4


def get_pipeline_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取 pipeline 任务状态"""
    return _pipeline_tasks.get(task_id)


def _normalize_key_value_aliases(items: Any) -> Any:
    """Normalize key/value rows so the executor can read parameter locations."""
    if not isinstance(items, list):
        return items
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        row = dict(item)
        if "in" not in row and row.get("in_field") is not None:
            row["in"] = row.get("in_field")
        row.pop("in_field", None)
        normalized.append(row)
    return normalized


def _resolve_pipeline_endpoint_id(step: dict, endpoint_index_to_id: Dict[int, int]) -> int | None:
    """Resolve endpoint_id from either endpoint_id or pipeline endpoint_ref."""
    eid = step.get("endpoint_id")
    if eid:
        return int(eid)
    ref = step.get("endpoint_ref")
    if ref and endpoint_index_to_id:
        return endpoint_index_to_id.get(int(ref))
    return None


def _is_pipeline_api_call_step(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    return (
        step.get("type") == "api_call"
        or step.get("endpoint_id") is not None
        or step.get("endpoint_ref") is not None
    )


def _normalize_pipeline_api_step(step: dict, endpoint_index_to_id: Dict[int, int]) -> dict:
    """Normalize an LLM-produced pipeline API step before persistence/execution."""
    normalized = dict(step)
    if normalized.get("endpoint_id") or normalized.get("endpoint_ref"):
        normalized.setdefault("type", "api_call")
    eid = _resolve_pipeline_endpoint_id(normalized, endpoint_index_to_id)
    if eid:
        normalized["endpoint_id"] = eid
        normalized.pop("endpoint_ref", None)
    for key in ("headers", "parameters", "variables"):
        normalized[key] = _normalize_key_value_aliases(normalized.get(key))
    return normalized


def _normalize_pipeline_api_steps(steps: Any, endpoint_index_to_id: Dict[int, int]) -> Any:
    if not isinstance(steps, list):
        return steps
    return [
        _normalize_pipeline_api_step(step, endpoint_index_to_id)
        if isinstance(step, dict) else step
        for step in steps
    ]


def _collect_pipeline_endpoint_ids(test_cases: list, endpoint_index_to_id: Dict[int, int]) -> set[int]:
    endpoint_ids: set[int] = set()
    for tc in test_cases:
        for step in (tc.steps or []):
            if _is_pipeline_api_call_step(step):
                eid = _resolve_pipeline_endpoint_id(step, endpoint_index_to_id)
                if eid:
                    endpoint_ids.add(eid)
        for pc in (tc.preset_conditions or []):
            if _is_pipeline_api_call_step(pc):
                eid = _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id)
                if eid:
                    endpoint_ids.add(eid)
    return endpoint_ids


def _build_pipeline_scenario_steps(testcase, endpoint_map: dict, endpoint_index_to_id: Dict[int, int]) -> list[dict]:
    """Build executable ApiScenario steps from a pipeline-generated testcase."""
    api_steps = []
    for pc in (testcase.preset_conditions or []):
        if _is_pipeline_api_call_step(pc) and _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id):
            api_steps.append(("preset", pc))
    for step in (testcase.steps or []):
        if _is_pipeline_api_call_step(step) and _resolve_pipeline_endpoint_id(step, endpoint_index_to_id):
            api_steps.append(("step", step))

    scenario_steps = []
    for step_type, raw_step in api_steps:
        step_copy = _normalize_pipeline_api_step(raw_step, endpoint_index_to_id)
        eid = step_copy.get("endpoint_id")
        ep = endpoint_map.get(eid) if eid else None
        if ep:
            step_copy.setdefault("method", ep.method)
            step_copy.setdefault("path", ep.path)
            step_copy.setdefault("url", ep.url or ep.path)
            step_copy.setdefault("endpoint_name", ep.name)
            step_copy.setdefault("enabled", True)
            step_copy.setdefault("continue_on_failure", True)
        if step_type == "preset":
            step_copy["is_preset"] = True
        scenario_steps.append(step_copy)
    return scenario_steps


def merge_entities_by_position(
    existing: list,
    generated: list,
    *,
    max_keep: int | None = None,
) -> tuple[list, list, list]:
    """
    按位置顺序匹配，计算就地更新动作。

    Args:
        existing: 已存在的实体列表（按稳定顺序，如按 id 升序）。
        generated: 新生成的实体数据列表（可能为含空 dict 的占位）。
        max_keep: 最多保留的生成数量（None 表示全部保留）。

    Returns:
        (to_update, to_create, to_delete)
        - to_update: [(existing[i], generated[i])] 需要更新的 (实体, 新数据) 对
        - to_create: [generated[i]] 需要新建的数据（i >= len(existing)）
        - to_delete: [existing[i]] 多余的旧实体（i >= 生成数量）
    保留 generated 中位置 < len(existing) 但内容为空的占位项（跳过，不更新也不删）。
    """
    if max_keep is not None:
        generated = generated[:max_keep]

    to_update: list = []
    to_create: list = []
    to_delete: list = []

    for i, gen in enumerate(generated):
        is_empty = (gen is None) or (isinstance(gen, dict) and not gen)
        if i < len(existing):
            if is_empty:
                # 占位空项：既不更新也不删除已有实体（保留现状）
                continue
            to_update.append((existing[i], gen))
        else:
            if is_empty:
                continue
            to_create.append(gen)

    # 多余旧实体删除
    for i in range(len(generated), len(existing)):
        to_delete.append(existing[i])

    return to_update, to_create, to_delete


def _is_empty_case(tc: "TestCase") -> bool:
    return not (tc.case_name or tc.steps)


def _is_empty_tp(tp: "TestPoint") -> bool:
    return not tp.content


def _cascade_delete_test_point(session, tp):
    """级联删除测试点及其下所有用例 + 孤儿场景（提交由调用方统一处理）。"""
    from db.models import TestCase, ApiScenario
    cases = session.exec(select(TestCase).where(TestCase.test_point_id == tp.id)).all()
    for c in cases:
        if c.scenario_id:
            sc = session.get(ApiScenario, c.scenario_id)
            if sc:
                session.delete(sc)
        session.delete(c)
    session.delete(tp)


class PipelineRunner:
    """4阶段 Pipeline 编排器（支持功能测试和接口测试）"""

    def __init__(
        self,
        db_session: Session,
        api_key: str,
        api_base_url: str = "https://api.deepseek.com",
        api_model: str = "deepseek-v4-flash",
        api_proxy_url: Optional[str] = None,
        mcp_configs: Optional[List[Dict[str, Any]]] = None,
    ):
        self.db = db_session
        self.api_key = api_key
        self.api_base_url = api_base_url
        self.api_model = api_model
        self.api_proxy_url = api_proxy_url
        self.mcp_configs = mcp_configs or []

        # 初始化 4 个 agent（共享同一模型配置）
        model_kwargs = dict(
            api_key=api_key,
            api_base_url=api_base_url,
            api_model=api_model,
            api_proxy_url=api_proxy_url,
        )
        self.splitter = RequirementSplitter(**model_kwargs)
        self.tp_generator = TestPointGenerator(**model_kwargs)
        self.tc_designer = TestCaseDesigner(**model_kwargs)
        self.optimizer = TestCaseOptimizer(**model_kwargs)

    def _create_task(self) -> str:
        """创建 pipeline 任务，返回 task_id"""
        task_id = str(uuid.uuid4())
        _pipeline_tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "current_phase": 0,
            "total_phases": 4,
            "phase_name": "初始化",
            "progress": 0,
            "message": "正在初始化...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }
        return task_id

    def _update_progress(
        self,
        task_id: str,
        phase: int,
        phase_name: str,
        progress: int,
        message: str,
    ):
        """更新任务进度"""
        if task_id in _pipeline_tasks:
            _pipeline_tasks[task_id].update({
                "current_phase": phase,
                "phase_name": phase_name,
                "progress": min(progress, 100),
                "message": message,
            })
            logger.info(f"[Pipeline {task_id[:8]}] 阶段{phase} {phase_name}: {progress}% - {message}")

    def _complete_task(self, task_id: str, result: Dict[str, Any]):
        """标记任务完成"""
        if task_id in _pipeline_tasks:
            _pipeline_tasks[task_id].update({
                "status": "completed",
                "progress": 100,
                "phase_name": "完成",
                "message": "生成完成！",
                "result": result,
            })

    def _fail_task(self, task_id: str, error: str):
        """标记任务失败"""
        if task_id in _pipeline_tasks:
            _pipeline_tasks[task_id].update({
                "status": "failed",
                "message": f"生成失败：{error}",
                "error": error,
            })

    def _save_history_prompt(
        self,
        task_id: str,
        session_id: int,
        module_id: Optional[int],
        user_id: Optional[str],
        requirement_text: str,
        original_requirement: str,
    ):
        """保存本次生成的需求描述到历史提示词记录。

        仅保存用户原始需求（或文档链接预取后的正文），不保存技能注入前缀与
        接口 schema 上下文，避免历史记录被无关内容污染。
        """
        # 剥离技能注入前缀（"## 激活的测试方法论技能 ... ## 用户需求\\n\\n"）
        content = requirement_text
        if content.startswith("## 激活的测试方法论技能"):
            idx = content.find("## 用户需求")
            if idx != -1:
                content = content[idx + len("## 用户需求"):].lstrip("\n")

        # 若需求文本与原始输入不同（说明含文档链接且已被预取为文档正文），
        # 优先保存预取后的文档正文；否则保存用户原始输入
        has_doc_url = bool(
            _FEISHU_URL_RE.search(original_requirement)
            or _LANHU_URL_RE.search(original_requirement)
        )
        if has_doc_url and content.strip() != original_requirement.strip():
            save_content = content
        else:
            save_content = original_requirement

        _upsert_history_prompt(
            self.db,
            content=save_content,
            module_id=module_id,
            session_id=session_id,
            user_id=user_id,
        )
        logger.info(f"[Pipeline {task_id[:8]}] 已保存历史提示词")

    async def run(
        self,
        session_id: int,
        requirement_text: str,
        user_id: Optional[str] = None,
        selected_skills: Optional[List[str]] = None,
        module_id: Optional[int] = None,
        *,
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
        original_requirement: str = "",
    ) -> str:
        """
        启动 4 阶段 pipeline（异步执行）

        Args:
            session_id: 会话ID
            requirement_text: 需求文本
            user_id: 用户ID
            selected_skills: 选中的技能名称列表
            module_id: 模块ID
            api_endpoint_ids: API 接口 ID 列表
            api_project_id: API 项目 ID
            endpoint_index_to_id: 接口序号→数据库ID映射
            api_context: API 接口信息文本

        Returns:
            task_id: 任务ID，可用于查询进度
        """
        task_id = self._create_task()

        # 保留用户原始需求（注入技能前），用于保存历史提示词
        original_requirement = requirement_text

        # 注入技能内容到需求文本
        if selected_skills:
            skill_context = ""
            for name in selected_skills:
                body = get_skill_body(name)
                if body:
                    skill_context += f"\n\n{body}"
            if skill_context:
                requirement_text = (
                    "## 激活的测试方法论技能\n\n"
                    + skill_context.strip()
                    + "\n\n---\n\n## 用户需求\n\n"
                    + requirement_text
                )
                logger.info(f"已注入 {len(selected_skills)} 个技能到 pipeline")

        # 在后台执行 pipeline
        asyncio.create_task(
            self._run_pipeline(
                task_id, session_id, requirement_text, user_id,
                module_id=module_id,
                api_endpoint_ids=api_endpoint_ids,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id or {},
                api_context=api_context,
                original_requirement=original_requirement,
            )
        )

        return task_id

    async def _prefetch_document(self, requirement_text: str) -> str:
        """预取飞书/蓝湖文档内容，替换需求文本中的链接为实际内容"""
        return await prefetch_document(requirement_text, self.mcp_configs)

    async def _run_pipeline(
        self,
        task_id: str,
        session_id: int,
        requirement_text: str,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
        original_requirement: str = "",
    ):
        """执行完整的 4 阶段 pipeline"""
        from db.models import RequirementPoint, TestPoint, TestCase

        is_api_test = bool(api_context)

        try:
            # 预取飞书/蓝湖文档内容
            if _FEISHU_URL_RE.search(requirement_text) or _LANHU_URL_RE.search(requirement_text):
                self._update_progress(task_id, 1, "需求拆分", 2, "正在预取文档内容...")
                prefetched = await self._prefetch_document(requirement_text)
                if prefetched != requirement_text:
                    requirement_text = prefetched
                    logger.info(f"Pipeline: 文档预取成功，内容长度 {len(requirement_text)}")

            # ========== 阶段1: 需求拆分 (0-25%) ==========
            self._update_progress(task_id, 1, "需求拆分", 5, "正在分析需求文档...")

            requirement_points_data = await self.splitter.analyze(
                requirement_text,
                is_api_test=is_api_test,
                api_context=api_context,
            )

            if not requirement_points_data:
                raise ValueError("未生成任何需求点")

            # 查询当前模块下已有需求点，用于去重（范围：同一 session + 同一 module）
            existing_stmt = select(RequirementPoint).where(
                RequirementPoint.session_id == session_id
            )
            if module_id is not None:
                existing_stmt = existing_stmt.where(
                    RequirementPoint.module_id == module_id
                )
            else:
                # 未选择模块时，按 module_id IS NULL 过滤同一会话下的无模块需求点
                existing_stmt = existing_stmt.where(
                    RequirementPoint.module_id.is_(None)
                )
            existing_points = self.db.exec(existing_stmt).all()

            # 去重：基于 LLM 语义比对，已存在的相同需求点不再创建，仅新建未覆盖到的
            to_create = await _dedup_requirement_points_by_llm(
                requirement_points_data, existing_points, self.splitter.model, module_id
            )
            skipped = len(requirement_points_data) - len(to_create)

            # 保存需求点到数据库
            req_points = []
            for rp_data in to_create:
                rp = RequirementPoint(
                    session_id=session_id,
                    content=rp_data["content"],
                    module_id=rp_data["module_id"],
                    priority=rp_data["priority"],
                    order_index=rp_data["order_index"],
                    user_id=user_id,
                )
                self.db.add(rp)
                req_points.append(rp)

            self.db.commit()
            for rp in req_points:
                self.db.refresh(rp)

            self._update_progress(
                task_id, 1, "需求拆分", 25,
                f"需求拆分完成，新建 {len(req_points)} 个需求点"
                + (f"（已跳过 {skipped} 个重复）" if skipped else "")
            )

            # ========== 阶段2: 测试点生成 (25-50%) ==========
            all_test_points = []
            sem = asyncio.Semaphore(_PIPELINE_CONCURRENCY)
            total_rp = len(req_points)

            async def _gen_for_point(rp):
                async with sem:
                    try:
                        return rp, await self.tp_generator.generate(
                            rp.content,
                            is_api_test=is_api_test,
                            api_context=api_context,
                        )
                    except Exception as e:
                        logger.error(f"需求点 {rp.id} 测试点生成失败: {e}")
                        return rp, []

            self._update_progress(
                task_id, 2, "测试点生成", 25,
                f"正在为 {total_rp} 个需求点并发生成测试点..."
            )
            tp_results = await asyncio.gather(*[_gen_for_point(rp) for rp in req_points])

            # 收集后统一写库（Session 非协程安全，写操作不并发）
            for rp, tp_data_list in tp_results:
                for tp_data in tp_data_list:
                    tp = TestPoint(
                        requirement_point_id=rp.id,
                        session_id=session_id,
                        content=tp_data.get("content", ""),
                        test_type=tp_data.get("test_type", "functional"),
                        design_method=tp_data.get("design_method", ""),
                        priority=tp_data.get("priority", "medium"),
                        user_id=user_id,
                    )
                    self.db.add(tp)
                    all_test_points.append(tp)

            self.db.commit()
            for tp in all_test_points:
                self.db.refresh(tp)

            self._update_progress(
                task_id, 2, "测试点生成", 50,
                f"测试点生成完成，共 {len(all_test_points)} 个测试点"
            )

            # ========== 阶段3: 用例设计 (50-85%) ==========
            all_test_cases = []
            BATCH_SIZE = 3
            batches = [
                all_test_points[i:i + BATCH_SIZE]
                for i in range(0, len(all_test_points), BATCH_SIZE)
            ]

            # 加载原始需求文档作为上下文
            requirement_content = requirement_text
            design_sem = asyncio.Semaphore(_PIPELINE_CONCURRENCY)

            async def _design_batch(batch):
                async with design_sem:
                    test_points_for_batch = [
                        {
                            "id": tp.id,
                            "content": tp.content,
                            "test_type": tp.test_type,
                            "design_method": tp.design_method,
                            "priority": tp.priority,
                        }
                        for tp in batch
                    ]
                    try:
                        return await self.tc_designer.design_batch(
                            test_points_for_batch, requirement_content,
                            is_api_test=is_api_test,
                            api_context=api_context,
                        )
                    except Exception as e:
                        logger.error(f"批次用例设计失败: {e}")
                        return []

            self._update_progress(
                task_id, 3, "用例设计", 50,
                f"正在并发设计{'接口' if is_api_test else '功能'}测试用例... 共 {len(batches)} 批次"
            )
            batch_results = await asyncio.gather(*[_design_batch(b) for b in batches])

            # 收集后统一写库（Session 非协程安全，写操作不并发）
            for cases_data in batch_results:
                for case_data in cases_data:
                    steps = case_data.get("test_steps", [])

                    # 接口测试：将 endpoint_ref 转为 endpoint_id（与单次生成路径一致）
                    if is_api_test and isinstance(steps, list):
                        converted_steps = []
                        for s in steps:
                            if isinstance(s, dict):
                                s = dict(s)
                                # 补全 type 字段（LLM 有时不设置）
                                if s.get("endpoint_id") or s.get("endpoint_ref"):
                                    if not s.get("type"):
                                        s["type"] = "api_call"
                                # 转换 endpoint_ref → endpoint_id
                                if s.get("endpoint_ref") and endpoint_index_to_id:
                                    ref = s.pop("endpoint_ref", None)
                                    eid = endpoint_index_to_id.get(int(ref)) if ref else None
                                    if eid:
                                        s["endpoint_id"] = eid
                            converted_steps.append(s)
                        steps = converted_steps

                    # 根据测试类型设置不同的字段
                    if is_api_test:
                        steps = _normalize_pipeline_api_steps(steps, endpoint_index_to_id)

                    tc_kwargs = dict(
                        case_name=case_data.get("title", ""),
                        steps=steps if isinstance(steps, list) else [],
                        preset_conditions=case_data.get("preconditions", []) or [],
                        case_level=case_data.get("case_level", 1),
                        session_id=session_id,
                        module_id=module_id,
                        test_point_id=case_data.get("test_point_id"),
                        test_type=case_data.get("test_type"),
                        design_method=case_data.get("design_method"),
                        user_id=user_id,
                    )

                    if is_api_test:
                        # 接口测试：设置 API 相关字段
                        tc_kwargs["expected_results"] = [
                            s.get("description", "") if isinstance(s, dict) else str(s)
                            for s in steps
                        ] if steps else []
                        if api_endpoint_ids:
                            tc_kwargs["api_endpoint_id"] = ",".join(str(eid) for eid in api_endpoint_ids)
                        if api_project_id:
                            tc_kwargs["api_project_id"] = api_project_id
                        # 保存断言规则
                        assertions = case_data.get("assertions")
                        if assertions:
                            tc_kwargs["assertions"] = assertions
                    else:
                        # 功能测试：纯文本步骤
                        tc_kwargs["expected_results"] = [
                            s.get("expected", "") for s in steps
                        ] if steps else []

                    # 准确性后处理（一致性 + 置信度），仅接口测试
                    if is_api_test and api_endpoint_ids and endpoint_index_to_id:
                        try:
                            report, score, level = _compute_accuracy(case_data, endpoint_index_to_id, self.db)
                            tc_kwargs["consistency_report"] = json.dumps(report, ensure_ascii=False)
                            tc_kwargs["confidence_score"] = score
                            tc_kwargs["confidence_level"] = level
                        except Exception as _acc_err:
                            logger.warning("[Pipeline] 准确性计算失败: %s", _acc_err)

                    tc = TestCase(**tc_kwargs)
                    self.db.add(tc)
                    all_test_cases.append(tc)

            self.db.commit()
            # 刷新获取 ID
            for tc in all_test_cases:
                self.db.refresh(tc)

            # 为接口测试用例创建 ApiScenario 并关联 scenario_id
            logger.info(
                f"[Pipeline] 场景创建检查: is_api_test={is_api_test}, "
                f"api_project_id={api_project_id}, test_cases={len(all_test_cases)}, "
                f"endpoint_index_to_id={endpoint_index_to_id}"
            )
            if is_api_test and api_project_id and all_test_cases:
                from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel

                def _resolve_endpoint_id(step: dict) -> int | None:
                    """从步骤中提取 endpoint_id，支持 endpoint_ref（pipeline）和 endpoint_id（单次）两种格式。"""
                    eid = step.get("endpoint_id")
                    if eid:
                        return int(eid)
                    ref = step.get("endpoint_ref")
                    if ref and endpoint_index_to_id:
                        return endpoint_index_to_id.get(int(ref))
                    return None

                # 预加载接口信息
                # 识别 API 调用步骤：type == "api_call" 或 有 endpoint_id/endpoint_ref
                def _is_api_call_step(step: dict) -> bool:
                    if not isinstance(step, dict):
                        return False
                    return (
                        step.get("type") == "api_call"
                        or step.get("endpoint_id") is not None
                        or step.get("endpoint_ref") is not None
                    )

                all_endpoint_ids = set()
                for tc in all_test_cases:
                    for step in (tc.steps or []):
                        if _is_api_call_step(step):
                            eid = _resolve_endpoint_id(step)
                            if eid:
                                all_endpoint_ids.add(eid)
                    for pc in (tc.preset_conditions or []):
                        if _is_api_call_step(pc):
                            eid = _resolve_endpoint_id(pc)
                            if eid:
                                all_endpoint_ids.add(eid)

                logger.info(f"[Pipeline] 发现 {len(all_endpoint_ids)} 个关联接口: {all_endpoint_ids}")

                endpoint_map: dict[int, ApiEndpointModel] = {}
                if all_endpoint_ids:
                    rows = self.db.exec(
                        select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
                    ).all()
                    endpoint_map = {e.id: e for e in rows}

                scenarios_created = 0
                for tc in all_test_cases:
                    # 收集 API 调用步骤（前置条件 + 主步骤）
                    api_steps = []
                    for pc in (tc.preset_conditions or []):
                        if _is_api_call_step(pc) and _resolve_endpoint_id(pc):
                            api_steps.append(("preset", pc))
                    for step in (tc.steps or []):
                        if _is_api_call_step(step) and _resolve_endpoint_id(step):
                            api_steps.append(("step", step))

                    if not api_steps:
                        continue

                    scenario_steps = []
                    for step_type, s in api_steps:
                        step_copy = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                        # 统一转为 endpoint_id 格式
                        eid = _resolve_endpoint_id(s)
                        if eid:
                            step_copy["endpoint_id"] = eid
                            step_copy.pop("endpoint_ref", None)
                        ep = endpoint_map.get(eid) if eid else None
                        if ep:
                            step_copy.setdefault("method", ep.method)
                            step_copy.setdefault("path", ep.path)
                            step_copy.setdefault("url", ep.url or ep.path)
                            step_copy.setdefault("endpoint_name", ep.name)
                            step_copy.setdefault("enabled", True)
                            step_copy.setdefault("continue_on_failure", True)
                        if step_type == "preset":
                            step_copy["is_preset"] = True
                        scenario_steps.append(step_copy)

                    scenario = ApiScenario(
                        project_id=api_project_id,
                        name=f"{tc.case_name}_场景",
                        description=f"测试用例 {tc.case_name} 的接口场景",
                        steps=scenario_steps,
                        user_id=user_id,
                    )
                    self.db.add(scenario)
                    self.db.flush()
                    tc.scenario_id = scenario.id
                    scenarios_created += 1

                self.db.commit()
                logger.info(f"[Pipeline] 已创建 {scenarios_created} 个场景，关联到 {scenarios_created} 个用例")

            self._update_progress(
                task_id, 3, "用例设计", 85,
                f"用例设计完成，共 {len(all_test_cases)} 个用例"
            )

            # ========== 阶段4: 用例优化 (85-100%) ==========
            if all_test_cases:
                self._update_progress(task_id, 4, "用例优化", 88, "正在优化测试用例...")

                try:
                    cases_for_opt = []
                    for tc in all_test_cases:
                        item = {
                            "id": tc.id,
                            "title": tc.case_name,
                            "description": "",
                            "preconditions": tc.preset_conditions or [],
                            "test_steps": tc.steps if tc.steps else [],
                        }
                        if is_api_test:
                            item["assertions"] = tc.assertions or []
                        cases_for_opt.append(item)

                    optimized = await self.optimizer.optimize(
                        cases_for_opt, is_api_test=is_api_test, api_context=api_context,
                    )

                    # 应用优化结果
                    opt_map = {opt.get("id"): opt for opt in optimized if opt.get("id")}
                    optimized_count = 0
                    for tc in all_test_cases:
                        opt = opt_map.get(tc.id)
                        if opt and opt.get("title"):
                            tc.case_name = opt["title"]
                            if opt.get("test_steps"):
                                opt_steps = opt["test_steps"]
                                # 优化器输出也是 endpoint_ref 格式，需转为 endpoint_id
                                if is_api_test and endpoint_index_to_id:
                                    converted_opt_steps = []
                                    for s in opt_steps:
                                        if isinstance(s, dict) and s.get("endpoint_ref"):
                                            s = dict(s)
                                            ref = s.pop("endpoint_ref", None)
                                            eid = endpoint_index_to_id.get(int(ref)) if ref else None
                                            if eid:
                                                s["endpoint_id"] = eid
                                        if isinstance(s, dict):
                                            s = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                                        converted_opt_steps.append(s)
                                    opt_steps = converted_opt_steps
                                elif is_api_test:
                                    opt_steps = _normalize_pipeline_api_steps(opt_steps, endpoint_index_to_id)
                                tc.steps = opt_steps
                                if is_api_test:
                                    tc.expected_results = [
                                        s.get("description", "") for s in opt_steps
                                    ]
                                else:
                                    tc.expected_results = [
                                        s.get("expected", "") for s in opt_steps
                                    ]
                            if opt.get("preconditions"):
                                tc.preset_conditions = opt["preconditions"]
                            if is_api_test and opt.get("assertions"):
                                tc.assertions = opt["assertions"]
                            optimized_count += 1

                    if is_api_test and api_project_id and all_test_cases:
                        from db.models import ApiEndpoint as ApiEndpointModel, ApiScenario

                        all_endpoint_ids = _collect_pipeline_endpoint_ids(all_test_cases, endpoint_index_to_id)
                        endpoint_map: dict[int, ApiEndpointModel] = {}
                        if all_endpoint_ids:
                            rows = self.db.exec(
                                select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
                            ).all()
                            endpoint_map = {e.id: e for e in rows}

                        for tc in all_test_cases:
                            if not tc.scenario_id:
                                continue
                            scenario = self.db.get(ApiScenario, tc.scenario_id)
                            if not scenario:
                                continue
                            scenario.steps = _build_pipeline_scenario_steps(tc, endpoint_map, endpoint_index_to_id)
                            self.db.add(scenario)

                    self.db.commit()
                    logger.info(f"优化完成，成功优化 {optimized_count} 个用例")

                except Exception as e:
                    logger.error(f"用例优化失败: {e}")

            # 保存历史提示词（需求描述），仅整体生成成功时记录
            try:
                self._save_history_prompt(
                    task_id, session_id, module_id, user_id,
                    requirement_text, original_requirement,
                )
            except Exception as e:
                logger.warning(f"保存历史提示词失败（不影响主流程）: {e}")

            # 完成
            self._complete_task(task_id, {
                "requirement_points_count": len(req_points),
                "test_points_count": len(all_test_points),
                "test_cases_count": len(all_test_cases),
                "is_api_test": is_api_test,
            })

            logger.info(
                f"Pipeline 完成: {len(req_points)} 需求点, "
                f"{len(all_test_points)} 测试点, {len(all_test_cases)} 测试用例"
                f"{'(接口测试)' if is_api_test else '(功能测试)'}"
            )

        except Exception as e:
            logger.error(f"Pipeline 执行失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    # ===================== 复用：用例构造 + 场景同步 =====================

    def _build_testcase_kwargs(
        self,
        case_data: dict,
        *,
        test_point_id: int,
        session_id: int,
        requirement_content: str,
        is_api_test: bool,
        api_context: str,
        endpoint_index_to_id: Dict[int, int],
        api_project_id: Optional[int],
        api_endpoint_ids: Optional[List[int]],
        module_id: Optional[int],
        user_id: Optional[str],
        test_type: Optional[str] = None,
        design_method: Optional[str] = None,
        priority: Optional[str] = "medium",
    ) -> dict:
        """从 designer 输出的单条 case_data 构造 TestCase 构造参数（不含 id）。"""
        steps = case_data.get("test_steps", [])

        if is_api_test and isinstance(steps, list):
            converted = []
            for s in steps:
                if isinstance(s, dict):
                    s = dict(s)
                    if s.get("endpoint_id") or s.get("endpoint_ref"):
                        if not s.get("type"):
                            s["type"] = "api_call"
                    if s.get("endpoint_ref") and endpoint_index_to_id:
                        ref = s.pop("endpoint_ref", None)
                        eid = endpoint_index_to_id.get(int(ref)) if ref else None
                        if eid:
                            s["endpoint_id"] = eid
                converted.append(s)
            steps = converted
            steps = _normalize_pipeline_api_steps(steps, endpoint_index_to_id)

        tc_kwargs = dict(
            case_name=case_data.get("title", ""),
            steps=steps if isinstance(steps, list) else [],
            preset_conditions=case_data.get("preconditions", []) or [],
            case_level=case_data.get("case_level", 1),
            session_id=session_id,
            module_id=module_id,
            test_point_id=test_point_id,
            test_type=case_data.get("test_type") or test_type,
            design_method=case_data.get("design_method") or design_method,
            user_id=user_id,
        )

        if is_api_test:
            tc_kwargs["expected_results"] = [
                s.get("description", "") if isinstance(s, dict) else str(s)
                for s in steps
            ] if steps else []
            if api_endpoint_ids:
                tc_kwargs["api_endpoint_id"] = ",".join(str(eid) for eid in api_endpoint_ids)
            if api_project_id:
                tc_kwargs["api_project_id"] = api_project_id
            assertions = case_data.get("assertions")
            if assertions:
                tc_kwargs["assertions"] = assertions
        else:
            tc_kwargs["expected_results"] = [
                s.get("expected", "") for s in steps
            ] if steps else []

        return tc_kwargs

    def _sync_api_scenarios(
        self,
        test_cases: list,
        *,
        api_project_id: int,
        endpoint_index_to_id: Dict[int, int],
        user_id: Optional[str],
    ) -> None:
        """为 test_cases 中带 API 调用步骤的用例创建/更新 ApiScenario，并回写 scenario_id。"""
        from db.models import ApiScenario, ApiEndpoint as ApiEndpointModel

        all_endpoint_ids = _collect_pipeline_endpoint_ids(test_cases, endpoint_index_to_id)
        endpoint_map: dict[int, ApiEndpointModel] = {}
        if all_endpoint_ids:
            rows = self.db.exec(
                select(ApiEndpointModel).where(ApiEndpointModel.id.in_(list(all_endpoint_ids)))
            ).all()
            endpoint_map = {e.id: e for e in rows}

        for tc in test_cases:
            api_steps = []
            for pc in (tc.preset_conditions or []):
                if _is_pipeline_api_call_step(pc) and _resolve_pipeline_endpoint_id(pc, endpoint_index_to_id):
                    api_steps.append(("preset", pc))
            for step in (tc.steps or []):
                if _is_pipeline_api_call_step(step) and _resolve_pipeline_endpoint_id(step, endpoint_index_to_id):
                    api_steps.append(("step", step))
            if not api_steps:
                continue

            scenario_steps = []
            for step_type, s in api_steps:
                step_copy = _normalize_pipeline_api_step(s, endpoint_index_to_id)
                eid = _resolve_pipeline_endpoint_id(s, endpoint_index_to_id)
                if eid:
                    step_copy["endpoint_id"] = eid
                    step_copy.pop("endpoint_ref", None)
                ep = endpoint_map.get(eid) if eid else None
                if ep:
                    step_copy.setdefault("method", ep.method)
                    step_copy.setdefault("path", ep.path)
                    step_copy.setdefault("url", ep.url or ep.path)
                    step_copy.setdefault("endpoint_name", ep.name)
                    step_copy.setdefault("enabled", True)
                    step_copy.setdefault("continue_on_failure", True)
                if step_type == "preset":
                    step_copy["is_preset"] = True
                scenario_steps.append(step_copy)

            if tc.scenario_id:
                scenario = self.db.get(ApiScenario, tc.scenario_id)
                if scenario:
                    scenario.steps = scenario_steps
                    scenario.name = f"{tc.case_name}_场景"
                    self.db.add(scenario)
                    continue
            scenario = ApiScenario(
                project_id=api_project_id,
                name=f"{tc.case_name}_场景",
                description=f"测试用例 {tc.case_name} 的接口场景",
                steps=scenario_steps,
                user_id=user_id,
            )
            self.db.add(scenario)
            self.db.flush()
            tc.scenario_id = scenario.id

    # ===================== 就地更新：用例 =====================

    async def _inplace_update_cases(
        self,
        test_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """
        用 LLM 为一个测试点重设计用例，并就地更新其下已有关联用例（去重、不重复）。

        Returns: 该测试点下最终用例数量。
        """
        from db.models import TestCase

        endpoint_index_to_id = endpoint_index_to_id or {}

        # 1) 调 LLM 生成（design_batch 强制 len == 1，缺失返回 {}）
        cases_data = await self.tc_designer.design_batch(
            [{
                "id": test_point.id,
                "content": test_point.content,
                "test_type": test_point.test_type,
                "design_method": test_point.design_method,
                "priority": test_point.priority,
            }],
            requirement_content,
            is_api_test=is_api_test,
            api_context=api_context,
        )
        generated = cases_data[0] if cases_data else {}

        # 2) 读取该测试点下已有关联用例（按 id 稳定顺序）
        existing: list = self.db.exec(
            select(TestCase).where(TestCase.test_point_id == test_point.id).order_by(TestCase.id)
        ).all()

        # 3) 按位置匹配
        to_update, to_create, to_delete = merge_entities_by_position(existing, [generated])

        # 4) 删除多余旧用例（同时清理其孤儿场景）
        for old in to_delete:
            if old.scenario_id:
                from db.models import ApiScenario
                sc = self.db.get(ApiScenario, old.scenario_id)
                if sc:
                    self.db.delete(sc)
            self.db.delete(old)

        # 5) 更新已有（保留 id）
        for old, gen in to_update:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            for k, v in kwargs.items():
                setattr(old, k, v)
            self.db.add(old)

        # 6) 新建缺失
        created: list = []
        for gen in to_create:
            kwargs = self._build_testcase_kwargs(
                gen,
                test_point_id=test_point.id,
                session_id=test_point.session_id,
                requirement_content=requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
                test_type=test_point.test_type,
                design_method=test_point.design_method,
                priority=test_point.priority,
            )
            tc = TestCase(**kwargs)
            self.db.add(tc)
            created.append(tc)

        kept_existing = [old for old, _ in to_update]
        self.db.commit()
        for tc in (kept_existing + created):
            self.db.refresh(tc)

        # 7) 接口测试：为所有（更新+新建）用例同步 ApiScenario
        # 仅包含保留下来的用例，避免为已删除用例生成孤儿场景
        all_cases = [t for t in (kept_existing + created) if not _is_empty_case(t)]
        if is_api_test and api_project_id and all_cases:
            self._sync_api_scenarios(
                all_cases,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id,
                user_id=user_id,
            )
            self.db.commit()

        return len(all_cases)

    # ===================== 就地更新：测试点（级联到用例） =====================

    async def _inplace_update_test_points(
        self,
        requirement_point,
        requirement_content: str,
        *,
        is_api_test: bool,
        api_context: str = "",
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_project_id: Optional[int] = None,
        api_endpoint_ids: Optional[List[int]] = None,
        module_id: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> list:
        """
        用 LLM 为需求点重生成测试点，并就地更新其下已有关联测试点；
        每个（更新/新建的）测试点再就地重生成用例（一步到位）。
        返回最终测试点列表。
        """
        from db.models import TestPoint

        endpoint_index_to_id = endpoint_index_to_id or {}

        tp_data_list = await self.tp_generator.generate(
            requirement_point.content,
            is_api_test=is_api_test,
            api_context=api_context,
        )

        existing: list = self.db.exec(
            select(TestPoint)
            .where(TestPoint.requirement_point_id == requirement_point.id)
            .order_by(TestPoint.id)
        ).all()

        to_update, to_create, to_delete = merge_entities_by_position(existing, tp_data_list)

        # 删除多余旧测试点（级联删其用例 + 孤儿场景）
        for old in to_delete:
            _cascade_delete_test_point(self.db, old)

        # 更新已有测试点（保留 id）
        for old, gen in to_update:
            old.content = gen.get("content", old.content)
            old.test_type = gen.get("test_type", old.test_type)
            old.design_method = gen.get("design_method", old.design_method)
            old.priority = gen.get("priority", old.priority)
            old.api_project_id = api_project_id
            old.api_endpoint_ids = ",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None
            self.db.add(old)

        # 新建缺失测试点
        created: list = []
        for gen in to_create:
            tp = TestPoint(
                requirement_point_id=requirement_point.id,
                session_id=requirement_point.session_id,
                content=gen.get("content", ""),
                test_type=gen.get("test_type", "functional"),
                design_method=gen.get("design_method", ""),
                priority=gen.get("priority", "medium"),
                user_id=user_id,
                api_project_id=api_project_id,
                api_endpoint_ids=",".join(str(e) for e in api_endpoint_ids) if api_endpoint_ids else None,
            )
            self.db.add(tp)
            created.append(tp)

        kept_existing = [old for old, _ in to_update]
        self.db.commit()
        for tp in (kept_existing + created):
            self.db.refresh(tp)

        # 一步到位：为每个测试点就地重生成用例
        # 仅对保留下来的测试点执行，避免为已删除测试点生成孤儿用例
        kept_existing = [old for old, _ in to_update]
        final_tps = [t for t in (kept_existing + created) if not _is_empty_tp(t)]
        for tp in final_tps:
            await self._inplace_update_cases(
                tp,
                requirement_content,
                is_api_test=is_api_test,
                api_context=api_context,
                endpoint_index_to_id=endpoint_index_to_id,
                api_project_id=api_project_id,
                api_endpoint_ids=api_endpoint_ids,
                module_id=module_id,
                user_id=user_id,
            )
        return final_tps

    # ===================== 重生成入口（异步任务包装） =====================

    def _resolve_api_context(self, api_project_id, api_endpoint_ids_str):
        """根据测试点/需求点上的 api 字段重建接口上下文。"""
        from app.services.api_context import build_api_context, parse_endpoint_ids
        from app.routes.testcase import infer_endpoint_dependencies

        api_endpoint_ids = parse_endpoint_ids(api_endpoint_ids_str) if api_endpoint_ids_str else []
        is_api_test = bool(api_endpoint_ids and api_project_id)
        endpoint_index_to_id: Dict[int, int] = {}
        api_context = ""
        if is_api_test:
            extra = infer_endpoint_dependencies(self.db, api_endpoint_ids, api_project_id)
            for eid in extra:
                if eid not in api_endpoint_ids:
                    api_endpoint_ids.append(eid)
            api_context, endpoint_index_to_id = build_api_context(
                self.db, api_endpoint_ids, api_project_id, {},
            )
        return is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids

    async def regenerate_test_point(
        self,
        task_id: str,
        test_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成单个测试点的用例（就地更新）。"""
        from db.db import engine
        from sqlmodel import Session as DBSession
        from db.models import TestPoint
        try:
            with DBSession(engine) as session:
                self.db = session
                tp = session.get(TestPoint, test_point_id)
                if not tp:
                    raise ValueError("测试点不存在")

                is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                    self._resolve_api_context(tp.api_project_id, tp.api_endpoint_ids)

                requirement_content = tp.content  # 测试点级用测试点内容作为上下文
                count = await self._inplace_update_cases(
                    tp,
                    requirement_content,
                    is_api_test=is_api_test,
                    api_context=api_context,
                    endpoint_index_to_id=endpoint_index_to_id,
                    api_project_id=api_project_id,
                    api_endpoint_ids=api_endpoint_ids,
                    module_id=module_id,
                    user_id=user_id,
                )
                self._complete_task(task_id, {
                    "test_point_id": test_point_id,
                    "cases_count": count,
                    "is_api_test": is_api_test,
                })
        except Exception as e:
            logger.error(f"重生成测试点用例失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))

    async def regenerate_requirement_point(
        self,
        task_id: str,
        requirement_point_id: int,
        user_id: Optional[str] = None,
        *,
        module_id: Optional[int] = None,
    ) -> None:
        """重生成需求点下的测试点（一步到位级联到用例）。"""
        from db.db import engine
        from sqlmodel import Session as DBSession
        from db.models import RequirementPoint
        try:
            with DBSession(engine) as session:
                self.db = session
                rp = session.get(RequirementPoint, requirement_point_id)
                if not rp:
                    raise ValueError("需求点不存在")

                # 未显式传入时继承需求点已有的 module_id
                if module_id is None:
                    module_id = rp.module_id

                is_api_test, api_context, endpoint_index_to_id, api_project_id, api_endpoint_ids = \
                    self._resolve_api_context(rp.api_project_id, rp.api_endpoint_ids)

                final_tps = await self._inplace_update_test_points(
                    rp,
                    rp.content,
                    is_api_test=is_api_test,
                    api_context=api_context,
                    endpoint_index_to_id=endpoint_index_to_id,
                    api_project_id=api_project_id,
                    api_endpoint_ids=api_endpoint_ids,
                    module_id=module_id,
                    user_id=user_id,
                )
                self._complete_task(task_id, {
                    "requirement_point_id": requirement_point_id,
                    "test_points_count": len(final_tps),
                    "is_api_test": is_api_test,
                })
        except Exception as e:
            logger.error(f"重生成需求点测试点失败: {e}", exc_info=True)
            self._fail_task(task_id, str(e))
