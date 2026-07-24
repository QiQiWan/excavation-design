from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import Project
from app.services.review_workflow import review_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def screen_hazardous_work(project: Project) -> dict[str, Any]:
    explicit = str(project.design_settings.hazardous_work_classification or "unclassified")
    depth = float(project.excavation.depth if project.excavation else 0.0)
    environment = str(project.design_settings.surrounding_environment_level or "一般")
    complexity = str(project.design_settings.site_complexity or "中等")
    if explicit != "unclassified":
        classification = explicit
        source = "project_confirmed"
    elif depth >= 10.0 or environment == "高" or complexity == "复杂":
        classification = "suspected_large_scale_hazardous"
        source = "software_screening"
    elif depth >= 5.0 or environment == "较高":
        classification = "suspected_hazardous"
        source = "software_screening"
    else:
        classification = "screening_not_triggered"
        source = "software_screening"
    return {
        "classification": classification,
        "source": source,
        "explicitProjectClassification": explicit,
        "excavationDepthM": depth,
        "surroundingEnvironmentLevel": environment,
        "siteComplexity": complexity,
        "basis": project.design_settings.hazardous_work_basis,
        "requiresProjectConfirmation": source == "software_screening",
        "boundary": "软件筛查不能替代项目所在地危大工程清单、地方补充范围和法定责任主体确认。",
    }


def _evidence_store(project: Project) -> dict[str, Any]:
    advanced = dict(project.advanced_engineering or {})
    store = dict(advanced.get("statutoryWorkflowEvidence") or {})
    advanced["statutoryWorkflowEvidence"] = store
    project.advanced_engineering = advanced
    return store



def _artifact_current(project: Project, artifact_id: str, sha256: str) -> bool:
    storage = dict((project.advanced_engineering or {}).get("artifactStorage") or {})
    for row in storage.get("artifacts") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("artifactId") or "") == str(artifact_id) and str(row.get("sha256") or "").lower() == str(sha256).lower():
            return True
    return False

def record_statutory_evidence(
    project: Project,
    *,
    evidence_type: str,
    artifact_id: str,
    artifact_sha256: str,
    verifier: str,
    status: str = "verified",
    note: str | None = None,
) -> dict[str, Any]:
    if status not in {"verified", "provisional", "rejected"}:
        raise ValueError("status must be verified, provisional or rejected")
    sha = str(artifact_sha256 or "").strip().lower()
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("artifactSha256 must be a 64-character hexadecimal SHA-256")
    store = _evidence_store(project)
    artifact_current = _artifact_current(project, artifact_id, sha)
    effective_status = status if artifact_current else "provisional"
    row = {
        "evidenceType": str(evidence_type),
        "artifactId": str(artifact_id),
        "artifactSha256": sha,
        "verifier": str(verifier).strip(),
        "status": effective_status,
        "requestedStatus": status,
        "artifactCurrent": artifact_current,
        "note": note if artifact_current else ((note + "；" if note else "") + "不可变制品库中未找到匹配 artifactId 和 SHA-256，暂按 provisional 记录。"),
        "recordedAt": _now(),
    }
    row["recordHash"] = _hash_payload(row)
    store[str(evidence_type)] = row
    return row


def _monitoring_freshness(project: Project) -> dict[str, Any]:
    if not project.monitoring_records:
        return {"status": "missing", "recordCount": 0, "latestTimestamp": None, "ageHours": None}
    parsed = []
    for record in project.monitoring_records:
        if record.quality == "rejected":
            continue
        try:
            value = datetime.fromisoformat(str(record.timestamp).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            parsed.append(value.astimezone(timezone.utc))
        except ValueError:
            continue
    if not parsed:
        return {"status": "invalid", "recordCount": len(project.monitoring_records), "latestTimestamp": None, "ageHours": None}
    latest = max(parsed)
    age = max(0.0, (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0)
    limit = float(project.design_settings.monitoring_feedback_max_age_hours)
    return {
        "status": "pass" if age <= limit else "stale",
        "recordCount": len(project.monitoring_records),
        "latestTimestamp": latest.isoformat(),
        "ageHours": round(age, 2),
        "maximumAgeHours": limit,
    }


def evaluate_statutory_workflow(project: Project) -> dict[str, Any]:
    """Evaluate evidence by responsibility phase.

    V3.81 removes contractor/field evidence from the design-issue gate while
    preserving it for construction preparation and stage release.
    """
    from app.services.workflow_v381 import (
        evaluate_construction_preparation_gate,
        evaluate_design_issue_gate,
        evaluate_field_release_gate,
    )

    hazard = screen_hazardous_work(project)
    evidence = dict((project.advanced_engineering or {}).get("statutoryWorkflowEvidence") or {})
    classification = str(hazard["classification"])
    hazardous = classification in {"hazardous", "large_scale_hazardous", "suspected_hazardous", "suspected_large_scale_hazardous"}
    large = classification in {"large_scale_hazardous", "suspected_large_scale_hazardous"}

    requirement_catalog = [
        ("design_source_data", "真实、准确、完整的地质、水文地质和周边环境资料", "design", True, "建设单位/勘察单位"),
        ("hazardous_work_register", "危大工程清单和安全管理措施", "construction", hazardous, "建设单位/施工单位"),
        ("special_construction_plan", "专项施工方案及施工单位技术负责人审核", "construction", hazardous, "施工单位"),
        ("supervision_review", "总监理工程师审查和执业印章证据", "construction", hazardous, "监理单位"),
        ("expert_review_report", "专家论证报告及修改闭环", "construction", large and project.design_settings.require_expert_review_for_large_hazard, "施工单位组织/专家组"),
        ("monitoring_plan", "第三方监测方案和资质证据", "construction", hazardous, "建设单位委托监测单位"),
        ("technical_briefing", "方案交底和安全技术交底记录", "field", hazardous, "施工单位"),
        ("emergency_plan", "险情应急处置和恢复预案", "construction", hazardous, "施工单位"),
        ("stage_acceptance", "危大工程阶段验收和责任人签字", "field", hazardous, "施工单位/监理单位"),
        ("archive_manifest", "危大工程安全管理档案清单", "field", hazardous, "施工单位/建设单位"),
    ]
    rows: list[dict[str, Any]] = []
    missing_by_phase: dict[str, list[str]] = {"design": [], "construction": [], "field": []}
    for code, label, phase, required, responsibility in requirement_catalog:
        item = evidence.get(code)
        verified = bool(item and item.get("status") == "verified" and item.get("artifactCurrent"))
        status = "pass" if verified else "fail" if required else "not_applicable"
        if required and not verified:
            missing_by_phase[phase].append(code)
        rows.append({
            "code": code, "label": label, "phase": phase, "responsibility": responsibility,
            "required": required, "status": status, "evidence": item,
            "affectsDesignIssue": phase == "design",
            "affectsConstructionPreparation": phase in {"design", "construction"},
            "affectsFieldRelease": phase in {"design", "construction", "field"},
        })

    design_gate = evaluate_design_issue_gate(project)
    construction_gate = evaluate_construction_preparation_gate(project)
    field_gate = evaluate_field_release_gate(project)
    review = review_status(project)
    monitoring = _monitoring_freshness(project)

    design_missing = list(missing_by_phase["design"])
    if hazard.get("requiresProjectConfirmation"):
        design_missing.append("hazardous_work_classification_confirmation")
    design_eligible = bool(design_gate.get("eligible")) and not design_missing
    construction_eligible = bool(construction_gate.get("eligible")) and not missing_by_phase["construction"]
    field_eligible = bool(field_gate.get("eligible")) and not missing_by_phase["field"]

    status = "fail" if not design_eligible else "warning" if not construction_eligible or not field_eligible else "pass"
    result = {
        "schema": "pitguard-statutory-workflow-assurance-v2",
        "status": status,
        "hazardScreening": hazard,
        "requirements": rows,
        "missingRequiredEvidence": sorted(set(design_missing + missing_by_phase["construction"] + missing_by_phase["field"])),
        "missingByPhase": {key: sorted(set(value)) for key, value in missing_by_phase.items()},
        "monitoringFreshness": monitoring,
        "reviewStatus": review,
        "designIssueGate": design_gate,
        "constructionPreparationGate": construction_gate,
        "fieldStageReleaseGate": field_gate,
        "designIssueEligible": design_eligible,
        "constructionPreparationEligible": construction_eligible,
        "fieldStageReleaseEligible": field_eligible,
        # Backward-compatible alias used by the formal report gate.
        "formalIssueEligible": design_eligible,
        "responsibilityBoundary": {
            "design": "设计发行不依赖专项施工方案、专家论证、现场验收或实测数据。",
            "construction": "施工准备由施工、监理、专家和监测责任主体补充证据。",
            "field": "现场状态只控制施工阶段放行和偏差闭环。",
        },
        "legalBoundary": "该台账用于流程完整性和证据追溯，不替代各法定责任主体依法履责。",
    }
    project.advanced_engineering["statutoryWorkflowAssurance"] = result
    return result

