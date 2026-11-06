from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import hashlib
import os
import time

from app.services.borehole_import import parse_borehole_rows, read_csv_bytes, read_excel_bytes
from app.services.calculation_state import invalidate_calculation_state
from app.storage.artifact_store import ProjectArtifactStore, append_project_artifact_ref
from app.storage.repository import ProjectRepository

ProgressCallback = Callable[[int, str], None]


def import_staging_root() -> Path:
    configured = os.getenv("PITGUARD_IMPORT_STAGING_ROOT")
    if configured:
        root = Path(configured)
    else:
        database = Path(os.getenv("PITGUARD_DB_PATH", str(Path(__file__).resolve().parents[2] / "pitguard.sqlite3")))
        root = database.parent / "import-staging"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # A task can be cancelled after upload but before the worker claims it.
    # Bound stale staging growth without touching recent/running uploads.
    stale_seconds = max(3600, int(os.getenv("PITGUARD_IMPORT_STAGING_TTL_SECONDS", "86400")))
    cutoff = time.time() - stale_seconds
    for candidate in root.iterdir():
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue
    return root


def _validated_staging_path(raw: str) -> Path:
    root = import_staging_root()
    path = Path(raw).resolve()
    if path != root and root not in path.parents:
        raise ValueError("钻孔导入临时文件路径无效。")
    if not path.is_file():
        raise ValueError("钻孔导入临时文件不存在，可能已被清理，请重新选择文件。")
    return path


def execute_borehole_import(
    project_id: str,
    payload: dict[str, Any],
    repo: ProjectRepository,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    staging_path = _validated_staging_path(str(payload.get("stagingPath") or ""))
    original_filename = str(payload.get("originalFilename") or staging_path.name)
    content_type = str(payload.get("contentType") or "application/octet-stream")
    import_type = str(payload.get("importType") or staging_path.suffix.lstrip(".") or "csv").lower()
    expected_sha256 = str(payload.get("sha256") or "")

    try:
        report(8, "读取已暂存的地勘文件")
        raw = staging_path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and expected_sha256 != actual_sha256:
            raise ValueError("上传文件校验失败，临时文件内容发生变化，请重新导入。")

        report(25, "解析钻孔、分层和地下水记录")
        if import_type in {"xlsx", "xlsm", "excel"} or staging_path.suffix.lower() in {".xlsx", ".xlsm"}:
            rows = read_excel_bytes(raw)
            normalized_type = "excel"
        elif import_type == "csv" or staging_path.suffix.lower() == ".csv":
            rows = read_csv_bytes(raw)
            normalized_type = "csv"
        else:
            raise ValueError("仅支持 CSV、XLSX 或 XLSM 钻孔文件。")

        report(48, "校验孔号、层序、标高与参数完整性")
        parsed = parse_borehole_rows(rows, source_file=original_filename)
        response = parsed.as_response()
        if not parsed.success:
            return {
                "projectId": project_id,
                "importResult": response,
                "refreshProject": False,
                "sha256": actual_sha256,
            }

        project = repo.require(project_id)
        report(62, "保存原始地勘文件与可追溯校验值")
        ref = ProjectArtifactStore().write_bytes(
            project.id,
            "engineering-source-evidence",
            raw,
            filename=original_filename,
            content_type=content_type,
            metadata={"domain": "borehole", "importType": normalized_type, "taskBased": True},
        )
        append_project_artifact_ref(project, ref, storage_key=f"borehole-import:{ref['sha256']}")
        for borehole in parsed.boreholes:
            borehole.source_file_sha256 = ref["sha256"]
            borehole.source_artifact_id = ref["artifactId"]
            borehole.source_verified = False
            for record in borehole.water_levels:
                record.source_file = original_filename
                record.source_file_sha256 = ref["sha256"]
                record.source_artifact_id = ref["artifactId"]
                record.quality = "provisional"
                record.verified_by = None

        report(78, "更新工程地勘输入并失效旧计算证据")
        project.boreholes = parsed.boreholes
        project.strata = parsed.strata
        # Imported strata change the design domain.  Keeping an old IDW model or
        # old calculation results would mix two different engineering snapshots.
        project.geological_model = None
        invalidate_calculation_state(
            project,
            reason="borehole and stratum source data changed; geological model and calculations must be rebuilt",
            rebuild_cases=False,
            preserve_cases=False,
            invalidate_candidates=False,
        )
        project.messages.append(
            f"Imported {parsed.borehole_count} boreholes from {original_filename}; geological model and old calculation evidence were invalidated."
        )
        report(90, "提交工程版本并刷新工作区")
        repo.save(
            project,
            action="task.borehole_import",
            summary=f"Imported {parsed.borehole_count} boreholes and {parsed.stratum_count} strata from {original_filename}",
        )
        report(98, "钻孔解析与工程更新完成")
        return {
            "projectId": project.id,
            "importResult": response,
            "refreshProject": True,
            "artifact": ref,
            "sha256": actual_sha256,
        }
    finally:
        staging_path.unlink(missing_ok=True)
