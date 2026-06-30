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

from sqlmodel import Session

from app.pipeline.requirement_splitter import RequirementSplitter
from app.pipeline.test_point_generator import TestPointGenerator
from app.pipeline.test_case_designer import TestCaseDesigner
from app.pipeline.test_case_optimizer import TestCaseOptimizer
from app.routes.skills import get_skill_body
from utils.document_prefetch import (
    FEISHU_URL_RE as _FEISHU_URL_RE,
    LANHU_URL_RE as _LANHU_URL_RE,
    prefetch_document,
)

logger = logging.getLogger(__name__)

# 内存中的 pipeline 任务状态存储
_pipeline_tasks: Dict[str, Dict[str, Any]] = {}


def get_pipeline_task(task_id: str) -> Optional[Dict[str, Any]]:
    """获取 pipeline 任务状态"""
    return _pipeline_tasks.get(task_id)


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

    async def run(
        self,
        session_id: int,
        requirement_text: str,
        user_id: Optional[str] = None,
        selected_skills: Optional[List[str]] = None,
        *,
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
    ) -> str:
        """
        启动 4 阶段 pipeline（异步执行）

        Args:
            session_id: 会话ID
            requirement_text: 需求文本
            user_id: 用户ID
            selected_skills: 选中的技能名称列表
            api_endpoint_ids: API 接口 ID 列表
            api_project_id: API 项目 ID
            endpoint_index_to_id: 接口序号→数据库ID映射
            api_context: API 接口信息文本

        Returns:
            task_id: 任务ID，可用于查询进度
        """
        task_id = self._create_task()

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
                api_endpoint_ids=api_endpoint_ids,
                api_project_id=api_project_id,
                endpoint_index_to_id=endpoint_index_to_id or {},
                api_context=api_context,
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
        api_endpoint_ids: Optional[List[int]] = None,
        api_project_id: Optional[int] = None,
        endpoint_index_to_id: Optional[Dict[int, int]] = None,
        api_context: str = "",
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

            requirement_points_data = await self.splitter.analyze(requirement_text)

            if not requirement_points_data:
                raise ValueError("未生成任何需求点")

            # 保存需求点到数据库
            req_points = []
            for idx, rp_data in enumerate(requirement_points_data):
                rp = RequirementPoint(
                    session_id=session_id,
                    content=rp_data.get("content", ""),
                    module=rp_data.get("module", ""),
                    priority=rp_data.get("priority", "medium"),
                    order_index=rp_data.get("order_index", idx),
                    user_id=user_id,
                )
                self.db.add(rp)
                req_points.append(rp)

            self.db.commit()
            for rp in req_points:
                self.db.refresh(rp)

            self._update_progress(
                task_id, 1, "需求拆分", 25,
                f"需求拆分完成，共 {len(req_points)} 个需求点"
            )

            # ========== 阶段2: 测试点生成 (25-50%) ==========
            all_test_points = []
            completed = 0

            for i, rp in enumerate(req_points):
                self._update_progress(
                    task_id, 2, "测试点生成",
                    25 + int((i / len(req_points)) * 25),
                    f"正在为需求点 {i+1}/{len(req_points)} 生成测试点..."
                )

                try:
                    tp_data_list = await self.tp_generator.generate(rp.content)

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

                    completed += 1
                except Exception as e:
                    logger.error(f"需求点 {i+1} 测试点生成失败: {e}")
                    completed += 1

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

            for batch_idx, batch in enumerate(batches):
                progress = 50 + int((batch_idx / len(batches)) * 35)
                self._update_progress(
                    task_id, 3, "用例设计", progress,
                    f"正在设计{'接口' if is_api_test else '功能'}测试用例... 批次 {batch_idx+1}/{len(batches)}"
                )

                try:
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

                    cases_data = await self.tc_designer.design_batch(
                        test_points_for_batch, requirement_content,
                        is_api_test=is_api_test,
                        api_context=api_context,
                    )

                    for case_data in cases_data:
                        # 转换 test_steps 为 JSON 字符串存储
                        steps = case_data.get("test_steps", [])
                        steps_str = ""
                        if steps:
                            steps_str = json.dumps(steps, ensure_ascii=False)

                        # 根据测试类型设置不同的字段
                        tc_kwargs = dict(
                            case_name=case_data.get("title", ""),
                            steps=steps if isinstance(steps, list) else [],
                            preset_conditions=[case_data.get("preconditions", "")] if case_data.get("preconditions") else [],
                            case_level=4,
                            session_id=session_id,
                            module_id=None,
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

                        tc = TestCase(**tc_kwargs)
                        self.db.add(tc)
                        all_test_cases.append(tc)

                    self.db.commit()

                except Exception as e:
                    logger.error(f"批次 {batch_idx+1} 用例设计失败: {e}")

            # 刷新获取 ID
            for tc in all_test_cases:
                self.db.refresh(tc)

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
                            "preconditions": tc.preset_conditions[0] if tc.preset_conditions else "",
                            "test_steps": tc.steps if tc.steps else [],
                        }
                        if is_api_test:
                            item["assertions"] = tc.assertions or []
                        cases_for_opt.append(item)

                    optimized = await self.optimizer.optimize(
                        cases_for_opt, is_api_test=is_api_test,
                    )

                    # 应用优化结果
                    opt_map = {opt.get("id"): opt for opt in optimized if opt.get("id")}
                    optimized_count = 0
                    for tc in all_test_cases:
                        opt = opt_map.get(tc.id)
                        if opt and opt.get("title"):
                            tc.case_name = opt["title"]
                            if opt.get("test_steps"):
                                tc.steps = opt["test_steps"]
                                if is_api_test:
                                    tc.expected_results = [
                                        s.get("description", "") for s in opt["test_steps"]
                                    ]
                                else:
                                    tc.expected_results = [
                                        s.get("expected", "") for s in opt["test_steps"]
                                    ]
                            if opt.get("preconditions"):
                                tc.preset_conditions = [opt["preconditions"]]
                            if is_api_test and opt.get("assertions"):
                                tc.assertions = opt["assertions"]
                            optimized_count += 1

                    self.db.commit()
                    logger.info(f"优化完成，成功优化 {optimized_count} 个用例")

                except Exception as e:
                    logger.error(f"用例优化失败: {e}")

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
