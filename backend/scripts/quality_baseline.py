"""用例质量基线脚本（B6）。

用法（从 backend 目录）：
    python -m scripts.quality_baseline              # 打印基线报告
    python -m scripts.quality_baseline --json out.json   # 同时导出 JSON
    python -m scripts.quality_baseline --session-id 3    # 只看某个会话

只读，不修改任何数据；不调 LLM，零 API 成本。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 允许以脚本方式直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.services.quality_metrics import (  # noqa: E402
    format_baseline_report,
    summarize_metrics,
)
from db.db import engine  # noqa: E402
from db.models import ApiEndpoint, TestCase  # noqa: E402


def _build_endpoint_context(db: Session, testcases: list) -> tuple[dict, dict]:
    """构建 (endpoint_index_to_id, endpoints) 供一致性检查使用。

    每条用例用自己的 `api_endpoint_id`（逗号分隔）构造自己的序号映射——
    因为 pipeline 的 endpoint_ref 是相对该用例所选接口集的序号。
    """
    endpoint_ids: set[int] = set()
    for tc in testcases:
        raw = str(getattr(tc, "api_endpoint_id", "") or "")
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                endpoint_ids.add(int(part))

    endpoints: dict = {}
    if endpoint_ids:
        rows = db.exec(
            select(ApiEndpoint).where(ApiEndpoint.id.in_(list(endpoint_ids)))
        ).all()
        endpoints = {e.id: e for e in rows}
    return endpoints, endpoints


def _per_case_index_map(tc) -> dict:
    """单条用例的 endpoint 序号 → ID 映射。"""
    raw = str(getattr(tc, "api_endpoint_id", "") or "")
    ids = [int(p) for p in raw.split(",") if p.strip().isdigit()]
    return {i + 1: eid for i, eid in enumerate(ids)}


def run(session_id: int | None = None, module_id: int | None = None) -> tuple[dict, list]:
    with Session(engine) as db:
        stmt = select(TestCase)
        if session_id is not None:
            stmt = stmt.where(TestCase.session_id == session_id)
        if module_id is not None:
            stmt = stmt.where(TestCase.module_id == module_id)
        testcases = db.exec(stmt).all()

        all_endpoints, _ = _build_endpoint_context(db, testcases)

        results = []
        for tc in testcases:
            idx_map = _per_case_index_map(tc)
            eps = {eid: all_endpoints[eid] for eid in idx_map.values() if eid in all_endpoints}
            try:
                from app.services.quality_metrics import evaluate_testcase

                results.append(evaluate_testcase(
                    tc, endpoint_index_to_id=idx_map, endpoints=eps,
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] 用例 {getattr(tc, 'id', '?')} 指标计算失败: {exc}", file=sys.stderr)

        summary = summarize_metrics(results)
        return summary, results


def main() -> int:
    parser = argparse.ArgumentParser(description="用例质量基线（B6）")
    parser.add_argument("--session-id", type=int, default=None)
    parser.add_argument("--module-id", type=int, default=None)
    parser.add_argument("--json", dest="json_path", default=None)
    parser.add_argument("--save-snapshot", dest="save_snapshot", default=None,
                        help="保存基线快照（用于后续改动前后对比）")
    parser.add_argument("--compare", dest="compare", default=None,
                        help="与已有基线快照对比")
    args = parser.parse_args()

    summary, results = run(args.session_id, args.module_id)
    print(format_baseline_report(summary))

    if args.json_path:
        payload = {
            "summary": summary,
            "cases": [
                {k: v for k, v in r.items() if k != "consistency_checks"}
                for r in results
            ],
        }
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\n已导出 JSON：{args.json_path}")

    # --save-snapshot：保存基线快照（供后续改动前后对比）
    if args.save_snapshot:
        from app.services.quality_metrics import save_baseline_snapshot

        path = save_baseline_snapshot(summary, args.save_snapshot, results=results)
        print(f"已保存基线快照：{path}")

    # --compare：与已有快照对比
    if args.compare:
        from app.services.quality_metrics import compare_baselines

        with open(args.compare, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        deltas = compare_baselines(old, {"summary": summary})
        print(f"\n## 与基线对比（{args.compare}）\n")
        print("| 指标 | 基线 | 当前 | Δ | 结论 |")
        print("|---|---|---|---|---|")
        for metric, d in deltas.items():
            verdict = "-" if d["improved"] is None else ("↑ 变好" if d["improved"] else "↓ 变差")
            print(f"| {metric} | {d['old']:.4f} | {d['new']:.4f} | {d['delta']:+.4f} | {verdict} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
