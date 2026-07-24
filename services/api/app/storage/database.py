from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Lock
from uuid import uuid4

from app.storage.artifact_store import ProjectArtifactStore, artifact_refs, externalize_project_payload, rehydrate_project_payload
from app.services.runtime_resource_policy import adaptive_resource_policy, classify_payload
from app.services.runtime_diagnostics import append_event, memory_event

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "pitguard.sqlite3"


class ProjectPayloadTooLarge(RuntimeError):
    """Raised before a full project JSON document is copied into the API heap."""

    def __init__(self, project_id: str, payload_bytes: int, limit_bytes: int) -> None:
        self.project_id = project_id
        self.payload_bytes = int(payload_bytes)
        self.limit_bytes = int(limit_bytes)
        super().__init__(
            f"Project {project_id} payload is {payload_bytes / 1048576:.1f} MB; "
            f"the API full-load limit is {limit_bytes / 1048576:.1f} MB. "
            "Open the workspace profile or run storage compaction before a full load."
        )


def _process_role() -> str:
    return str(os.getenv("PITGUARD_PROCESS_ROLE", "api")).strip().lower() or "api"


def _api_full_load_limit_bytes() -> int:
    return int(adaptive_resource_policy(role=_process_role())["apiFullLoadLimitBytes"])


def _compact_candidate_calculation_summary(value: Any) -> dict[str, Any]:
    """Keep the auditable A/B/C decision summary without heavy result arrays."""
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {
        key: item for key, item in value.items()
        if isinstance(item, (str, int, float, bool)) or item is None
    }
    for key in (
        "checkSummary", "decisionComponentScores", "geologyCoverage",
        "calculationDiagnostics", "transferReadiness", "pareto",
    ):
        item = value.get(key)
        if isinstance(item, dict):
            compact[key] = item
    for key in ("blockers", "warnings", "decisionReasons"):
        item = value.get(key)
        if isinstance(item, list):
            compact[key] = item[:20]
    return compact



def _compact_calculation_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}
    compact["phases"] = [
        {key: item for key, item in row.items() if key in {"phaseId", "label", "status", "durationSeconds", "message", "blockerCount"}}
        for row in list(value.get("phases") or [])[:20] if isinstance(row, dict)
    ]
    if isinstance(value.get("bottleneckPhase"), dict):
        compact["bottleneckPhase"] = {
            key: item for key, item in value["bottleneckPhase"].items()
            if key in {"phaseId", "label", "durationSeconds", "durationSharePercent"}
        }
    return compact


def _compact_numerical_health(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}
    for key in ("reactionIteration", "conditionNumber", "matrixHealth", "sensitivity"):
        row = value.get(key)
        if isinstance(row, dict):
            compact[key] = {
                nested_key: nested_value for nested_key, nested_value in row.items()
                if isinstance(nested_value, (str, int, float, bool)) or nested_value is None
            }
    return compact


def _compact_result_completeness(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}
    compact["criticalBlockingDomains"] = list(value.get("criticalBlockingDomains") or [])[:30]
    if isinstance(value.get("readinessPolicy"), dict):
        compact["readinessPolicy"] = value["readinessPolicy"]
    compact["domains"] = []
    for row in list(value.get("domains") or [])[:40]:
        if not isinstance(row, dict):
            continue
        domain = {
            key: item for key, item in row.items()
            if key in {"domainId", "label", "status", "coveragePercent", "message"}
        }
        evidence = row.get("evidence")
        if isinstance(evidence, dict):
            domain["evidence"] = {
                key: item for key, item in evidence.items()
                if isinstance(item, (str, int, float, bool)) or item is None
            }
        compact["domains"].append(domain)
    return compact


def _compact_result_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}
    for key in ("counts", "ruleStatusCounts", "reinforcementInventory"):
        if isinstance(value.get(key), dict):
            compact[key] = value[key]
    compact["criticalStages"] = list(value.get("criticalStages") or [])[:10]
    for key in ("blockingCheckLedger", "warningCheckLedger", "manualReviewLedger"):
        compact[key] = list(value.get(key) or [])[:50]
    # Per-member envelopes, stage matrices and node arrays remain external.
    compact["workspaceSummaryOnly"] = True
    return compact

def _compact_result_for_workspace(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep = {
        "id", "projectId", "caseId", "supportTopologyHash", "inputSnapshotHash",
        "adoptedDesignSnapshotHash", "calculationContractId", "resultHash",
        "calculationAssurance", "deliveryReadiness", "stageResultSummary", "governingValues", "warnings",
        "checkSummary", "designIterationSummary", "designReviewSummary",
        "supportLayoutQuality", "ifcCompatibility", "formalReportGate", "standards",
        "professionalReviewRequired", "calculatedAt",
    }
    compact = {key: value[key] for key in keep if key in value}
    execution = value.get("calculationExecution") or value.get("calculation_execution")
    numerical = value.get("numericalHealth") or value.get("numerical_health")
    completeness = value.get("resultCompleteness") or value.get("result_completeness")
    catalog = value.get("resultCatalog") or value.get("result_catalog")
    compact["calculationExecution"] = _compact_calculation_execution(execution)
    compact["numericalHealth"] = _compact_numerical_health(numerical)
    compact["resultCompleteness"] = _compact_result_completeness(completeness)
    compact["resultCatalog"] = _compact_result_catalog(catalog)
    repair = value.get("supportLayoutRepair") or value.get("support_layout_repair")
    if isinstance(repair, dict):
        compact["supportLayoutRepair"] = {
            "selectedCandidateId": repair.get("selectedCandidateId", repair.get("selected_candidate_id")),
            "bestCandidateId": repair.get("bestCandidateId", repair.get("best_candidate_id")),
            "candidateFullCalculations": [
                _compact_candidate_calculation_summary(row)
                for row in list(repair.get("candidateFullCalculations") or repair.get("candidate_full_calculations") or [])[:12]
                if isinstance(row, dict)
            ],
        }
    report = value.get("reportDiagramData") or value.get("report_diagram_data")
    compact_report: dict[str, Any] = {}
    if isinstance(report, dict):
        if isinstance(report.get("calculationExecution"), dict):
            compact_report["calculationExecution"] = _compact_calculation_execution(report["calculationExecution"])
        if isinstance(report.get("numericalHealth"), dict):
            compact_report["numericalHealth"] = _compact_numerical_health(report["numericalHealth"])
        if isinstance(report.get("resultCompleteness"), dict):
            compact_report["resultCompleteness"] = _compact_result_completeness(report["resultCompleteness"])
        if isinstance(report.get("resultCatalog"), dict):
            compact_report["resultCatalog"] = _compact_result_catalog(report["resultCatalog"])
        rows = report.get("candidateFullCalculationComparison")
        if isinstance(rows, list):
            compact_report["candidateFullCalculationComparison"] = [
                _compact_candidate_calculation_summary(row) for row in rows[:12] if isinstance(row, dict)
            ]
    compact["reportDiagramData"] = compact_report
    # Preserve the result contract while excluding stage matrices and diagrams.
    compact.setdefault("stageResults", [])
    compact.setdefault("checks", [])
    compact.setdefault("optimizationActions", [])
    compact.setdefault("drawingSheets", [])
    compact.setdefault("supportLayoutRepair", None)
    compact.setdefault("stabilityDetailedResult", None)
    return compact


def _downsample_surface_grid(grid: Any, max_axis: int = 64) -> Any:
    if not isinstance(grid, dict):
        return grid
    xs = list(grid.get("xValues") or [])
    ys = list(grid.get("yValues") or [])
    zs = list(grid.get("zValues") or [])
    if len(xs) <= max_axis and len(ys) <= max_axis:
        return grid
    x_step = max(1, (len(xs) + max_axis - 1) // max_axis)
    y_step = max(1, (len(ys) + max_axis - 1) // max_axis)
    x_idx = list(range(0, len(xs), x_step))
    y_idx = list(range(0, len(ys), y_step))
    if xs and x_idx[-1] != len(xs) - 1:
        x_idx.append(len(xs) - 1)
    if ys and y_idx[-1] != len(ys) - 1:
        y_idx.append(len(ys) - 1)
    sampled_rows: list[list[Any]] = []
    for yi in y_idx:
        row = zs[yi] if yi < len(zs) and isinstance(zs[yi], list) else []
        sampled_rows.append([row[xi] if xi < len(row) else None for xi in x_idx])
    return {
        **grid,
        "xValues": [xs[i] for i in x_idx],
        "yValues": [ys[i] for i in y_idx],
        "zValues": sampled_rows,
        "workspaceDownsampled": True,
        "sourceShape": [len(ys), len(xs)],
    }


def _workspace_limit_bytes() -> int:
    return int(adaptive_resource_policy(role=_process_role())["workspaceLimitBytes"])


CANDIDATE_PREVIEW_SCHEMA = "candidate-plan-v3"


def _compact_candidate_plan_geometry(
    value: Any,
    *,
    max_supports: int = 4000,
    max_columns: int = 4000,
    max_transfer_beams: int = 4000,
    max_transfer_zones: int = 200,
) -> dict[str, Any]:
    """Create a bounded, topology-complete and self-auditing plan preview.

    V3 rejects invalid coordinates instead of drawing phantom members at (0, 0),
    preserves transfer-system readiness, and declares any collection truncation.
    The browser can therefore distinguish a genuinely open topology from a
    deliberately bounded preview.
    """
    if not isinstance(value, dict):
        return {}

    invalid_point_count = 0

    def compact_point(point: Any) -> dict[str, float] | None:
        nonlocal invalid_point_count
        if not isinstance(point, dict):
            invalid_point_count += 1
            return None
        try:
            x = float(point.get("x"))
            y = float(point.get("y"))
        except (TypeError, ValueError):
            invalid_point_count += 1
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            invalid_point_count += 1
            return None
        return {"x": round(x, 6), "y": round(y, 6)}

    raw_outline = list(value.get("outline") or [])
    outline = [row for row in (compact_point(point) for point in raw_outline[:2000]) if row]

    raw_supports = list(value.get("supports") or [])
    supports = []
    invalid_member_count = 0
    for support in raw_supports[:max_supports]:
        if not isinstance(support, dict):
            invalid_member_count += 1
            continue
        start_point = compact_point(support.get("start"))
        end_point = compact_point(support.get("end"))
        if not start_point or not end_point:
            invalid_member_count += 1
            continue
        supports.append({
            "id": support.get("id"),
            "code": support.get("code"),
            "role": support.get("role", support.get("supportRole")),
            "supportRole": support.get("supportRole", support.get("role")),
            "levelIndex": support.get("levelIndex"),
            "elevation": support.get("elevation"),
            "topologyFamily": support.get("topologyFamily"),
            "spanLength": support.get("spanLength"),
            "baySpacing": support.get("baySpacing"),
            "locked": bool(support.get("locked")),
            "lockState": support.get("lockState") if isinstance(support.get("lockState"), dict) else {},
            "changed": bool(support.get("changed")),
            "start": start_point,
            "end": end_point,
        })

    raw_columns = list(value.get("columns") or [])
    columns = []
    for column in raw_columns[:max_columns]:
        if not isinstance(column, dict):
            invalid_member_count += 1
            continue
        location = compact_point(column.get("location"))
        if not location:
            invalid_member_count += 1
            continue
        columns.append({"id": column.get("id"), "code": column.get("code"), "location": location})

    raw_transfer_beams = list(value.get("transferBeams") or value.get("transfer_beams") or [])
    transfer_beams = []
    member_point_truncated = False
    for beam in raw_transfer_beams[:max_transfer_beams]:
        if not isinstance(beam, dict):
            invalid_member_count += 1
            continue
        raw_points = list(beam.get("points") or [])
        if len(raw_points) > 500:
            member_point_truncated = True
        points = [row for row in (compact_point(point) for point in raw_points[:500]) if row]
        if len(points) < 2:
            invalid_member_count += 1
            continue
        transfer_beams.append({
            "id": beam.get("id"),
            "code": beam.get("code"),
            "role": beam.get("role", beam.get("beamRole")),
            "beamRole": beam.get("beamRole", beam.get("role")),
            "elevation": beam.get("elevation"),
            "supportLevel": beam.get("supportLevel"),
            "points": points,
        })

    raw_transfer_zones = list(value.get("transferZones") or value.get("transfer_zones") or [])
    transfer_zones = []
    zone_point_truncated = False
    for zone in raw_transfer_zones[:max_transfer_zones]:
        if not isinstance(zone, dict):
            continue
        raw_zone_outline = list(zone.get("outline") or [])
        if len(raw_zone_outline) > 1000:
            zone_point_truncated = True
        zone_outline = [row for row in (compact_point(point) for point in raw_zone_outline[:1000]) if row]
        transfer_zones.append({
            "id": zone.get("id"),
            "zoneId": zone.get("zoneId"),
            "zoneType": zone.get("zoneType"),
            "outline": zone_outline,
        })

    raw_obstacles = list(value.get("obstacles") or [])
    obstacles = []
    for obstacle in raw_obstacles[:200]:
        if not isinstance(obstacle, dict):
            continue
        points = [row for row in (compact_point(point) for point in list(obstacle.get("points") or [])[:1000]) if row]
        obstacles.append({
            "id": obstacle.get("id"),
            "name": obstacle.get("name"),
            "type": obstacle.get("type"),
            "points": points,
        })

    layout_summary = value.get("layoutSummary") if isinstance(value.get("layoutSummary"), dict) else {}
    transfer_audit_raw = value.get("transferAudit") if isinstance(value.get("transferAudit"), dict) else layout_summary.get("transferSystem")
    transfer_audit_raw = transfer_audit_raw if isinstance(transfer_audit_raw, dict) else {}
    readiness = transfer_audit_raw.get("readiness") if isinstance(transfer_audit_raw.get("readiness"), dict) else {}
    transfer_audit = {
        "templateId": transfer_audit_raw.get("templateId"),
        "topologyClass": transfer_audit_raw.get("topologyClass"),
        "status": transfer_audit_raw.get("status"),
        "calculationReady": transfer_audit_raw.get("calculationReady"),
        "formalCalculationReady": transfer_audit_raw.get("formalCalculationReady"),
        "readiness": {
            key: readiness.get(key) for key in (
                "geometryClosed", "loadPathClosed", "structuralModelClosed",
                "constructionStageClosed", "formalCalculationReady",
            ) if key in readiness
        },
    }

    truncation = {
        "outline": len(raw_outline) > 2000,
        "supports": len(raw_supports) > max_supports,
        "columns": len(raw_columns) > max_columns,
        "transferBeams": len(raw_transfer_beams) > max_transfer_beams or member_point_truncated,
        "transferZones": len(raw_transfer_zones) > max_transfer_zones or zone_point_truncated,
        "obstacles": len(raw_obstacles) > 200,
    }
    preview_truncated = any(truncation.values())
    expected_transfer = bool(raw_transfer_beams or transfer_audit.get("templateId") not in {None, "", "none"})
    transfer_geometry_present = bool(transfer_beams)
    if invalid_point_count or invalid_member_count or (expected_transfer and not transfer_geometry_present):
        integrity_status = "incomplete"
        integrity_message = "预览存在无效坐标、无效构件或缺失的转接体系，不能据此判断闭合。"
    elif preview_truncated:
        integrity_status = "warning"
        integrity_message = "预览为有界抽样，完整构件仍保存在工程模型中；正式判断应读取完整拓扑。"
    else:
        integrity_status = "complete"
        integrity_message = "预览已完整保留普通支撑、转接梁、闭合环梁和立柱。"

    return {
        "outline": outline,
        "supports": supports,
        "columns": columns,
        "transferBeams": transfer_beams,
        "transferZones": transfer_zones,
        "obstacles": obstacles,
        "supportElevations": list(value.get("supportElevations") or [])[:100],
        "transferAudit": transfer_audit,
        "previewIntegrity": {
            "status": integrity_status,
            "message": integrity_message,
            "truncated": preview_truncated,
            "truncation": truncation,
            "invalidPointCount": invalid_point_count,
            "invalidMemberCount": invalid_member_count,
            "expectedTransferSystem": expected_transfer,
            "transferGeometryPresent": transfer_geometry_present,
        },
        "previewSchema": CANDIDATE_PREVIEW_SCHEMA,
        "sourceSupportCount": len(raw_supports),
        "renderedSupportCount": len(supports),
        "sourceColumnCount": len(raw_columns),
        "renderedColumnCount": len(columns),
        "sourceTransferBeamCount": len(raw_transfer_beams),
        "renderedTransferBeamCount": len(transfer_beams),
        "sourceTransferZoneCount": len(raw_transfer_zones),
        "renderedTransferZoneCount": len(transfer_zones),
    }


def _compact_candidate_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            compact[key] = item
    for key in ("wallJunctionPoints", "junctionPoints"):
        rows = []
        for row in list(value.get(key) or [])[:120]:
            if not isinstance(row, dict):
                continue
            point = row.get("point") if isinstance(row.get("point"), dict) else {}
            rows.append({
                "nodeType": row.get("nodeType"),
                "highDegree": row.get("highDegree"),
                "supportCodes": list(row.get("supportCodes") or [])[:12],
                "point": {"x": point.get("x"), "y": point.get("y")},
            })
        compact[key] = rows
    return compact


def _compact_candidate_for_workspace(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {}
    scalar_or_small_keys = {
        "id", "rank", "score", "status", "targetSpacing", "target_spacing",
        "columnMaxSpan", "column_max_span", "hardConstraints", "hard_constraints",
        "variableSummary", "variable_summary", "weightSummary", "weight_summary",
        "issueCount", "issue_count", "failCount", "fail_count",
        "warningCount", "warning_count", "supportCount", "support_count",
        "columnCount", "column_count", "maxSpanLength", "max_span_length",
        "maxBaySpacing", "max_bay_spacing", "crossingCount", "crossing_count",
        "junctionCount", "junction_count", "highDegreeJunctionCount",
        "high_degree_junction_count", "wallJunctionCount", "wall_junction_count",
        "planIntersectionComplexity", "plan_intersection_complexity",
        "obstacleConflictCount", "obstacle_conflict_count", "axialPeakProxy",
        "axial_peak_proxy", "symmetryScore", "symmetry_score",
        "muckPathContinuityScore", "muck_path_continuity_score",
        "exportReadiness", "export_readiness", "constructabilityNote",
        "constructability_note", "objectiveTerms", "objective_terms",
        "softObjectives", "soft_objectives",
    }
    compact = {key: candidate[key] for key in scalar_or_small_keys if key in candidate}
    compact["planGeometry"] = _compact_candidate_plan_geometry(
        candidate.get("planGeometry", candidate.get("plan_geometry"))
    )
    compact["metrics"] = _compact_candidate_metrics(candidate.get("metrics"))
    compact["lineAdjustments"] = list(candidate.get("lineAdjustments") or candidate.get("line_adjustments") or [])[:24]
    compact["deltaGeometry"] = {}
    compact["fullCalculation"] = _compact_candidate_calculation_summary(
        candidate.get("fullCalculation") or candidate.get("full_calculation")
    )
    compact["workspacePreviewAvailable"] = bool(
        compact["planGeometry"].get("outline")
        and (compact["planGeometry"].get("supports") or compact["planGeometry"].get("transferBeams"))
    )
    return compact


def _aggressively_compact_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    bounded = dict(workspace)
    geological = bounded.get("geologicalModel")
    if isinstance(geological, dict):
        geo = dict(geological)
        geo["surfaces"] = []
        geo["volumes"] = []
        geo["vtuMesh"] = None
        warnings = list(geo.get("warnings") or [])
        warnings.append("工作区采用轻量地质摘要；进入地质页后按需读取完整模型。")
        geo["warnings"] = warnings
        bounded["geologicalModel"] = geo
    excavation = bounded.get("excavation")
    if isinstance(excavation, dict):
        exc = dict(excavation)
        exc["drawingLayers"] = []
        bounded["excavation"] = exc
    retaining = bounded.get("retainingSystem")
    if isinstance(retaining, dict):
        ret = dict(retaining)
        layout_summary = dict(ret.get("layoutSummary") or {})
        layout_summary.pop("autoRepair", None)
        layout_summary.pop("supportOptimizationCandidates", None)
        ret["layoutSummary"] = layout_summary
        repair = ret.get("supportLayoutRepair")
        if isinstance(repair, dict):
            rep = dict(repair)
            rep["candidates"] = [
                _compact_candidate_for_workspace(candidate)
                for candidate in list(rep.get("candidates") or [])[:8]
                if isinstance(candidate, dict)
            ]
            rep["candidateFullCalculations"] = [
                _compact_candidate_calculation_summary(row)
                for row in list(repair.get("candidateFullCalculations") or repair.get("candidate_full_calculations") or [])[:6]
                if isinstance(row, dict)
            ]
            ret["supportLayoutRepair"] = rep
        bounded["retainingSystem"] = ret
    bounded["monitoringRecords"] = []
    advanced = dict(bounded.get("advancedEngineering") or {})
    workspace_meta = dict(advanced.get("workspaceStorage") or {})
    workspace_meta["aggressivelyCompacted"] = True
    workspace_meta["workspaceLimitBytes"] = _workspace_limit_bytes()
    bounded["advancedEngineering"] = {
        key: value for key, value in advanced.items()
        if key in {
            "calculationState", "requiresRecalculation", "invalidationReason",
            "wallLengthOptimization", "supportDesignerAudit", "planShapeDiagnostics",
            "industrialReadiness", "calculationBlockerRecovery", "workspaceStorage", "artifactStorage",
        }
    }
    bounded["advancedEngineering"]["workspaceStorage"] = workspace_meta
    return bounded


def _compact_project_for_workspace(project: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded project payload for opening the web workspace.

    The full engineering snapshot remains in ``projects.data`` and immutable
    revisions.  This projection deliberately excludes result matrices, raw VTU
    meshes, repeated candidate calculations and detailed manufacturing caches.
    """
    workspace = dict(project)
    results = list(project.get("calculationResults") or [])
    workspace["calculationResults"] = [_compact_result_for_workspace(results[-1])] if results else []
    workspace["messages"] = list(project.get("messages") or [])[-100:]
    workspace["monitoringRecords"] = list(project.get("monitoringRecords") or [])[-500:]
    workspace["calibrationRuns"] = list(project.get("calibrationRuns") or [])[-20:]
    workspace["drawingRevisions"] = list(project.get("drawingRevisions") or [])[-50:]

    geological = project.get("geologicalModel")
    if isinstance(geological, dict):
        compact_geo = dict(geological)
        compact_geo["vtuMesh"] = None
        surfaces = []
        for surface in list(geological.get("surfaces") or [])[:64]:
            if not isinstance(surface, dict):
                continue
            item = dict(surface)
            item["grid"] = _downsample_surface_grid(surface.get("grid"))
            surfaces.append(item)
        compact_geo["surfaces"] = surfaces
        compact_geo["volumes"] = list(geological.get("volumes") or [])[:128]
        workspace["geologicalModel"] = compact_geo

    retaining = project.get("retainingSystem")
    if isinstance(retaining, dict):
        compact_retaining = dict(retaining)
        layout_summary = dict(compact_retaining.get("layoutSummary") or {})
        layout_summary.pop("autoRepair", None)
        layout_summary.pop("supportOptimizationCandidates", None)
        compact_retaining["layoutSummary"] = layout_summary
        repair = retaining.get("supportLayoutRepair")
        if isinstance(repair, dict):
            compact_repair = dict(repair)
            compact_repair["candidates"] = [
                _compact_candidate_for_workspace(candidate)
                for candidate in list(repair.get("candidates") or [])[:12]
                if isinstance(candidate, dict)
            ]
            compact_repair["candidateFullCalculations"] = [
                _compact_candidate_calculation_summary(row)
                for row in list(repair.get("candidateFullCalculations") or repair.get("candidate_full_calculations") or [])[:12]
                if isinstance(row, dict)
            ]
            compact_retaining["supportLayoutRepair"] = compact_repair
        rebar_scheme = compact_retaining.get("rebarDesignScheme")
        if isinstance(rebar_scheme, dict):
            rebar_compact = dict(rebar_scheme)
            for key in ("bars", "barInstances", "fullGeometry", "manufacturingRows", "bbsRows"):
                if key in rebar_compact:
                    rebar_compact[key] = []
            compact_retaining["rebarDesignScheme"] = rebar_compact
        workspace["retainingSystem"] = compact_retaining

    advanced = dict(project.get("advancedEngineering") or {})
    omitted: list[str] = []
    for key in (
        "latestSuite", "industrialDetailing", "qualificationSuite",
        "detailGeometryPatches", "fullRebarGeometry", "manufacturingData",
        "renderCache", "ifcEntityCache", "calculationResultArchive",
    ):
        if key in advanced:
            advanced.pop(key, None)
            omitted.append(f"advancedEngineering.{key}")
    advanced["workspaceStorage"] = {
        "profile": "workspace",
        "fullCalculationResultCount": len(results),
        "omittedPaths": omitted,
    }
    workspace["advancedEngineering"] = advanced
    return workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_summary_payload(project: dict[str, Any], revision: int = 0) -> dict[str, Any]:
    results = list(project.get("calculationResults") or [])
    latest = results[-1] if results else {}
    return {
        "id": project.get("id"),
        "revision": revision,
        "name": project.get("name", "Untitled"),
        "location": project.get("location"),
        "createdAt": project.get("createdAt") or project.get("created_at"),
        "updatedAt": project.get("updatedAt") or project.get("updated_at"),
        "hasExcavation": bool(project.get("excavation")),
        "hasRetainingSystem": bool(project.get("retainingSystem")),
        "calculationCaseCount": len(project.get("calculationCases") or []),
        "calculationResultCount": len(results),
        "latestCalculationId": latest.get("id"),
        "governingStatus": (latest.get("governingValues") or {}).get("governingCheckStatus"),
        "geometryConsistent": ((latest.get("reportDiagramData") or {}).get("geometryConsistency") or {}).get("consistent"),
    }


class SQLiteProjectStore:
    """SQLite project store with WAL, immutable revisions and audit events.

    Schema migration is process-scoped. Earlier releases repeated JSON1
    backfill and PRAGMA/schema work for every HTTP request because each request
    constructed a repository. That was a major source of latency and lock
    contention on create/open/save operations.
    """

    _schema_lock = Lock()
    _initialized_paths: set[str] = set()

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("PITGUARD_DB_PATH", DEFAULT_DB_PATH)).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.db_path)
        if key not in self._initialized_paths:
            with self._schema_lock:
                if key not in self._initialized_paths:
                    self._ensure_schema()
                    self._initialized_paths.add(key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("PRAGMA cache_size=-32768")
        conn.execute("PRAGMA mmap_size=0")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA journal_size_limit=67108864")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '{}',
                    workspace_data TEXT NOT NULL DEFAULT '{}',
                    payload_bytes INTEGER NOT NULL DEFAULT 0,
                    workspace_bytes INTEGER NOT NULL DEFAULT 0,
                    external_bytes INTEGER NOT NULL DEFAULT 0,
                    artifact_count INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                )
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "revision" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            if "content_hash" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            if "summary" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN summary TEXT NOT NULL DEFAULT '{}'")
            if "workspace_data" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN workspace_data TEXT NOT NULL DEFAULT '{}'")
            if "payload_bytes" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN payload_bytes INTEGER NOT NULL DEFAULT 0")
            if "workspace_bytes" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN workspace_bytes INTEGER NOT NULL DEFAULT 0")
            if "external_bytes" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN external_bytes INTEGER NOT NULL DEFAULT 0")
            if "artifact_count" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN artifact_count INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_revisions (
                    project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    revision INTEGER,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_design_sessions (
                    project_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 1,
                    config TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_candidate_previews (
                    project_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_rank INTEGER,
                    plan_geometry TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, candidate_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_revisions_updated ON project_revisions(project_id, revision DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_project_created ON audit_events(project_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_design_session_updated ON project_design_sessions(updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_preview_rank ON project_candidate_previews(project_id, candidate_rank)")
            self._backfill_workspace_columns(conn)
            conn.commit()

    def _backfill_workspace_columns(self, conn: sqlite3.Connection) -> None:
        """Create safe workspace projections for databases produced by older releases.

        The transformation is executed by SQLite JSON1 so a legacy multi-hundred
        megabyte document is never copied into the API Python heap during startup.
        The latest full result is intentionally omitted for the initial backfill;
        the next normal save writes a compact current-result summary.
        """
        conn.execute(
            "UPDATE projects SET payload_bytes = length(CAST(data AS BLOB)) "
            "WHERE payload_bytes <= 0"
        )
        try:
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    json_remove(
                        data,
                        '$.calculationResults',
                        '$.geologicalModel.vtuMesh',
                        '$.geologicalModel.surfaces',
                        '$.geologicalModel.volumes',
                        '$.retainingSystem.supportLayoutRepair.candidateFullCalculations',
                        '$.retainingSystem.rebarDesignScheme.bars',
                        '$.retainingSystem.rebarDesignScheme.barInstances',
                        '$.retainingSystem.rebarDesignScheme.fullGeometry',
                        '$.advancedEngineering.latestSuite',
                        '$.advancedEngineering.industrialDetailing',
                        '$.advancedEngineering.qualificationSuite',
                        '$.advancedEngineering.detailGeometryPatches',
                        '$.advancedEngineering.fullRebarGeometry',
                        '$.advancedEngineering.manufacturingData',
                        '$.advancedEngineering.renderCache',
                        '$.advancedEngineering.ifcEntityCache',
                        '$.advancedEngineering.calculationResultArchive',
                        '$.monitoringRecords'
                    ),
                    '$.calculationResults', json('[]'),
                    '$.geologicalModel.surfaces', json('[]'),
                    '$.geologicalModel.volumes', json('[]'),
                    '$.monitoringRecords', json('[]'),
                    '$.advancedEngineering.workspaceStorage',
                    json_object('profile', 'workspace', 'legacyBackfill', 1)
                )
                WHERE (workspace_data IS NULL OR workspace_data = '' OR workspace_data = '{}')
                  AND json_valid(data)
                """
            )
            # Remove repeated full candidate calculations from each candidate.
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    workspace_data,
                    '$.retainingSystem.supportLayoutRepair.candidates',
                    COALESCE((
                        SELECT json_group_array(json(json_remove(value, '$.fullCalculation')))
                        FROM json_each(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates')
                    ), json('[]'))
                )
                WHERE json_type(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') = 'array'
                  AND json_array_length(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') > 0
                """
            )
        except sqlite3.OperationalError:
            # JSON1 is built into supported Python/SQLite releases.  Retaining a
            # minimal payload still keeps the service alive on unusual builds.
            conn.execute(
                "UPDATE projects SET workspace_data = json_object("
                "'id', id, 'name', name, 'updatedAt', updated_at, "
                "'advancedEngineering', json_object('workspaceStorage', json_object('profile','minimal'))) "
                "WHERE workspace_data IS NULL OR workspace_data = '' OR workspace_data = '{}'"
            )
        # V3.44 removes two historical copies of the same A/B/C candidate set.
        # Run in SQLite JSON1 so a 100+ MB legacy project is compacted without
        # hydrating it in the API process. The canonical copy remains under
        # retainingSystem.supportLayoutRepair.
        try:
            conn.execute(
                """
                UPDATE projects
                SET data = json_remove(
                        data,
                        '$.retainingSystem.layoutSummary.autoRepair',
                        '$.retainingSystem.layoutSummary.supportOptimizationCandidates'
                    ),
                    workspace_data = json_remove(
                        workspace_data,
                        '$.retainingSystem.layoutSummary.autoRepair',
                        '$.retainingSystem.layoutSummary.supportOptimizationCandidates'
                    )
                WHERE json_valid(data)
                  AND (
                    json_type(data, '$.retainingSystem.layoutSummary.autoRepair') IS NOT NULL
                    OR json_type(data, '$.retainingSystem.layoutSummary.supportOptimizationCandidates') IS NOT NULL
                    OR json_type(workspace_data, '$.retainingSystem.layoutSummary.autoRepair') IS NOT NULL
                    OR json_type(workspace_data, '$.retainingSystem.layoutSummary.supportOptimizationCandidates') IS NOT NULL
                  )
                """
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "UPDATE projects SET payload_bytes = length(CAST(data AS BLOB)), "
            "workspace_bytes = length(CAST(workspace_data AS BLOB))"
        )
        # If a legacy workspace is unusually large, remove candidate calculation
        # deltas while preserving the lightweight plan preview.  Engineering metrics and the complete snapshot
        # remain available in projects.data for the isolated worker.
        try:
            conn.execute(
                """
                UPDATE projects
                SET workspace_data = json_set(
                    workspace_data,
                    '$.retainingSystem.supportLayoutRepair.candidates',
                    COALESCE((
                        SELECT json_group_array(json(json_remove(
                            value, '$.fullCalculation', '$.deltaGeometry', '$.lineAdjustments'
                        )))
                        FROM json_each(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates')
                    ), json('[]')),
                    '$.advancedEngineering.workspaceStorage.aggressivelyCompacted', 1
                )
                WHERE workspace_bytes > ?
                  AND json_type(workspace_data, '$.retainingSystem.supportLayoutRepair.candidates') = 'array'
                """,
                (_workspace_limit_bytes(),),
            )
            conn.execute(
                "UPDATE projects SET workspace_bytes = length(CAST(workspace_data AS BLOB)) "
                "WHERE workspace_bytes > ?",
                (_workspace_limit_bytes(),),
            )
        except sqlite3.OperationalError:
            pass

    def upsert(
        self,
        project: dict[str, Any],
        *,
        expected_revision: int | None = None,
        actor: str = "system",
        action: str = "project.save",
        summary: str = "Project snapshot saved",
    ) -> int:
        # V3.31 stores large arrays as immutable content-addressed objects.
        # ``project`` is a fresh model_dump dictionary, so it is safe to compact
        # it in place without mutating the caller's Pydantic model.
        started = time.perf_counter()
        memory_event(
            "project-storage",
            "save-start",
            projectId=project.get("id"),
            action=action,
            calculationResultCount=len(project.get("calculationResults") or []),
            candidateCount=len((((project.get("retainingSystem") or {}).get("supportLayoutRepair") or {}).get("candidates") or [])),
        )
        artifact_store = ProjectArtifactStore()
        project = externalize_project_payload(project, artifact_store)
        refs = artifact_refs(project)
        external_bytes = sum(int(item.get("storedBytes") or 0) for item in refs)
        artifact_count = len(refs)
        encoded = _canonical_json(project)
        workspace_project = _compact_project_for_workspace(project)
        workspace_encoded = _canonical_json(workspace_project)
        payload_bytes = len(encoded.encode("utf-8"))
        workspace_bytes = len(workspace_encoded.encode("utf-8"))
        aggressively_compacted = False
        if workspace_bytes > _workspace_limit_bytes():
            workspace_project = _aggressively_compact_workspace(workspace_project)
            workspace_encoded = _canonical_json(workspace_project)
            workspace_bytes = len(workspace_encoded.encode("utf-8"))
            aggressively_compacted = True
        append_event(
            "project-storage",
            "save-serialized",
            projectId=project.get("id"),
            action=action,
            payloadMb=round(payload_bytes / 1048576.0, 3),
            workspaceMb=round(workspace_bytes / 1048576.0, 3),
            externalMb=round(external_bytes / 1048576.0, 3),
            artifactCount=artifact_count,
            aggressivelyCompacted=aggressively_compacted,
            serializeSeconds=round(time.perf_counter() - started, 3),
        )
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        updated_at = str(project.get("updatedAt") or project.get("updated_at") or _now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT revision, content_hash FROM projects WHERE id = ?", (project["id"],)).fetchone()
            current_revision = int(current["revision"] or 0) if current else 0
            if expected_revision is not None and expected_revision != current_revision:
                conn.rollback()
                raise RuntimeError(f"Project revision conflict: expected {expected_revision}, current {current_revision}")
            if current and str(current["content_hash"] or "") == content_hash:
                conn.rollback()
                return current_revision
            revision = current_revision + 1
            summary_encoded = json.dumps(_project_summary_payload(project, revision), ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO projects (id, name, updated_at, revision, content_hash, summary, workspace_data, payload_bytes, workspace_bytes, external_bytes, artifact_count, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    revision=excluded.revision,
                    content_hash=excluded.content_hash,
                    summary=excluded.summary,
                    workspace_data=excluded.workspace_data,
                    payload_bytes=excluded.payload_bytes,
                    workspace_bytes=excluded.workspace_bytes,
                    external_bytes=excluded.external_bytes,
                    artifact_count=excluded.artifact_count,
                    data=excluded.data
                """,
                (project["id"], project.get("name", "Untitled"), updated_at, revision, content_hash, summary_encoded, workspace_encoded, payload_bytes, workspace_bytes, external_bytes, artifact_count, encoded),
            )
            conn.execute("DELETE FROM project_candidate_previews WHERE project_id = ?", (project["id"],))
            repair = ((workspace_project.get("retainingSystem") or {}).get("supportLayoutRepair") or {})
            for index, candidate in enumerate(list(repair.get("candidates") or [])[:20]):
                if not isinstance(candidate, dict):
                    continue
                candidate_id = str(candidate.get("id") or f"candidate-{index + 1}")
                geometry = _compact_candidate_plan_geometry(candidate.get("planGeometry") or candidate.get("plan_geometry"))
                if not geometry.get("outline") or not (geometry.get("supports") or geometry.get("transferBeams")):
                    continue
                conn.execute(
                    """
                    INSERT INTO project_candidate_previews(project_id, candidate_id, candidate_rank, plan_geometry, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (project["id"], candidate_id, int(candidate.get("rank") or index + 1), _canonical_json(geometry), updated_at),
                )
            conn.execute(
                """
                INSERT INTO project_revisions(project_id, revision, updated_at, content_hash, actor, action, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project["id"], revision, updated_at, content_hash, actor, action, encoded),
            )
            conn.execute(
                """
                INSERT INTO audit_events(id, project_id, revision, actor, action, summary, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"audit-{uuid4().hex[:16]}", project["id"], revision, actor, action, summary, "{}", _now()),
            )
            retain = max(10, int(os.getenv("PITGUARD_REVISION_RETENTION", "30")))
            conn.execute(
                """
                DELETE FROM project_revisions
                WHERE project_id = ? AND revision NOT IN (
                    SELECT revision FROM project_revisions WHERE project_id = ? ORDER BY revision DESC LIMIT ?
                )
                """,
                (project["id"], project["id"], retain),
            )
            conn.commit()
            memory_event(
                "project-storage",
                "save-complete",
                projectId=project.get("id"),
                action=action,
                revision=revision,
                payloadMb=round(payload_bytes / 1048576.0, 3),
                workspaceMb=round(workspace_bytes / 1048576.0, 3),
                elapsedSeconds=round(time.perf_counter() - started, 3),
            )
            return revision

    def append_audit(
        self,
        project_id: str | None,
        *,
        action: str,
        summary: str,
        actor: str = "system",
        revision: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"audit-{uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events(id, project_id, revision, actor, action, summary, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, project_id, revision, actor, action, summary, json.dumps(metadata or {}, ensure_ascii=False), _now()),
            )
            conn.commit()
        return event_id

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY updated_at DESC").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def list_summaries(self) -> list[dict[str, Any]]:
        # Summaries are persisted separately so the project list never asks
        # SQLite JSON1 to parse multi-megabyte calculation payloads.
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, updated_at, revision, summary, payload_bytes, workspace_bytes, external_bytes, artifact_count FROM projects ORDER BY updated_at DESC").fetchall()
        output: list[dict[str, Any]] = []
        policy = adaptive_resource_policy(role=_process_role())
        for row in rows:
            try:
                item = json.loads(str(row["summary"] or "{}"))
            except json.JSONDecodeError:
                item = {}
            output.append({
                "id": item.get("id") or row["id"],
                "revision": int(item.get("revision") or row["revision"] or 0),
                "name": item.get("name") or row["name"],
                "location": item.get("location"),
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt") or row["updated_at"],
                "has_excavation": bool(item.get("hasExcavation")),
                "has_retaining_system": bool(item.get("hasRetainingSystem")),
                "calculation_case_count": int(item.get("calculationCaseCount") or 0),
                "calculation_result_count": int(item.get("calculationResultCount") or 0),
                "latest_calculation_id": item.get("latestCalculationId"),
                "governing_status": item.get("governingStatus"),
                "geometry_consistent": item.get("geometryConsistent"),
                "payload_bytes": int(row["payload_bytes"] or 0),
                "workspace_bytes": int(row["workspace_bytes"] or 0),
                "external_bytes": int(row["external_bytes"] or 0),
                "artifact_count": int(row["artifact_count"] or 0),
                "storage_status": classify_payload(int(row["payload_bytes"] or 0), policy=policy),
            })
        return output

    def get_payload_info(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, revision, updated_at, payload_bytes, workspace_bytes, external_bytes, artifact_count, "
                "length(CAST(data AS BLOB)) AS measured_payload_bytes, "
                "length(CAST(workspace_data AS BLOB)) AS measured_workspace_bytes "
                "FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        payload_bytes = int(row["payload_bytes"] or row["measured_payload_bytes"] or 0)
        workspace_bytes = int(row["workspace_bytes"] or row["measured_workspace_bytes"] or 0)
        policy = adaptive_resource_policy(role=_process_role())
        full_limit = int(policy["apiFullLoadLimitBytes"])
        workspace_limit = int(policy["workspaceLimitBytes"])
        external_bytes = int(row["external_bytes"] or 0)
        total_logical = payload_bytes + external_bytes
        full_allowed = _process_role() != "api" or payload_bytes <= full_limit
        compaction_recommended = (
            workspace_bytes > workspace_limit
            or (payload_bytes > full_limit and external_bytes <= 0)
            or (payload_bytes > 128 * 1024 * 1024 and workspace_bytes / max(payload_bytes, 1) > 0.18)
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "revision": int(row["revision"] or 0),
            "updatedAt": row["updated_at"],
            "payloadBytes": payload_bytes,
            "workspaceBytes": workspace_bytes,
            "externalBytes": external_bytes,
            "artifactCount": int(row["artifact_count"] or 0),
            "totalLogicalBytes": total_logical,
            "compressionRatio": round(workspace_bytes / max(total_logical, 1), 6),
            "apiFullLoadLimitBytes": full_limit,
            "workspaceLimitBytes": workspace_limit,
            "fullLoadAllowed": full_allowed,
            "workspaceLoadAllowed": workspace_bytes <= workspace_limit,
            "storageStatus": classify_payload(payload_bytes, policy=policy),
            "compactionRecommended": compaction_recommended,
            "processRole": _process_role(),
            "resourcePolicy": policy,
        }

    def _rebuild_workspace_projection_sql(self, conn: sqlite3.Connection, project_id: str) -> int:
        """Rebuild a bounded workspace entirely inside SQLite JSON1.

        This path is used when Python hydration of the complete snapshot would
        exceed the current worker budget. It retains the current excavation,
        retaining system and candidate plan previews while removing result
        matrices, detailed reinforcement, IDW surfaces and other heavy arrays.
        """
        query = """
            WITH candidate_bundle AS (
                SELECT COALESCE(json_group_array(json_object(
                    'id', json_extract(value, '$.id'),
                    'rank', json_extract(value, '$.rank'),
                    'score', json_extract(value, '$.score'),
                    'status', json_extract(value, '$.status'),
                    'targetSpacing', COALESCE(json_extract(value, '$.targetSpacing'), json_extract(value, '$.target_spacing')),
                    'columnMaxSpan', COALESCE(json_extract(value, '$.columnMaxSpan'), json_extract(value, '$.column_max_span')),
                    'hardConstraints', json(COALESCE(json_extract(value, '$.hardConstraints'), json_extract(value, '$.hard_constraints'), '{}')),
                    'variableSummary', json(COALESCE(json_extract(value, '$.variableSummary'), json_extract(value, '$.variable_summary'), '{}')),
                    'metrics', json(COALESCE(json_extract(value, '$.metrics'), '{}')),
                    'issueCount', COALESCE(json_extract(value, '$.issueCount'), json_extract(value, '$.issue_count')),
                    'failCount', COALESCE(json_extract(value, '$.failCount'), json_extract(value, '$.fail_count')),
                    'warningCount', COALESCE(json_extract(value, '$.warningCount'), json_extract(value, '$.warning_count')),
                    'supportCount', COALESCE(json_extract(value, '$.supportCount'), json_extract(value, '$.support_count')),
                    'columnCount', COALESCE(json_extract(value, '$.columnCount'), json_extract(value, '$.column_count')),
                    'maxSpanLength', COALESCE(json_extract(value, '$.maxSpanLength'), json_extract(value, '$.max_span_length')),
                    'maxBaySpacing', COALESCE(json_extract(value, '$.maxBaySpacing'), json_extract(value, '$.max_bay_spacing')),
                    'crossingCount', COALESCE(json_extract(value, '$.crossingCount'), json_extract(value, '$.crossing_count')),
                    'junctionCount', COALESCE(json_extract(value, '$.junctionCount'), json_extract(value, '$.junction_count')),
                    'wallJunctionCount', COALESCE(json_extract(value, '$.wallJunctionCount'), json_extract(value, '$.wall_junction_count')),
                    'constructabilityNote', COALESCE(json_extract(value, '$.constructabilityNote'), json_extract(value, '$.constructability_note')),
                    'planGeometry', json(COALESCE(json_extract(value, '$.planGeometry'), json_extract(value, '$.plan_geometry'), '{}')),
                    'fullCalculation', json('{}'),
                    'deltaGeometry', json('{}'),
                    'lineAdjustments', json('[]')
                )), json('[]')) AS candidates_json
                FROM projects, json_each(projects.data, '$.retainingSystem.supportLayoutRepair.candidates')
                WHERE projects.id = ?
            ), base AS (
                SELECT CASE
                    WHEN json_valid(workspace_data) AND length(workspace_data) > 2 THEN workspace_data
                    ELSE data
                END AS document
                FROM projects WHERE id = ?
            )
            SELECT json_set(
                json_remove(
                    base.document,
                    '$.calculationResults',
                    '$.geologicalModel.vtuMesh',
                    '$.geologicalModel.surfaces',
                    '$.geologicalModel.volumes',
                    '$.retainingSystem.supportLayoutRepair.candidateFullCalculations',
                    '$.retainingSystem.rebarDesignScheme.bars',
                    '$.retainingSystem.rebarDesignScheme.barInstances',
                    '$.retainingSystem.rebarDesignScheme.fullGeometry',
                    '$.retainingSystem.rebarDesignScheme.manufacturingRows',
                    '$.retainingSystem.rebarDesignScheme.bbsRows',
                    '$.advancedEngineering.latestSuite',
                    '$.advancedEngineering.industrialDetailing',
                    '$.advancedEngineering.qualificationSuite',
                    '$.advancedEngineering.detailGeometryPatches',
                    '$.advancedEngineering.fullRebarGeometry',
                    '$.advancedEngineering.manufacturingData',
                    '$.advancedEngineering.renderCache',
                    '$.advancedEngineering.ifcEntityCache',
                    '$.advancedEngineering.calculationResultArchive',
                    '$.monitoringRecords'
                ),
                '$.calculationResults', json('[]'),
                '$.geologicalModel.surfaces', json('[]'),
                '$.geologicalModel.volumes', json('[]'),
                '$.monitoringRecords', json('[]'),
                '$.retainingSystem.supportLayoutRepair.candidates', json(candidate_bundle.candidates_json),
                '$.retainingSystem.supportLayoutRepair.candidateFullCalculations', json('[]'),
                '$.advancedEngineering.workspaceStorage', json_object(
                    'profile', 'workspace',
                    'projectionMode', 'sqlite_json1_low_memory',
                    'fullSnapshotPreserved', 1,
                    'candidatePreviewCount', json_array_length(candidate_bundle.candidates_json),
                    'updatedAt', ?
                )
            ) AS workspace
            FROM base, candidate_bundle
        """
        row = conn.execute(query, (project_id, project_id, _now())).fetchone()
        workspace_encoded = str(row["workspace"] or "{}") if row else "{}"
        conn.execute(
            "UPDATE projects SET workspace_data=?, workspace_bytes=? WHERE id=?",
            (workspace_encoded, len(workspace_encoded.encode("utf-8")), project_id),
        )
        return len(workspace_encoded.encode("utf-8"))

    def compact_project_storage(self, project_id: str, *, include_revisions: bool = False) -> dict[str, Any]:
        """Externalize heavy data when safe, otherwise rebuild only the workspace.

        Full JSON hydration can require five or more copies of the serialized
        document. Before parsing, the worker compares that estimate with current
        cgroup/host headroom. Low-headroom runs preserve the immutable full
        snapshot and rebuild the interactive projection through SQLite JSON1,
        preventing an OOM while keeping the web workflow usable.
        """
        artifact_store = ProjectArtifactStore()
        policy = adaptive_resource_policy(role="worker")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, payload_bytes, workspace_bytes, external_bytes, artifact_count FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Project not found: {project_id}")
        before = {
            "payloadBytes": int(row["payload_bytes"] or 0),
            "workspaceBytes": int(row["workspace_bytes"] or 0),
            "externalBytes": int(row["external_bytes"] or 0),
            "artifactCount": int(row["artifact_count"] or 0),
        }
        amplification = max(3.0, float(policy.get("apiJsonAmplification") or 5.5))
        estimated_hydration = int(before["payloadBytes"] * amplification)
        rss = int(policy.get("processRssBytes") or 0)
        soft_remaining = max(0, int(policy.get("workerSoftLimitBytes") or 0) - rss)
        runtime_remaining = max(0, int(policy.get("usableHeadroomBytes") or 0))
        safe_budget = min(value for value in (soft_remaining, runtime_remaining) if value > 0) if (soft_remaining > 0 and runtime_remaining > 0) else max(soft_remaining, runtime_remaining)
        full_hydration_safe = bool(policy.get("workerFullHydrationAllowed")) and estimated_hydration <= int(max(1, safe_budget) * 0.78)
        estimated_artifact_disk = max(256 * 1024**2, int(before["payloadBytes"] * 0.65))
        disk_usable = int(policy.get("diskUsableBytes") or 0)
        disk_policy_known = "diskUsableBytes" in policy or "storageCompactionAllowed" in policy
        disk_safe = (not disk_policy_known) or (
            bool(policy.get("storageCompactionAllowed")) and disk_usable >= estimated_artifact_disk
        )

        if not full_hydration_safe or not disk_safe:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                workspace_bytes = self._rebuild_workspace_projection_sql(conn, project_id)
                conn.commit()
            after = {**before, "workspaceBytes": workspace_bytes}
            return {
                "projectId": project_id,
                "mode": "workspace_projection_only",
                "before": before,
                "after": after,
                "payloadReductionBytes": 0,
                "workspaceReductionBytes": max(0, before["workspaceBytes"] - workspace_bytes),
                "revisionCountCompacted": 0,
                "revisionCompactionDeferred": bool(include_revisions),
                "fullSnapshotExternalizationDeferred": True,
                "deferredReason": "memory_headroom" if not full_hydration_safe else "disk_headroom",
                "estimatedHydrationBytes": estimated_hydration,
                "safeHydrationBudgetBytes": safe_budget,
                "estimatedArtifactDiskBytes": estimated_artifact_disk,
                "diskUsableBytes": disk_usable,
                "resourcePolicy": policy,
                "fullLoadAllowed": _process_role() != "api" or before["payloadBytes"] <= _api_full_load_limit_bytes(),
                "message": (
                    "当前空余内存不足以安全展开完整快照；已用SQLite重建轻量工作区，完整外部化等待更高内存余量的worker执行。"
                    if not full_hydration_safe else
                    "当前磁盘安全余量不足以写入外部化对象；已重建轻量工作区，完整外部化等待释放磁盘空间后执行。"
                ),
            }

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            full_row = conn.execute(
                "SELECT data FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if full_row is None:
                conn.rollback()
                raise KeyError(f"Project not found: {project_id}")
            project = json.loads(str(full_row["data"]))
            compact = externalize_project_payload(project, artifact_store)
            encoded = _canonical_json(compact)
            workspace = _compact_project_for_workspace(compact)
            workspace_encoded = _canonical_json(workspace)
            if len(workspace_encoded.encode("utf-8")) > _workspace_limit_bytes():
                workspace = _aggressively_compact_workspace(workspace)
                workspace_encoded = _canonical_json(workspace)
            refs = artifact_refs(compact)
            payload_bytes = len(encoded.encode("utf-8"))
            workspace_bytes = len(workspace_encoded.encode("utf-8"))
            external_bytes = sum(int(item.get("storedBytes") or 0) for item in refs)
            artifact_count = len(refs)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            conn.execute(
                """
                UPDATE projects
                SET data=?, content_hash=?, workspace_data=?, payload_bytes=?, workspace_bytes=?, external_bytes=?, artifact_count=?
                WHERE id=?
                """,
                (encoded, digest, workspace_encoded, payload_bytes, workspace_bytes, external_bytes, artifact_count, project_id),
            )
            conn.execute("DELETE FROM project_candidate_previews WHERE project_id = ?", (project_id,))
            repair = ((workspace.get("retainingSystem") or {}).get("supportLayoutRepair") or {})
            for index, candidate in enumerate(list(repair.get("candidates") or [])[:20]):
                if not isinstance(candidate, dict):
                    continue
                geometry = _compact_candidate_plan_geometry(candidate.get("planGeometry") or candidate.get("plan_geometry"))
                if not geometry.get("outline") or not (geometry.get("supports") or geometry.get("transferBeams")):
                    continue
                conn.execute(
                    """
                    INSERT INTO project_candidate_previews(project_id, candidate_id, candidate_rank, plan_geometry, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        str(candidate.get("id") or f"candidate-{index + 1}"),
                        int(candidate.get("rank") or index + 1),
                        _canonical_json(geometry),
                        _now(),
                    ),
                )
            revision_count = 0
            if include_revisions:
                revision_rows = conn.execute(
                    "SELECT revision, data FROM project_revisions WHERE project_id = ? ORDER BY revision",
                    (project_id,),
                ).fetchall()
                for revision_row in revision_rows:
                    revision_project = externalize_project_payload(json.loads(str(revision_row["data"])), artifact_store)
                    revision_encoded = _canonical_json(revision_project)
                    conn.execute(
                        "UPDATE project_revisions SET data=?, content_hash=? WHERE project_id=? AND revision=?",
                        (
                            revision_encoded,
                            hashlib.sha256(revision_encoded.encode("utf-8")).hexdigest(),
                            project_id,
                            int(revision_row["revision"]),
                        ),
                    )
                    revision_count += 1
            conn.commit()
        after = {
            "payloadBytes": payload_bytes,
            "workspaceBytes": workspace_bytes,
            "externalBytes": external_bytes,
            "artifactCount": artifact_count,
        }
        return {
            "projectId": project_id,
            "mode": "full_externalization",
            "before": before,
            "after": after,
            "payloadReductionBytes": max(0, before["payloadBytes"] - payload_bytes),
            "workspaceReductionBytes": max(0, before["workspaceBytes"] - workspace_bytes),
            "revisionCountCompacted": revision_count,
            "fullSnapshotExternalizationDeferred": False,
            "estimatedHydrationBytes": estimated_hydration,
            "safeHydrationBudgetBytes": safe_budget,
            "resourcePolicy": policy,
            "fullLoadAllowed": _process_role() != "api" or payload_bytes <= _api_full_load_limit_bytes(),
        }

    def get_progressive_design_config(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version, config, updated_at FROM project_design_sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return {}
        try:
            config = json.loads(str(row["config"] or "{}"))
        except json.JSONDecodeError:
            config = {}
        # The database version is authoritative. Older rows could persist a
        # stale sessionVersion inside the JSON body, which caused every later
        # optimistic update to conflict even after a successful save.
        config["sessionVersion"] = int(row["version"] or 1)
        config["updatedAt"] = row["updated_at"]
        return config

    def save_progressive_design_config(
        self,
        project_id: str,
        config: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version FROM project_design_sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            current = int(row["version"] or 0) if row else 0
            if expected_version is not None and current != int(expected_version):
                conn.rollback()
                raise RuntimeError(
                    f"Progressive design session revision conflict: expected {expected_version}, current {current}"
                )
            version = current + 1
            stored_config = dict(config or {})
            stored_config["sessionVersion"] = version
            stored_config["updatedAt"] = now
            encoded = _canonical_json(stored_config)
            conn.execute(
                """
                INSERT INTO project_design_sessions(project_id, version, config, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    version=excluded.version, config=excluded.config, updated_at=excluded.updated_at
                """,
                (project_id, version, encoded, now),
            )
            conn.commit()
        return stored_config

    def get_candidate_preview_bundle(self, project_id: str, *, limit: int = 12) -> dict[str, Any]:
        """Read candidate previews without hydrating the complete project.

        New saves populate a dedicated preview cache. Legacy projects are read
        once through SQLite JSON1 and the bounded geometry is cached, avoiding
        repeated parsing of a multi-hundred-MB snapshot on every card click.
        """
        bounded_limit = max(1, min(int(limit), 20))
        with self._connect() as conn:
            cached = conn.execute(
                """
                SELECT candidate_id, candidate_rank, plan_geometry
                FROM project_candidate_previews
                WHERE project_id = ?
                ORDER BY COALESCE(candidate_rank, 999), candidate_id
                LIMIT ?
                """,
                (project_id, bounded_limit),
            ).fetchall()
        if cached:
            previews = []
            cache_current = True
            for row in cached:
                try:
                    geometry = json.loads(str(row["plan_geometry"] or "{}"))
                except json.JSONDecodeError:
                    geometry = {}
                if geometry.get("previewSchema") != CANDIDATE_PREVIEW_SCHEMA:
                    cache_current = False
                    break
                previews.append({
                    "candidateId": row["candidate_id"],
                    "rank": row["candidate_rank"],
                    "planGeometry": _compact_candidate_plan_geometry(geometry),
                })
            if cache_current:
                return {"projectId": project_id, "source": "preview_cache", "previews": previews}
            # Older preview schemas may omit transfer frames, integrity metadata
            # or current result evidence. Delete them and rebuild once.
            # once from the authoritative project snapshot.
            with self._connect() as conn:
                conn.execute("DELETE FROM project_candidate_previews WHERE project_id = ?", (project_id,))
                conn.commit()

        rows: list[sqlite3.Row] = []
        source = "full_snapshot_json1"
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        json_extract(value, '$.id') AS candidate_id,
                        json_extract(value, '$.rank') AS candidate_rank,
                        json_extract(value, '$.planGeometry') AS plan_geometry,
                        json_extract(value, '$.plan_geometry') AS plan_geometry_snake
                    FROM projects, json_each(projects.data, '$.retainingSystem.supportLayoutRepair.candidates')
                    WHERE projects.id = ?
                    ORDER BY CAST(COALESCE(json_extract(value, '$.rank'), 999) AS INTEGER)
                    LIMIT ?
                    """,
                    (project_id, bounded_limit),
                ).fetchall()
        except sqlite3.OperationalError:
            source = "workspace_fallback"
            workspace = self.get_workspace(project_id) or {}
            candidates = (((workspace.get("retainingSystem") or {}).get("supportLayoutRepair") or {}).get("candidates") or [])
            return {
                "projectId": project_id,
                "source": source,
                "previews": [
                    {
                        "candidateId": item.get("id"),
                        "rank": item.get("rank"),
                        "planGeometry": _compact_candidate_plan_geometry(item.get("planGeometry") or item.get("plan_geometry")),
                    }
                    for item in candidates[:bounded_limit]
                    if isinstance(item, dict)
                ],
            }
        previews = []
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for index, row in enumerate(rows):
                raw = row["plan_geometry"] or row["plan_geometry_snake"] or "{}"
                try:
                    geometry = json.loads(str(raw)) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    geometry = {}
                compact = _compact_candidate_plan_geometry(geometry)
                candidate_id = str(row["candidate_id"] or f"candidate-{index + 1}")
                rank = int(row["candidate_rank"] or index + 1)
                previews.append({"candidateId": candidate_id, "rank": rank, "planGeometry": compact})
                if compact.get("outline") and (compact.get("supports") or compact.get("transferBeams")):
                    conn.execute(
                        """
                        INSERT INTO project_candidate_previews(project_id, candidate_id, candidate_rank, plan_geometry, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(project_id, candidate_id) DO UPDATE SET
                            candidate_rank=excluded.candidate_rank,
                            plan_geometry=excluded.plan_geometry,
                            updated_at=excluded.updated_at
                        """,
                        (project_id, candidate_id, rank, _canonical_json(compact), now),
                    )
            conn.commit()
        return {"projectId": project_id, "source": source, "previews": previews}

    def get_workspace_metadata(self, project_id: str) -> dict[str, Any] | None:
        """Return workspace identity without copying the JSON payload into Python.

        Read-only design endpoints call this method before hydrating the workspace.
        It allows the repository-level model cache to reuse one validated Project
        across concurrent panels while still invalidating immediately on revision
        changes.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision, updated_at, payload_bytes, workspace_bytes FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "revision": int(row["revision"] or 0),
            "updatedAt": row["updated_at"],
            "payloadBytes": int(row["payload_bytes"] or 0),
            "workspaceBytes": int(row["workspace_bytes"] or 0),
        }

    def get_workspace_json(self, project_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_data, payload_bytes, workspace_bytes, revision, updated_at "
                "FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        payload = str(row["workspace_data"] or "{}")
        metadata = {
            "revision": int(row["revision"] or 0),
            "updatedAt": row["updated_at"],
            "payloadBytes": int(row["payload_bytes"] or 0),
            "workspaceBytes": int(row["workspace_bytes"] or len(payload.encode("utf-8"))),
        }
        return payload, metadata

    def get_workspace(self, project_id: str) -> dict[str, Any] | None:
        result = self.get_workspace_json(project_id)
        return json.loads(result[0]) if result else None

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            meta = conn.execute(
                "SELECT payload_bytes, length(CAST(data AS BLOB)) AS measured FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if meta is None:
                return None
            payload_bytes = int(meta["payload_bytes"] or meta["measured"] or 0)
            if _process_role() == "api" and payload_bytes > _api_full_load_limit_bytes():
                raise ProjectPayloadTooLarge(project_id, payload_bytes, _api_full_load_limit_bytes())
            row = conn.execute("SELECT data FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        project = json.loads(row["data"])
        if _process_role() == "worker" or str(os.getenv("PITGUARD_REHYDRATE_FULL_PROJECT", "0")).strip().lower() in {"1", "true", "yes"}:
            project = rehydrate_project_payload(project, ProjectArtifactStore())
        return project

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        """List external objects without hydrating the complete snapshot.

        The V3.38 implementation parsed ``projects.data`` for every manifest
        request.  On a 500 MB project that single UI panel could occupy the API
        for tens of seconds.  The workspace projection normally retains the
        bounded artifact index; legacy projects fall back to a direct directory
        scan.
        """
        result = self.get_workspace_json(project_id)
        if result is None:
            return []
        refs: list[dict[str, Any]] = []
        try:
            workspace = json.loads(result[0])
            refs = artifact_refs(workspace)
        except (json.JSONDecodeError, TypeError, ValueError):
            refs = []
        store = ProjectArtifactStore()
        if refs:
            return store.list_existing(refs)
        return store.scan_project(project_id)

    def get_artifact(self, project_id: str, artifact_id: str) -> dict[str, Any] | None:
        for ref in self.list_artifacts(project_id):
            if str(ref.get("artifactId")) == artifact_id:
                return ref
        return None

    def get_revision_number(self, project_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT revision FROM projects WHERE id = ?", (project_id,)).fetchone()
        return int(row["revision"]) if row else None

    def list_revisions(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, revision, updated_at, content_hash, actor, action FROM project_revisions WHERE project_id = ? ORDER BY revision DESC LIMIT ?",
                (project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_revision(self, project_id: str, revision: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM project_revisions WHERE project_id = ? AND revision = ?", (project_id, revision)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_audit_events(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(str(item.get("metadata") or "{}"))
            result.append(item)
        return result

    def delete(self, project_id: str, *, actor: str = "system") -> bool:
        revision = self.get_revision_number(project_id)
        self.append_audit(project_id, action="project.delete", summary="Project deleted", revision=revision, actor=actor)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.execute("DELETE FROM project_revisions WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_design_sessions WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_candidate_previews WHERE project_id = ?", (project_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            ProjectArtifactStore().delete_project(project_id)
        return deleted


    def backup(self, destination_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Create an online-consistent SQLite backup and verify its integrity."""
        backup_dir = Path(destination_dir or os.getenv("PITGUARD_BACKUP_DIR", self.db_path.parent / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"pitguard_{stamp}_{uuid4().hex[:8]}.sqlite3"
        with self._connect() as source, sqlite3.connect(destination, timeout=30.0) as target:
            source.execute("PRAGMA wal_checkpoint(PASSIVE)")
            source.backup(target)
            target.commit()
        with sqlite3.connect(destination, timeout=10.0) as check_conn:
            integrity = str(check_conn.execute("PRAGMA integrity_check").fetchone()[0])
            project_count = int(check_conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
            revision_count = int(check_conn.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0])
            audit_count = int(check_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        retention = max(1, int(os.getenv("PITGUARD_BACKUP_RETENTION", "20")))
        backups = sorted(backup_dir.glob("pitguard_*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in backups[retention:]:
            stale.unlink(missing_ok=True)
        return {
            "status": "pass" if integrity.lower() == "ok" else "fail",
            "path": str(destination),
            "filename": destination.name,
            "sizeBytes": destination.stat().st_size,
            "sha256": digest,
            "integrityCheck": integrity,
            "projectCount": project_count,
            "revisionCount": revision_count,
            "auditEventCount": audit_count,
            "retention": retention,
            "createdAt": _now(),
        }

    def list_backups(self, destination_dir: str | os.PathLike[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        backup_dir = Path(destination_dir or os.getenv("PITGUARD_BACKUP_DIR", self.db_path.parent / "backups"))
        if not backup_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(backup_dir.glob("pitguard_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)[:max(1, min(limit, 100))]:
            stat = path.stat()
            rows.append({
                "filename": path.name,
                "path": str(path),
                "sizeBytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return rows

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM project_revisions")
            conn.commit()
