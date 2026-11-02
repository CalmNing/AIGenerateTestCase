"""用例执行验证与失败归因（P3：把执行结果变成可回灌的反馈信号）。

背景（调研结论 B4 / C2-2）：
- 本项目已经具备客观 oracle：`scenario_runner.run_scenario` 会真实发 HTTP 请求，
  `assertion_engine._run_assertions` 会逐条判定断言；
- 但 `validation_status` **只有写侧没有读侧**——生成链路从不回读执行结果，
  所以"部分失败被伪装成完全成功"，agent 也没有可用的 error signal。

本模块提供 P3 闭环的三个能力：
1. `verify_testcase()`：执行单条接口用例，返回结构化结果（不抛异常）；
2. `build_failure_feedback()`：把执行失败**归因**成结构化、可直接喂给 LLM 的修复指令；
3. `verify_batch()`：批量执行并汇总（通过率、失败用例清单）。

设计约束：
- **只做接口测试**（有 ApiScenario + ApiProject 才能执行），功能测试用例直接跳过；
- 绝不抛异常：执行环境不可用（没有 base_url / 环境缺失 / 网络不通）时返回
  `skipped`，而不是把整个生成流程打断；
- 失败归因区分"**用例写得不对**"与"**环境/接口本身有问题**"，避免让 LLM 去修
  一个根本不由它控制的失败。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 执行结果状态
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

# 归因分类（决定该失败是否值得让 LLM 修）
CAUSE_ASSERTION = "assertion_mismatch"   # 断言不匹配：用例预期与实际不符 → 可修
CAUSE_STATUS = "unexpected_status"        # 状态码不符 → 多半可修
CAUSE_REQUEST = "invalid_request"         # 请求构造问题（4xx 参数/路径错）→ 可修
CAUSE_CONNECTION = "connection_error"     # 连不上 → 环境问题，不该让 LLM 修
CAUSE_TIMEOUT = "timeout"                 # 超时 → 环境问题
CAUSE_NO_SCENARIO = "no_scenario"         # 没有可执行场景 → 跳过
CAUSE_NO_PROJECT = "no_project"           # 没有 API 项目 → 跳过
CAUSE_READONLY_SKIPPED = "readonly_skipped"  # 只读模式下跳过的写操作
CAUSE_UNKNOWN = "unknown"

# 只读方法（不改变服务端状态）
READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

# 环境类失败：不该让 LLM 反复重试
_ENVIRONMENT_CAUSES = {CAUSE_CONNECTION, CAUSE_TIMEOUT}


@dataclass
class VerifyResult:
    """单条用例的执行验证结果。"""
    testcase_id: int
    status: str
    passed: bool = False
    cause: str = CAUSE_UNKNOWN
    cause_detail: str = ""
    fixable: bool = False          # 是否值得让 LLM 尝试修复
    failed_assertions: list[dict] = field(default_factory=list)
    failed_steps: list[dict] = field(default_factory=list)
    error: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "testcase_id": self.testcase_id,
            "status": self.status,
            "passed": self.passed,
            "cause": self.cause,
            "cause_detail": self.cause_detail,
            "fixable": self.fixable,
            "failed_assertions": self.failed_assertions,
            "failed_steps": self.failed_steps,
            "error": self.error,
        }


def _classify_step_failure(step_result: dict) -> tuple[str, str]:
    """把单步执行结果归类，返回 (cause, detail)。"""
    if not isinstance(step_result, dict):
        return CAUSE_UNKNOWN, ""

    status = (step_result.get("status") or "").lower()
    detail = str(step_result.get("detail") or step_result.get("error") or "")

    # 连接类错误：环境问题
    if "connect" in detail.lower() or "connection" in detail.lower():
        return CAUSE_CONNECTION, detail[:300]
    if "timeout" in detail.lower() or "timed out" in detail.lower():
        return CAUSE_TIMEOUT, detail[:300]
    if status == "error" and not detail:
        return CAUSE_UNKNOWN, "步骤执行异常"

    # 断言结果
    assertions = step_result.get("assertions")
    if isinstance(assertions, list):
        failed = [a for a in assertions if isinstance(a, dict) and not a.get("passed", True)]
        if failed:
            cause = CAUSE_ASSERTION
            # 状态码断言单独归类，便于区分"预期状态码写错"
            for a in failed:
                if a.get("type") == "status_code":
                    cause = CAUSE_STATUS
                    break
            return cause, _summarize_assertions(failed)

    http_status = step_result.get("status_code") or step_result.get("http_status")
    if isinstance(http_status, int):
        if 400 <= http_status < 500:
            return CAUSE_REQUEST, f"HTTP {http_status}: {detail[:200]}"
        if http_status >= 500:
            # 服务端错误：更可能是被测系统/环境问题，而不是用例写错
            return CAUSE_CONNECTION, f"HTTP {http_status}: {detail[:200]}"

    if status == "failed":
        return CAUSE_ASSERTION, detail[:300]

    return CAUSE_UNKNOWN, detail[:200]


def _summarize_assertions(failed: list[dict]) -> str:
    """把失败断言压缩成一行可读文本。"""
    parts: list[str] = []
    for a in failed[:5]:
        kind = a.get("type", "?")
        expected = a.get("expected")
        actual = a.get("actual")
        if isinstance(expected, str) and len(expected) > 60:
            expected = expected[:60] + "…"
        if isinstance(actual, str) and len(actual) > 60:
            actual = actual[:60] + "…"
        parts.append(f"{kind}(期望={expected!r}, 实际={actual!r})")
    return "; ".join(parts)


def _collect_failed_assertions(raw: dict) -> list[dict]:
    """从 run_scenario 的返回里收集所有失败断言。"""
    out: list[dict] = []
    for step in (raw.get("steps") or []):
        if not isinstance(step, dict):
            continue
        assertions = step.get("assertions")
        if not isinstance(assertions, list):
            continue
        for a in assertions:
            if isinstance(a, dict) and not a.get("passed", True):
                out.append({"step_index": step.get("index"), **a})
    return out


def _collect_failed_steps(raw: dict) -> list[dict]:
    """收集失败步骤的摘要（用于回灌给 LLM 时定位到具体步骤）。"""
    out: list[dict] = []
    for step in (raw.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if (step.get("status") or "").lower() in ("failed", "error"):
            out.append({
                "index": step.get("index"),
                "status": step.get("status"),
                "detail": str(step.get("detail") or "")[:200],
                "http_status": step.get("status_code") or step.get("http_status"),
            })
    return out


def _is_read_only_scenario(db, scenario) -> bool:
    """判断场景是否只包含只读方法的接口。

    只读模式（默认）下跳过写操作，避免真实改动被测系统的数据。
    场景步骤里没有 method 时按 endpoint 的 method 判断；都取不到时**保守视为只读**
    （宁可少执行，也不要误写）。
    """
    from db.models import ApiEndpoint

    steps = getattr(scenario, "steps", None) or []
    if not steps:
        return True
    for step in steps:
        if not isinstance(step, dict):
            continue
        method = (step.get("method") or "").upper()
        if not method:
            endpoint_id = step.get("endpoint_id")
            if endpoint_id:
                try:
                    endpoint = db.get(ApiEndpoint, endpoint_id)
                    method = (getattr(endpoint, "method", "") or "").upper()
                except Exception:  # noqa: BLE001
                    method = ""
        if method and method not in READ_ONLY_METHODS:
            return False
    return True


async def verify_testcase(
    db,
    testcase,
    *,
    session_headers: Optional[list] = None,
    timeout_guard: bool = True,
    read_only: bool = True,
) -> VerifyResult:
    """执行单条接口用例并归因失败。

    参数：
        db: SQLModel Session
        testcase: db.models.TestCase
        session_headers: 会话级请求头
        timeout_guard: 是否在执行前检查"环境是否可用"（base_url 为空则跳过）

    返回 `VerifyResult`；**任何异常都被吞掉并转成 error 结果**，不打断调用方。
    """
    tc_id = getattr(testcase, "id", 0) or 0

    try:
        from db.models import ApiProject, ApiScenario
    except Exception:  # pragma: no cover
        return VerifyResult(testcase_id=tc_id, status=STATUS_SKIPPED, cause=CAUSE_NO_PROJECT)

    scenario = None
    if getattr(testcase, "scenario_id", None):
        try:
            scenario = db.get(ApiScenario, testcase.scenario_id)
        except Exception:  # noqa: BLE001
            scenario = None

    if scenario is None:
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_SKIPPED,
            cause=CAUSE_NO_SCENARIO,
            cause_detail="该用例没有关联可执行的接口场景",
        )

    project = None
    try:
        project = db.get(ApiProject, scenario.project_id)
    except Exception:  # noqa: BLE001
        project = None

    if project is None:
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_SKIPPED,
            cause=CAUSE_NO_PROJECT,
            cause_detail="关联的 API 项目不存在",
        )

    if timeout_guard and not (project.base_url or "").strip():
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_SKIPPED,
            cause=CAUSE_NO_PROJECT,
            cause_detail="API 项目未配置 base_url，无法执行",
        )

    # 只读模式：跳过写操作接口（避免真实改动被测系统数据）
    if read_only and not _is_read_only_scenario(db, scenario):
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_SKIPPED,
            cause=CAUSE_READONLY_SKIPPED,
            cause_detail="只读模式：跳过包含写操作（POST/PUT/PATCH/DELETE）的用例",
        )

    try:
        from app.services.scenario_runner import run_scenario

        raw = await run_scenario(db, scenario, project, session_headers=session_headers)
    except Exception as exc:  # noqa: BLE001 - 执行异常不打断生成
        logger.warning("用例执行验证异常（用例 %s）: %s", tc_id, exc)
        cause, detail = _classify_step_failure({"detail": str(exc)})
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_ERROR,
            cause=cause,
            cause_detail=detail,
            fixable=cause not in _ENVIRONMENT_CAUSES,
            error=f"{type(exc).__name__}: {exc}"[:400],
        )

    passed = bool(raw.get("passed"))
    failed_assertions = _collect_failed_assertions(raw)
    failed_steps = _collect_failed_steps(raw)

    if passed:
        return VerifyResult(
            testcase_id=tc_id,
            status=STATUS_PASSED,
            passed=True,
            raw=raw,
        )

    cause = CAUSE_UNKNOWN
    detail = ""
    for step in (raw.get("steps") or []):
        step_cause, step_detail = _classify_step_failure(step)
        if step_cause != CAUSE_UNKNOWN:
            cause, detail = step_cause, step_detail
            break

    return VerifyResult(
        testcase_id=tc_id,
        status=STATUS_FAILED,
        passed=False,
        cause=cause,
        cause_detail=detail,
        fixable=cause not in _ENVIRONMENT_CAUSES,
        failed_assertions=failed_assertions,
        failed_steps=failed_steps,
        raw=raw,
    )


async def verify_batch(
    db,
    testcases: list,
    *,
    session_headers: Optional[list] = None,
    concurrency: int = 3,
    read_only: bool = True,
) -> list[VerifyResult]:
    """批量执行验证（顺序执行，避免对同一环境并发打压）。

    单条失败不影响其他用例。
    """
    import asyncio

    results: list[VerifyResult] = []
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(tc):
        async with sem:
            return await verify_testcase(
                db, tc, session_headers=session_headers, read_only=read_only,
            )

    for tc in testcases or []:
        try:
            results.append(await _one(tc))
        except Exception as exc:  # noqa: BLE001
            results.append(VerifyResult(
                testcase_id=getattr(tc, "id", 0) or 0,
                status=STATUS_ERROR,
                cause=CAUSE_UNKNOWN,
                error=f"{type(exc).__name__}: {exc}"[:400],
            ))
    return results


def build_failure_feedback(
    results: list[VerifyResult],
    *,
    case_names: Optional[dict[int, str]] = None,
) -> str:
    """把验证结果转成**可回灌给 LLM 的修复指令**文本。

    只包含"值得修"的失败（`fixable=True`）；环境类失败（连不上/超时）
    不写进指令，否则 LLM 会反复尝试修一个它控制不了的问题。
    """
    fixable = [r for r in results if not r.passed and r.fixable]
    if not fixable:
        return ""

    names = case_names or {}
    lines = [
        "## 执行验证失败明细（必须据此修正用例）",
        "以下用例在真实接口上执行失败。请据此修正断言/请求参数/步骤，"
        "使断言与实际接口行为一致；无法修正的请在预期结果中如实描述实际行为。",
        "",
    ]
    for r in fixable[:20]:
        name = names.get(r.testcase_id, f"用例#{r.testcase_id}")
        lines.append(f"### {name}（用例 id={r.testcase_id}）")
        lines.append(f"- 失败类型：{r.cause}")
        if r.cause_detail:
            lines.append(f"- 实际结果：{r.cause_detail}")
        for step in r.failed_steps[:5]:
            lines.append(
                f"- 步骤 {step.get('index')} 失败：HTTP {step.get('http_status') or '-'} "
                f"{step.get('detail') or ''}".rstrip()
            )
        for a in r.failed_assertions[:5]:
            lines.append(
                f"- 断言不通过：{a.get('type')} 期望={a.get('expected')!r} 实际={a.get('actual')!r}"
            )
        lines.append("")

    return "\n".join(lines)


def summarize_verification(results: list[VerifyResult]) -> dict:
    """汇总验证结果（写入 task result / 用例统计）。"""
    total = len(results or [])
    passed = sum(1 for r in results if r.status == STATUS_PASSED)
    failed = sum(1 for r in results if r.status == STATUS_FAILED)
    error = sum(1 for r in results if r.status == STATUS_ERROR)
    skipped = sum(1 for r in results if r.status == STATUS_SKIPPED)
    fixable = sum(1 for r in results if r.status in (STATUS_FAILED, STATUS_ERROR) and r.fixable)
    executed = total - skipped
    # 通过率按"实际执行过的用例"计算：skipped（没有场景/环境未就绪）不是失败，
    # 混进分母会低估真实通过率。
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "error": error,
        "skipped": skipped,
        "fixable": fixable,
        "pass_rate": round(passed / executed, 3) if executed else 0.0,
        "executed": executed,
    }
