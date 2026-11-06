from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import Project
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.review_workflow import project_snapshot_hash, review_status
from app.version import ALGORITHM_VERSION, EXPORT_SCHEMA_VERSION, RULE_SET_VERSION, SOFTWARE_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(code: str, title: str, passed: bool, evidence: Any, action: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "status": "pass" if passed else ("fail" if required else "warning"),
        "required": required,
        "evidence": evidence,
        "requiredAction": "" if passed else action,
    }


def evaluate_delivery_release_readiness(project: Project, *, issue_mode: str = "review") -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    review = review_status(project)
    contract = verify_current_calculation_contract(project, latest)
    assurance = dict(getattr(latest, "calculation_assurance", {}) or {}) if latest else {}
    formal = getattr(latest, "formal_report_gate", None) if latest else None
    snapshot = project_snapshot_hash(project)
    current_revision = next(
        (
            row for row in reversed(project.drawing_revisions)
            if row.snapshot_hash == snapshot and row.issue_status == "construction"
        ),
        None,
    )
    construction = issue_mode == "construction"
    checks = [
        _row("REL-CALCULATION", "当前快照存在计算结果", bool(latest), getattr(latest, "id", None), "运行当前快照完整计算。"),
        _row("REL-CONTRACT", "计算合同与当前输入一致", bool(contract.get("current")), contract, "按当前输入、工况、拓扑和规则集重新计算。"),
        _row("REL-ASSURANCE", "工业计算质量包通过", assurance.get("status") == "pass", assurance, "关闭输入、阶段覆盖、数值和独立复核问题。", required=construction),
        _row("REL-RESULT-HASH", "计算基线具备输入与结果哈希", bool(latest and latest.input_snapshot_hash and latest.result_hash and latest.calculation_contract_id), {
            "inputSnapshotHash": getattr(latest, "input_snapshot_hash", None),
            "resultHash": getattr(latest, "result_hash", None),
            "contractId": getattr(latest, "calculation_contract_id", None),
        }, "重新运行 V3.24 完整计算。"),
        _row("REL-FORMAL-GATE", "正式成果闸门通过", bool(formal and formal.allowed_for_official_issue), formal.model_dump(mode="json", by_alias=True) if formal else None, "关闭正式成果阻断、警告和缺项。", required=construction),
        _row("REL-APPROVAL", "当前快照完成岗位分离审签", bool(review.get("approvalValid")), review, "完成设计、校核、审核、批准四级审签。", required=construction),
        _row("REL-REVISION", "存在当前快照施工版修订", bool(current_revision), current_revision.model_dump(mode="json", by_alias=True) if current_revision else None, "创建与当前快照一致的施工版修订。", required=construction),
    ]
    fail_count = sum(row["status"] == "fail" for row in checks)
    warning_count = sum(row["status"] == "warning" for row in checks)
    return {
        "issueMode": issue_mode,
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "allowed": fail_count == 0,
        "snapshotHash": snapshot,
        "calculationResultId": getattr(latest, "id", None),
        "calculationContractId": getattr(latest, "calculation_contract_id", None),
        "inputSnapshotHash": getattr(latest, "input_snapshot_hash", None),
        "resultHash": getattr(latest, "result_hash", None),
        "checks": checks,
        "failCount": fail_count,
        "warningCount": warning_count,
        "evaluatedAt": _now(),
    }


def build_release_certificate(
    project: Project,
    *,
    issue_mode: str,
    release_grade: str,
    readiness: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    content_rows = [
        {"file": row.get("file"), "sha256": row.get("sha256"), "sizeBytes": row.get("sizeBytes")}
        for row in artifacts
        if row.get("sha256")
    ]
    content_rows.sort(key=lambda row: str(row.get("file")))
    content_root = hashlib.sha256(
        json.dumps(content_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "certificateType": "PitGuard controlled engineering release baseline",
        "projectId": project.id,
        "projectName": project.name,
        "issueMode": issue_mode,
        "releaseGrade": release_grade,
        "snapshotHash": readiness.get("snapshotHash"),
        "calculationResultId": readiness.get("calculationResultId"),
        "calculationContractId": readiness.get("calculationContractId"),
        "inputSnapshotHash": readiness.get("inputSnapshotHash"),
        "calculationResultHash": readiness.get("resultHash"),
        "contentRootScope": "all artifacts registered before release_certificate.json and SHA256SUMS.txt",
        "contentRootHash": content_root,
        "artifactCountInRoot": len(content_rows),
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "readinessStatus": readiness.get("status"),
        "readinessChecks": readiness.get("checks"),
        "issuedAt": _now(),
        "professionalReviewRequired": True,
    }
    payload["certificateHash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
