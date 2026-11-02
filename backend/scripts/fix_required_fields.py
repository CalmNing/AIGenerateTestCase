"""存量用例的必填字段补全脚本（B6）。

背景：质量基线实测存量 1574 条用例中有 442 处必填字段缺失，
导致接口用例与 Schema 一致性只有 76.2%。

用法（从 backend 目录）：
    python scripts/fix_required_fields.py            # 只报告，不改数据（默认 dry-run）
    python scripts/fix_required_fields.py --apply    # 实际写入
    python scripts/fix_required_fields.py --apply --session-id 3

安全：
- 默认 dry-run，必须显式 --apply 才写库；
- 只补「缺失」字段，绝不覆盖已有值；
- 只处理接口用例（有 api_endpoint_id 且步骤含 endpoint 引用）；
- 写入前会打印将变更的用例数与字段数。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.services.required_field_filler import (  # noqa: E402
    dependency_candidates,
    fill_testcase_steps,
)
from db.db import engine  # noqa: E402
from db.models import ApiEndpoint, TestCase  # noqa: E402


def _ordinal_map(tc):
    raw = str(getattr(tc, "api_endpoint_id", "") or "")
    ids = [int(p) for p in raw.split(",") if p.strip().isdigit()]
    return {i + 1: eid for i, eid in enumerate(ids)}


def run(apply=False, session_id=None, limit=None):
    with Session(engine) as db:
        stmt = select(TestCase).where(TestCase.api_endpoint_id.is_not(None))
        if session_id is not None:
            stmt = stmt.where(TestCase.session_id == session_id)
        if limit:
            stmt = stmt.limit(limit)
        testcases = db.exec(stmt).all()

        all_ids = set()
        for tc in testcases:
            all_ids.update(_ordinal_map(tc).values())
        if all_ids:
            rows = db.exec(
                select(ApiEndpoint).where(ApiEndpoint.id.in_(list(all_ids)))
            ).all()
        else:
            rows = []
        schema_by_id = {
            e.id: (e.request_schema if isinstance(e.request_schema, dict) else {})
            for e in rows
        }

        changed_cases = 0
        total_fields = 0
        field_counter = Counter()
        dep_counter = Counter()
        samples = []

        for tc in testcases:
            steps = tc.steps or []
            if not isinstance(steps, list) or not steps:
                continue
            new_steps, filled = fill_testcase_steps(
                steps, schema_by_id, endpoint_id_by_ordinal=_ordinal_map(tc),
            )
            if not filled:
                continue

            changed_cases += 1
            total_fields += len(filled)
            flat_names = []
            for item in filled:
                names = item.split(": ", 1)[-1]
                for name in names.split(", "):
                    field_counter[name] += 1
                    flat_names.append(name)
            for dep in dependency_candidates(flat_names):
                dep_counter[dep] += 1

            if len(samples) < 3:
                samples.append(
                    "用例 %s「%s」补: %s" % (
                        tc.id, (tc.case_name or "")[:24], filled[:2],
                    )
                )

            if apply:
                tc.steps = new_steps
                db.add(tc)

        if apply and changed_cases:
            db.commit()

        return {
            "mode": "apply" if apply else "dry-run",
            "scanned": len(testcases),
            "changed_cases": changed_cases,
            "total_fields": total_fields,
            "field_counter": dict(field_counter.most_common(15)),
            "dependency_candidates": dict(dep_counter.most_common(10)),
            "samples": samples,
        }


def main():
    parser = argparse.ArgumentParser(description="存量用例必填字段补全（B6）")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只报告）")
    parser.add_argument("--session-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    result = run(apply=args.apply, session_id=args.session_id, limit=args.limit)

    print("# 必填字段补全（%s）" % result["mode"])
    print()
    print("- 扫描用例：%d" % result["scanned"])
    print("- 需变更用例：%d" % result["changed_cases"])
    print("- 补齐字段总数：%d" % result["total_fields"])
    if result["field_counter"]:
        print()
        print("## 补齐的字段 Top 15")
        print()
        print("| 字段 | 次数 |")
        print("|---|---|")
        for k, v in result["field_counter"].items():
            print("| %s | %d |" % (k, v))
    if result["dependency_candidates"]:
        print()
        print("## 依赖候选（理想值来自上游接口）")
        print()
        print("| 字段 | 次数 |")
        print("|---|---|")
        for k, v in result["dependency_candidates"].items():
            print("| %s | %d |" % (k, v))
    if result["samples"]:
        print()
        print("## 样例")
        print()
        for s in result["samples"]:
            print("- %s" % s)
    if not args.apply:
        print()
        print("> 这是 dry-run。加 --apply 才会真正写入数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())