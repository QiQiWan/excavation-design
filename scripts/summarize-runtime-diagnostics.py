from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _mb(value: Any) -> float:
    try:
        return round(float(value or 0) / 1048576.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _effective_mb(row: dict[str, Any]) -> float:
    direct = row.get("effectiveMemoryMb")
    try:
        if direct is not None and float(direct) > 0:
            return round(float(direct), 2)
    except (TypeError, ValueError):
        pass
    return _mb(row.get("processEffectiveBytes") or row.get("processRssBytes") or row.get("processPrivateBytes"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize PitGuard runtime memory diagnostics")
    parser.add_argument("--runtime", default="runtime", help="runtime directory")
    args = parser.parse_args()
    root = Path(args.runtime).resolve() / "diagnostics"
    print(f"Diagnostics: {root}")

    worker = _read(root / "worker-memory.jsonl")
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in worker:
        by_task[str(row.get("taskId") or "unknown")].append(row)
    if by_task:
        print("\nWorker peak memory")
        for task_id, rows in by_task.items():
            peak = max(rows, key=_effective_mb)
            effective = _effective_mb(peak)
            private = peak.get("privateMb") or _mb(peak.get("processPrivateBytes"))
            rss = peak.get("rssMb") or _mb(peak.get("processRssBytes"))
            available = peak.get("systemAvailableMb") or _mb(peak.get("availableBytes"))
            metric_note = "" if effective > 0 else "（进程内存指标不可用）"
            print(
                f"- {task_id}: effective={effective} MB, private={private} MB, rss={rss} MB, "
                f"stage={peak.get('currentStep', '-')}, available={available} MB{metric_note}"
            )

    candidates = _read(root / "candidate-search.jsonl")
    if candidates:
        complete = [row for row in candidates if row.get("event") == "search-complete"]
        trials = [row for row in candidates if row.get("event") == "trial-complete"]
        print("\nCandidate search")
        print(f"- trial records: {len(trials)}")
        if complete:
            last = complete[-1]
            print(
                f"- latest: trials={last.get('trialCount')}, ranked={last.get('rankedCandidateCount')}, "
                f"effective={_mb(last.get('processEffectiveBytes'))} MB, "
                f"private={_mb(last.get('processPrivateBytes'))} MB"
            )

    storage = _read(root / "project-storage.jsonl")
    serialized = [row for row in storage if row.get("event") == "save-serialized"]
    if serialized:
        print("\nProject storage")
        for row in serialized[-5:]:
            print(
                f"- {row.get('projectId')}: payload={row.get('payloadMb')} MB, "
                f"workspace={row.get('workspaceMb')} MB, external={row.get('externalMb')} MB, "
                f"serialize={row.get('serializeSeconds')} s"
            )

    geometry = _read(root / "candidate-geometry.jsonl")
    if geometry:
        accepted = [row for row in geometry if row.get("event") == "candidate-accepted"]
        rejected = [row for row in geometry if row.get("event") == "candidate-rejected"]
        print("\nCandidate geometry integrity")
        print(f"- accepted={len(accepted)}, rejected={len(rejected)}")
        for row in accepted[-6:]:
            print(f"- rank={row.get('rank')} family={row.get('topologyFamily')} delta={row.get('geometryDelta')} supports={row.get('supportCount')} columns={row.get('columnCount')}")
        reasons = defaultdict(int)
        for row in rejected:
            reasons[str(row.get("reason") or "unknown")] += 1
        if reasons:
            print(f"- rejection reasons={dict(reasons)}")
            duplicate_count = sum(count for reason, count in reasons.items() if reason.startswith("identical_geometry"))
            total = len(accepted) + len(rejected)
            if total and duplicate_count / total >= 0.25:
                print(f"- warning: identical geometry rejection rate={duplicate_count / total:.1%}; reduce snapped-equivalent trial combinations")

    rebar_contract = _read(root / "rebar-contract.jsonl")
    if rebar_contract:
        resolved = [row for row in rebar_contract if row.get("event") == "support-contract-resolved"]
        incomplete = [row for row in resolved if row.get("missingBarTypes")]
        print("\nSupport rebar contract")
        print(f"- resolved supports={len(resolved)}, incomplete={len(incomplete)}")
        for row in incomplete[-10:]:
            print(f"- {row.get('hostCode')}: source={row.get('sourceBarTypes')} resolved={row.get('resolvedBarTypes')} missing={row.get('missingBarTypes')}")

    rebar_visual = _read(root / "rebar-visualization.jsonl")
    if rebar_visual:
        last = rebar_visual[-1]
        print("\nRebar visualization")
        print(f"- sampled={last.get('sampledBarCount')}, available={last.get('totalAvailableBarCount')}, support types={last.get('supportBarTypesPresent')}, missing={last.get('supportBarTypesMissing')}")

    rebar_tasks = _read(root / "rebar-task.jsonl")
    if rebar_tasks:
        print("\nRebar task closure")
        for row in rebar_tasks[-10:]:
            print(f"- {row.get('event')}: task={row.get('taskId')} project={row.get('projectId')} section changes={row.get('sectionChangeCount', row.get('remainingSectionChangeCount', '-'))}")

    lifecycle = _read(root / "task-lifecycle.jsonl")
    failures = [row for row in lifecycle if row.get("event") == "task-finish" and row.get("status") not in {"success", None}]
    if failures:
        print("\nNon-success task finishes")
        for row in failures[-10:]:
            detail = str(row.get("errorMessage") or row.get("message") or "未记录错误详情")
            if len(detail) > 220:
                detail = detail[:217] + "..."
            print(f"- {row.get('taskId')}: {row.get('operation')} status={row.get('status')} stage={row.get('stage', '-')} error={detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
