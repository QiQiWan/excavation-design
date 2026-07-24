#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import run_single_candidate_calculation
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def project() -> Project:
    excavation = make_excavation_model(
        "V3.87 candidate worker",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    return Project(name="V3.87 candidate worker", excavation=excavation, retainingSystem=retaining)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", choices=TEMPLATES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    total_started = time.perf_counter()
    item = project()
    search_started = time.perf_counter()
    _, candidates = optimize_support_layout_candidates(
        item,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 15,
            "candidatePoolLimit": 10,
        },
    )
    search_seconds = time.perf_counter() - search_started
    candidate = next(row for row in candidates if row.variable_summary.get("transferSystemTemplate") == args.template)
    full_started = time.perf_counter()
    summary = run_single_candidate_calculation(item, candidate, index=TEMPLATES.index(args.template), use_cache=False)
    full_seconds = time.perf_counter() - full_started
    candidate_payload = {
        "id": candidate.id,
        "rank": candidate.rank,
        "score": candidate.score,
        "status": candidate.status,
        "supportCount": candidate.support_count,
        "columnCount": candidate.column_count,
        "metrics": candidate.metrics,
        "hardConstraints": candidate.hard_constraints,
        "variableSummary": candidate.variable_summary,
        "fullCalculation": summary,
    }
    runtime = {
        "searchSeconds": round(search_seconds, 3),
        "fullCalculationSeconds": round(full_seconds, 3),
        "totalSeconds": round(time.perf_counter() - total_started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"candidate": candidate_payload, "summary": summary, "runtime": runtime}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
