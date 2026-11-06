from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_core_mode_registers_calculation_stage_dataset_routes() -> None:
    api_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PITGUARD_PRODUCT_MODE"] = "core"
    env["PYTHONPATH"] = str(api_dir)
    code = (
        "from app.main import app; "
        "paths={getattr(r,'path','') for r in app.routes}; "
        "assert '/api/projects/{project_id}/calculation-results/{result_id}/stage-chunks' in paths; "
        "assert '/api/projects/{project_id}/calculation-results/{result_id}/stage-chunks/{chunk_index}' in paths; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=api_dir, env=env, text=True, capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_cloud_viewer_uses_quiet_dataset_requests_and_authoritative_fallback() -> None:
    root = Path(__file__).resolve().parents[3]
    client = (root / "apps/web/src/api/client.ts").read_text(encoding="utf-8")
    viewer = (root / "apps/web/src/viewers/ResultViewer.tsx").read_text(encoding="utf-8")
    assert "getCalculationStageChunks" in client and "quiet: true" in client
    assert "getLatestCalculationEvidenceForViewer" in client
    assert "MAX_VIEWER_CHUNKS" in viewer
    assert "hydrateFromLatestEvidence" in viewer
