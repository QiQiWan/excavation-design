from __future__ import annotations

"""End-to-end smoke for editable stages and selective result evidence loading."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.calculation.engine import build_default_construction_cases
from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project, StageCalculationResult
from app.services.calculation_state import mark_calculation_state_current
from app.services.construction_stages import (
    build_construction_stage_workspace,
    normalize_user_calculation_case,
    select_calculation_case_for_run,
)
from app.services.deepening_readiness import calculation_readiness
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.version import SOFTWARE_VERSION


def main() -> int:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pitguard-v353-stage-evidence-") as temp_dir:
        root = Path(temp_dir)
        os.environ["PITGUARD_DB_PATH"] = str(root / "pitguard.sqlite3")
        os.environ["PITGUARD_ARTIFACT_ROOT"] = str(root / "artifacts")
        os.environ["PITGUARD_PROCESS_ROLE"] = "api"

        excavation = make_excavation_model(
            "v353-smoke",
            Polyline2D(points=[
                Point2D(x=0, y=0), Point2D(x=36, y=0),
                Point2D(x=36, y=20), Point2D(x=0, y=20),
            ], closed=True),
            0.0,
            -11.0,
        )
        project = Project(
            name="V3.53 construction-stage evidence smoke",
            excavation=excavation,
            retainingSystem=auto_diaphragm_wall(excavation),
        )
        project.design_settings.design_basis_confirmed = True
        generated = build_default_construction_cases(project)[0]
        generated.name = "已确认施工阶段"
        locked = normalize_user_calculation_case(project, generated)
        project.calculation_cases = [locked]

        wall = project.retaining_system.diaphragm_walls[0]
        result = CalculationResult(
            projectId=project.id,
            caseId=locked.id,
            stageResults=[StageCalculationResult(
                stageId=locked.stages[-1].id,
                segmentId=wall.segment_id,
                pressureProfile={"points": []},
            )],
            checkSummary={"pass": 1, "warning": 0, "fail": 0},
        )
        project.calculation_results = [result]
        mark_calculation_state_current(project, result.id)

        repo = ProjectRepository(SQLiteProjectStore(root / "pitguard.sqlite3"))
        repo.save(project, action="smoke.v353", summary="Persist staged-result smoke project")
        compact = repo.require_workspace(project.id)
        if compact.calculation_results[-1].stage_results:
            raise RuntimeError("workspace unexpectedly retained the heavy stage array")
        summary = compact.calculation_results[-1].stage_result_summary
        if int(summary.get("actualCount") or 0) != 1 or summary.get("storageState") != "externalized":
            raise RuntimeError(f"stage persistence summary is incomplete: {summary}")

        hydrated = repo.require_workspace_with_latest_calculation(project.id)
        readiness = calculation_readiness(hydrated)
        if readiness.get("stageEvidenceState") != "loaded" or not readiness.get("valid"):
            raise RuntimeError(f"selective stage evidence did not close: {readiness}")
        selected, decision = select_calculation_case_for_run(hydrated)
        if selected.id != locked.id or not decision.get("preserved"):
            raise RuntimeError("locked construction stages were replaced")
        stage_workspace = build_construction_stage_workspace(hydrated)
        if len(stage_workspace.get("inputGuide") or []) < 7:
            raise RuntimeError("construction-stage input guide is incomplete")

        storage = repo.store.get_payload_info(project.id) or {}
        output = {
            "status": "success",
            "version": SOFTWARE_VERSION,
            "elapsedSeconds": round(time.perf_counter() - started, 3),
            "workspaceStageResultCount": len(compact.calculation_results[-1].stage_results),
            "persistedStageResultCount": summary.get("actualCount"),
            "loadedStageResultCount": readiness.get("stageResultCount"),
            "stageEvidenceState": readiness.get("stageEvidenceState"),
            "stageEvidenceComplete": readiness.get("stageEvidenceComplete"),
            "lockedCasePreserved": decision.get("preserved"),
            "constructionStageCount": len(selected.stages),
            "inputGuideCount": len(stage_workspace.get("inputGuide") or []),
            "artifactCount": storage.get("artifactCount"),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
