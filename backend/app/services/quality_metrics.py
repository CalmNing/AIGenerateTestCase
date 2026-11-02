"""用例质量指标（B6：零成本确定性评估基座）。

目标：不调 LLM、不需要人工标注，就能对一批用例算出**可复现**的质量指标，
作为"改动有没有让用例变好"的判据。

设计原则：
- **纯规则**：所有指标只用数据库里的字段计算，零 API 成本、可离线重跑；
- **确定性**：同样输入永远得到同样结果（不含随机、不含时间依赖）；
- **可解释**：每个指标都能说清"什么算失败"，便于定位问题。

指标清单
--------
| 指标 | 含义 | 判定 |
|---|---|---|
| `completeness` | 完整性：步骤/预期/前置是否齐全 | 有步骤、有预期结果 |
| `level_distribution` | 等级分布 | 1/2/3/4 覆盖情况 |
| `duplicate_rate` | 重复率 | 标题归一后重复的用例占比 |
| `assertion_density` | 断言密度 | 带断言的用例占比 / 每用例断言数 |
| `consistency` | 与接口 Schema 的一致性 | 复用 api_consistency（已修 endpoint 引用 bug） |
| `structure` | 结构规范 | 步骤是否带类型/描述、接口引用是否可解析 |
| `executable` | 可执行性 | 有 scenario_id / api_endpoint_id |

用法：
```
from app.services.quality_metrics import evaluate_testcases, summarize_metrics

results = evaluate_testcases(db, testcases)
summary = summarize_metrics(results)
```
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# 归一化标题时去掉编号前缀与空白差异，避免"1. xxx"与"xxx"被当成不同用例
_TITLE_PREFIX_RE = re.compile(r"^[\s\-–—•*\d]+[.、．)）:：\s]*")
_TITLE_PUNCT_RE = re.compile(r"[\s\-_/\\|]+")


def normalize_title(title: str) -> str:
    """标题归一化（用于重复判定）。"""
    if not title:
        return ""
    text = str(title).strip().lower()
    text = _TITLE_PREFIX_RE.sub("", text)
    text = _TITLE_PUNCT_RE.sub("", text)
    return text.strip()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _api_steps(steps: list) -> list[dict]:
    """挑出接口调用步骤。"""
    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        if (
            s.get("type") == "api_call"
            or s.get("endpoint_id") is not None
            or s.get("endpoint_ref") is not None
        ):
            out.append(s)
    return out


# 功能用例"预期结果可判定性"的词表（模块级，便于测试与复用）
# 笼统词：只由这些词构成的预期，执行人无法据此判断对错
VAGUE_WORDS = (
    "成功", "正常", "正确", "有效", "完成", "失败", "错误",
    "提示", "显示", "展示", "更新", "保存",
)
# 具体"可观察对象"关键词：出现任一即认为预期可核对
CONCRETE_KEYWORDS = (
    "状态", "变为", "变成", "跳转", "提示语", "文案", "字段",
    "数量", "金额", "url", "code", "message", "列表", "按钮", "输入框",
)


def expected_verifiable(expected_results: Any) -> bool:
    """判断功能用例的预期结果是否"可判定"（执行人能据此核对对错）。

    可判定的信号（任一满足）：
      - 含具体可观察对象关键词（状态/变为/跳转/提示语/字段/数量/URL…）
      - 引用了具体文案（「…」/ 【…】）
      - 含大写常量（APPROVED / PENDING / 200）
      - 含具体数值（"显示 3 条记录"）

    不可判定：只由笼统词构成（"操作成功""页面显示正常"）。

    注意：这是**启发式**判定，用于质量度量与提示，不是强规则——
    误判的代价低（只是多提示一句），漏判的代价也低。
    """
    exps = expected_results if isinstance(expected_results, list) else []
    text = " ".join(str(e) for e in exps).strip()
    if not text:
        return False

    if any(k in text.lower() for k in CONCRETE_KEYWORDS):
        return True
    # 引用具体文案
    if re.search(r"[『「【\u201c]", text):
        return True
    # 大写常量
    if re.search(r"[A-Z_]{3,}", text):
        return True
    # 具体数值
    if re.search(r"\d+", text):
        return True
    # 只由笼统词构成 → 不可判定
    return not any(w in text for w in VAGUE_WORDS)


# 兼容别名（测试用）
_expected_verifiable_for_test = expected_verifiable


def evaluate_testcase(
    testcase: Any,
    *,
    endpoint_index_to_id: Optional[dict] = None,
    endpoints: Optional[dict] = None,
) -> dict:
    """对单条用例计算各项指标（纯规则，无 LLM）。

    返回结构：
    ```
    {
      "id": 1, "name": "...", "level": 1,
      "has_steps": True, "has_expected": True, "has_preconditions": True,
      "step_count": 3, "assertion_count": 2,
      "api_step_count": 2,
      "level_valid": True,
      "consistency_pass_rate": 1.0, "consistency_passed": True,
      "structure_ok": True, "structure_issues": [],
      "issues": ["missing_expected"],       # 硬伤清单
    }
    ```
    """
    idx_to_id = endpoint_index_to_id or {}
    eps = endpoints or {}

    tc_id = getattr(testcase, "id", None)
    name = getattr(testcase, "case_name", "") or ""
    level = getattr(testcase, "case_level", None)
    steps = _as_list(getattr(testcase, "steps", None))
    expected = _as_list(getattr(testcase, "expected_results", None))
    presets = _as_list(getattr(testcase, "preset_conditions", None))
    assertions = _as_list(getattr(testcase, "assertions", None))

    issues: list[str] = []
    structure_issues: list[str] = []

    # --- 完整性 ---
    if not steps:
        issues.append("missing_steps")
    if not expected:
        issues.append("missing_expected")

    # --- 用例类型与预期可判定性（B6 口径修正） ---
    # 接口用例：有接口引用 → 它的断言可执行（有 oracle）
    is_api_case = bool(
        _api_steps(steps)
        or getattr(testcase, "scenario_id", None)
        or getattr(testcase, "api_endpoint_id", None)
    )

    # 功能用例的"预期结果可判定性"：见模块级 expected_verifiable() 的说明。
    # 这不是给功能用例硬造 HTTP 断言，而是推动"预期写得可验证"。

    # --- 等级 ---
    level_valid = level in (1, 2, 3, 4)

    # --- 结构规范 ---
    api_steps = _api_steps(steps)
    for i, s in enumerate(api_steps, 1):
        if not (s.get("description") or "").strip():
            structure_issues.append(f"step{i}_no_description")
        eid = s.get("endpoint_id")
        ref = s.get("endpoint_ref")
        if eid is None and ref is None:
            structure_issues.append(f"step{i}_no_endpoint_ref")
    for i, s in enumerate(steps, 1):
        if isinstance(s, dict):
            if not (s.get("action") or s.get("description") or "").strip():
                structure_issues.append(f"step{i}_empty_action")

    # --- 一致性（仅接口用例）---
    consistency_pass_rate = 1.0
    consistency_passed = True
    consistency_checks: list[dict] = []
    if api_steps and eps:
        try:
            from app.services.api_consistency import check_testcase_consistency

            case = {
                "steps": steps,
                "assertions": assertions,
                "expected_results": expected,
            }
            report = check_testcase_consistency(case, idx_to_id, eps)
            consistency_pass_rate = float(report.get("pass_rate", 1.0))
            consistency_passed = bool(report.get("passed", True))
            consistency_checks = report.get("checks", [])
            for chk in consistency_checks:
                if not chk.get("passed", True) and not chk.get("skipped"):
                    issues.append(f"consistency:{chk.get('check')}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("一致性检查失败（用例 %s）: %s", tc_id, exc)

    return {
        "id": tc_id,
        "name": name,
        "normalized_name": normalize_title(name),
        "level": level,
        "has_steps": bool(steps),
        "has_expected": bool(expected),
        "has_preconditions": bool(presets),
        "step_count": len(steps),
        "assertion_count": len(assertions) + sum(
            len(s.get("assertions") or []) for s in api_steps if isinstance(s, dict)
        ),
        "api_step_count": len(api_steps),
        # B6 口径修正：类型标记 + 功能用例预期可判定性
        "is_api_case": is_api_case,
        "expected_verifiable": expected_verifiable(expected),
        "level_valid": level_valid,
        "consistency_pass_rate": consistency_pass_rate,
        "consistency_passed": consistency_passed,
        "consistency_checks": consistency_checks,
        "structure_ok": not structure_issues,
        "structure_issues": structure_issues,
        "executable": bool(
            getattr(testcase, "scenario_id", None)
            or getattr(testcase, "api_endpoint_id", None)
        ),
        "issues": issues,
    }


def evaluate_testcases(
    db,
    testcases: Iterable[Any],
    *,
    endpoint_index_to_id: Optional[dict] = None,
    endpoints: Optional[dict] = None,
) -> list[dict]:
    """批量计算指标。"""
    results = []
    for tc in testcases or []:
        try:
            results.append(evaluate_testcase(
                tc,
                endpoint_index_to_id=endpoint_index_to_id,
                endpoints=endpoints,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("计算用例指标失败（已跳过）: %s", exc)
    return results


def find_duplicates(results: list[dict]) -> dict:
    """找出重复用例（按归一化标题）。

    返回 {归一化标题: [用例 id...]}，只含出现 ≥2 次的。
    """
    buckets: dict[str, list] = defaultdict(list)
    for r in results or []:
        key = r.get("normalized_name")
        if key:
            buckets[key].append(r.get("id"))
    return {k: v for k, v in buckets.items() if len(v) > 1}


def summarize_metrics(results: list[dict]) -> dict:
    """汇总指标 → 基线报告。"""
    items = results or []
    total = len(items)
    if total == 0:
        return {
            "total": 0,
            "completeness_rate": 0.0,
            "duplicate_rate": 0.0,
            "level_valid_rate": 0.0,
            "assertion_coverage_rate": 0.0,
            "avg_assertions_per_case": 0.0,
            "consistency_pass_rate": 0.0,
            "consistency_passed_rate": 0.0,
            "structure_ok_rate": 0.0,
            "executable_rate": 0.0,
            "level_distribution": {},
            "issue_counts": {},
            "duplicate_groups": 0,
            "duplicate_cases": 0,
        }

    def _rate(pred: Callable[[dict], bool]) -> float:
        return round(sum(1 for r in items if pred(r)) / total, 4)

    dup_map = find_duplicates(items)
    dup_cases = sum(len(v) for v in dup_map.values())
    total_assertions = sum(r.get("assertion_count", 0) for r in items)

    # 一致性只统计真正做过检查的用例（接口用例）
    checked = [r for r in items if r.get("consistency_checks")]

    issue_counts: dict[str, int] = Counter()
    for r in items:
        for iss in r.get("issues", []):
            issue_counts[iss] += 1

    level_dist = Counter(
        str(r.get("level")) if r.get("level") in (1, 2, 3, 4) else "invalid"
        for r in items
    )

    # 口径说明（B6 修正）：
    # 断言覆盖率的旧定义（"带结构化断言的用例占比"）把功能用例也算进分母，
    # 而功能用例**本来就不该有 HTTP 断言**（无响应结构可引用，FUNCTIONAL_PROMPT
    # 明确声明不用 assertions）——实测接口用例断言覆盖 100%，功能用例 2.8%，
    # 混算出的 13.9% 是口径失真，不代表质量问题。
    # 修正后分两个口径：
    #   - assertion_coverage_rate：**仅接口用例**（它们的断言可执行、有 oracle）
    #   - expected_verifiable_rate：**功能用例**的"预期结果可判定性"
    #     （预期里含具体字段/数值/状态变化，而不是只写"成功/正常"）
    api_cases = [r for r in items if r.get("is_api_case")]
    func_cases = [r for r in items if not r.get("is_api_case")]

    return {
        "total": total,
        # 完整性：有步骤且有预期
        "completeness_rate": _rate(lambda r: r.get("has_steps") and r.get("has_expected")),
        # 重复率
        "duplicate_rate": round(dup_cases / total, 4),
        "duplicate_groups": len(dup_map),
        "duplicate_cases": dup_cases,
        # 等级合法率
        "level_valid_rate": _rate(lambda r: r.get("level_valid")),
        "level_distribution": dict(sorted(level_dist.items())),
        # 断言（仅接口用例：断言可执行、有 oracle）
        "api_case_count": len(api_cases),
        "assertion_coverage_rate": (
            round(sum(1 for r in api_cases if r.get("assertion_count", 0) > 0) / len(api_cases), 4)
            if api_cases else 0.0
        ),
        "avg_assertions_per_api_case": (
            round(sum(r.get("assertion_count", 0) for r in api_cases) / len(api_cases), 2)
            if api_cases else 0.0
        ),
        # 功能用例预期可判定性（见 evaluate_testcase 里 expected_verifiable 的判定）
        "functional_case_count": len(func_cases),
        "expected_verifiable_rate": (
            round(sum(1 for r in func_cases if r.get("expected_verifiable")) / len(func_cases), 4)
            if func_cases else 0.0
        ),
        # 兼容保留：全体平均断言数（供趋势参考）
        "avg_assertions_per_case": round(total_assertions / total, 2),
        # 一致性（仅对做过检查的接口用例）——分母必须是"已检查"的数量，
        # 否则与 consistency_pass_rate 口径不一致（会把未检查的用例算成未通过）
        "consistency_checked": len(checked),
        "consistency_pass_rate": (
            round(sum(r.get("consistency_pass_rate", 0) for r in checked) / len(checked), 4)
            if checked else 0.0
        ),
        "consistency_passed_rate": (
            round(
                sum(1 for r in checked if r.get("consistency_passed")) / len(checked),
                4,
            )
            if checked else 0.0
        ),
        # 结构 / 可执行
        "structure_ok_rate": _rate(lambda r: r.get("structure_ok")),
        "executable_rate": _rate(lambda r: r.get("executable")),
        # 问题分布
        "issue_counts": dict(issue_counts.most_common()),
    }


# 基线快照文件名（用于对比）
BASELINE_SNAPSHOT_NAME = "quality_baseline_snapshot.json"


def save_baseline_snapshot(
    summary: dict,
    path: str,
    *,
    results: Optional[list] = None,
) -> str:
    """保存基线快照，供后续改动做**前后对比**。

    快照包含：汇总指标 + 每条用例的指标指纹（不含逐条明细，避免文件过大）。
    """
    payload = {
        "summary": summary,
        "case_fingerprints": [
            {
                "id": r.get("id"),
                "name": r.get("normalized_name"),
                "level": r.get("level"),
                "issues": sorted(r.get("issues", [])),
                "consistency_pass_rate": r.get("consistency_pass_rate"),
            }
            for r in (results or [])
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def compare_baselines(
    old: dict,
    new: dict,
    *,
    key: str = "completeness_rate",
) -> dict:
    """对比两份基线，返回每个指标的 delta。

    约定：`higher_is_better` 的指标 delta>0 表示变好。
    """
    higher_better = {
        "completeness_rate": True,
        "level_valid_rate": True,
        "assertion_coverage_rate": True,
        "structure_ok_rate": True,
        "executable_rate": True,
        "consistency_pass_rate": True,
        "consistency_passed_rate": True,
        "duplicate_rate": False,   # 越低越好
    }
    out = {}
    old_s = old.get("summary", old)
    new_s = new.get("summary", new)
    for metric, better_high in higher_better.items():
        o = float(old_s.get(metric, 0) or 0)
        n = float(new_s.get(metric, 0) or 0)
        delta = round(n - o, 4)
        improved = (delta > 0) if better_high else (delta < 0)
        out[metric] = {
            "old": o,
            "new": n,
            "delta": delta,
            "improved": improved if delta != 0 else None,
        }
    return out


def format_baseline_report(summary: dict, *, title: str = "用例质量基线") -> str:
    """把基线汇总格式化成可读文本（供脚本输出/文档引用）。"""
    if not summary or not summary.get("total"):
        return f"# {title}\n\n（无数据）"

    def pct(v) -> str:
        return f"{float(v) * 100:.1f}%"

    lines = [
        f"# {title}",
        "",
        f"- 用例总数：**{summary['total']}**",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 完整率（步骤+预期齐全） | {pct(summary['completeness_rate'])} |",
        f"| 重复率 | {pct(summary['duplicate_rate'])}（{summary['duplicate_groups']} 组 / {summary['duplicate_cases']} 条）|",
        f"| 等级合法率 | {pct(summary['level_valid_rate'])} |",
        f"| 断言覆盖率（接口用例 {summary.get('api_case_count', 0)} 条）| {pct(summary['assertion_coverage_rate'])}（均 {summary.get('avg_assertions_per_api_case', 0)} 条/用例）|",
        f"| 预期可判定性（功能用例 {summary.get('functional_case_count', 0)} 条）| {pct(summary.get('expected_verifiable_rate', 0))} |",
        f"| 结构规范率 | {pct(summary['structure_ok_rate'])} |",
        f"| 可执行率（有接口或场景） | {pct(summary['executable_rate'])} |",
    ]
    if summary.get("consistency_checked"):
        lines.append(
            f"| 接口用例一致性（{summary['consistency_checked']} 条已检）| "
            f"平均 {pct(summary['consistency_pass_rate'])}，全通过 {pct(summary['consistency_passed_rate'])} |"
        )

    lines += [
        "",
        "## 等级分布",
        "",
        "| 等级 | 数量 |",
        "|---|---|",
    ]
    for k, v in (summary.get("level_distribution") or {}).items():
        lines.append(f"| {k} | {v} |")

    if summary.get("issue_counts"):
        lines += [
            "",
            "## 问题分布（Top 10）",
            "",
            "| 问题 | 出现次数 |",
            "|---|---|",
        ]
        for k, v in list(summary["issue_counts"].items())[:10]:
            lines.append(f"| {k} | {v} |")

    return "\n".join(lines)
