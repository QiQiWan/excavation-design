from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.schemas.domain import ProfessionalCredential, Project
from app.services.transfer_data_assurance import evaluate_transfer_engineering_data
from app.services.professional_credential_registry import verify_professional_credential
from app.services.review_workflow import review_status
from app.services.support_topology_contract import support_topology_hash
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION, SOFTWARE_VERSION, STRUCTURAL_KERNEL_VERSION

_REQUIRED_EVIDENCE = {
    "frameAnalysisStatus": "闭合内环梁平面轴力—弯矩—剪力复核",
    "nodeDetailingStatus": "径向支撑—环梁—围檩节点承压、加腋、锚固与附加钢筋深化",
    "stageReviewStatus": "安装、开挖、换撑和拆撑施工阶段复核",
    "reactionIterationStatus": "墙—围檩—转接框架反力迭代收敛",
    "spatialEffectStatus": "三维偏心、扭转、刚域和半刚性节点子模型复核",
    "torsionDetailingStatus": "环梁抗扭纵筋和闭合箍筋深化",
}
_PASS_STATES = {"pass", "approved", "verified"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _benchmark_certificate() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[4] / "packages" / "benchmarks" / "v377_structural_kernel_certificate.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "invalid", "reason": str(exc), "path": str(path)}
    current = bool(
        payload.get("status") == "pass"
        and payload.get("structuralKernelVersion") == STRUCTURAL_KERNEL_VERSION
    )
    return {**payload, "current": current, "path": str(path)}


def _credential_status(record: dict[str, Any]) -> dict[str, Any]:
    raw = dict(record.get("professionalCredential") or {})
    verification = verify_professional_credential(raw if raw else None)
    credential = dict(verification.get("credential") or {})
    number = str(credential.get("licenseNumber") or "")
    return {
        "status": verification.get("status", "fail"),
        "verified": bool(verification.get("verified")),
        "reason": verification.get("reason"),
        "licenseType": credential.get("licenseType"),
        "holderName": credential.get("holderName"),
        "licenseNumberMasked": ("***" + number[-4:]) if len(number) >= 4 else None,
        "verificationSource": credential.get("verificationSource"),
        "verificationReference": credential.get("verificationReference"),
        "registryRecordId": verification.get("registryRecordId"),
        "registryPath": verification.get("registryPath"),
    }


def evaluate_concave_transfer_delivery(project: Project, transfer_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    audit = dict(transfer_audit or {})
    required = bool(audit.get("required"))
    current_hash = support_topology_hash(project) if project.retaining_system else None
    record = dict((project.advanced_engineering or {}).get("concaveTransferDetailingApproval") or {})
    evidence = dict(record.get("evidence") or {})
    data_assurance = evaluate_transfer_engineering_data(project)
    benchmark = _benchmark_certificate()
    credential = _credential_status(record)
    workflow = review_status(project)
    workflow_approved = bool(workflow.get("approvalValid") and workflow.get("registeredStructuralApproverValid"))
    evidence_checks = [
        {
            "key": key,
            "label": label,
            "status": str(evidence.get(key) or "missing"),
            "passed": str(evidence.get(key) or "").strip().lower() in _PASS_STATES,
        }
        for key, label in _REQUIRED_EVIDENCE.items()
    ]
    topology_current = bool(current_hash and record.get("supportTopologyHash") == current_hash)
    reviewer_complete = bool(str(record.get("reviewer") or "").strip())
    calculation_ready = bool(audit.get("calculationReady", not required))
    formal_calculation_ready = bool(audit.get("formalCalculationReady", calculation_ready if not required else False))
    auto_detailing = dict((project.advanced_engineering or {}).get("concaveTransferAutoDetailing") or {})
    auto_detailing_current = bool(current_hash and auto_detailing.get("supportTopologyHash") == current_hash)
    evidence_complete = all(item["passed"] for item in evidence_checks)
    approved = (
        not required
        or (
            formal_calculation_ready
            and topology_current
            and reviewer_complete
            and evidence_complete
            and data_assurance.get("formalDataReady")
            and benchmark.get("status") == "pass"
            and benchmark.get("current")
            and credential.get("verified")
            and workflow_approved
            and str(record.get("status") or "").strip().lower() in _PASS_STATES
        )
    )
    reason_codes: list[str] = []
    if required and not calculation_ready:
        reason_codes.append("transfer_calculation_not_ready")
    if required and calculation_ready and not formal_calculation_ready:
        reason_codes.append("transfer_construction_stage_analysis_not_closed")
    if required and (not auto_detailing or not auto_detailing_current or auto_detailing.get("status") != "pass"):
        reason_codes.append("automatic_transfer_detailing_incomplete_or_stale")
    if required and not record:
        reason_codes.append("detailing_approval_missing")
    if required and record and not topology_current:
        reason_codes.append("detailing_topology_hash_mismatch")
    if required and not reviewer_complete:
        reason_codes.append("reviewer_missing")
    if required and not data_assurance.get("formalDataReady"):
        reason_codes.append("real_engineering_data_assurance_failed")
    if required and not (benchmark.get("status") == "pass" and benchmark.get("current")):
        reason_codes.append("structural_software_benchmark_missing_failed_or_stale")
    if required and not credential.get("verified"):
        reason_codes.append("registered_structural_engineer_credential_missing_or_unverified")
    if required and not workflow_approved:
        reason_codes.append("project_review_workflow_not_approved")
    reason_codes.extend(
        f"{item['key']}_missing_or_unpassed"
        for item in evidence_checks
        if required and not item["passed"]
    )
    return {
        "required": required,
        "status": "pass" if approved else ("not_required" if not required else "blocked"),
        "officialIssueReady": approved,
        "calculationReady": calculation_ready,
        "formalCalculationReady": formal_calculation_ready,
        "autoDetailing": auto_detailing,
        "autoDetailingCurrent": auto_detailing_current,
        "supportTopologyHash": current_hash,
        "approvedTopologyHash": record.get("supportTopologyHash"),
        "topologyCurrent": topology_current,
        "reviewer": record.get("reviewer"),
        "approvedAt": record.get("approvedAt"),
        "evidenceChecks": evidence_checks,
        "evidenceComplete": evidence_complete,
        "engineeringDataAssurance": data_assurance,
        "benchmarkCertificate": benchmark,
        "professionalCredential": credential,
        "projectReviewWorkflowApproved": workflow_approved,
        "projectReviewWorkflow": workflow,
        "reasonCodes": list(dict.fromkeys(reason_codes)),
        "record": record,
    }


def save_concave_transfer_detailing_approval(
    project: Project,
    *,
    evidence: dict[str, Any],
    reviewer: str,
    notes: str | None = None,
    evidence_refs: list[str] | None = None,
    professional_credential: dict[str, Any] | ProfessionalCredential | None = None,
    status: str = "approved",
) -> dict[str, Any]:
    if not project.retaining_system:
        raise ValueError("项目尚未生成围护支撑体系。")
    transfer_audit = dict((project.retaining_system.layout_summary or {}).get("transferSystem") or {})
    if not transfer_audit.get("required"):
        raise ValueError("当前支撑体系不需要异形转接节点深化审批。")
    if not transfer_audit.get("calculationReady"):
        raise ValueError("异形转接体系尚未形成完整计算传力路径，不能提交节点深化审批。")
    if not transfer_audit.get("formalCalculationReady"):
        raise ValueError("异形转接体系尚未完成逐施工阶段平面框架内力包络，不能提交正式深化审批。")
    auto_detailing = dict((project.advanced_engineering or {}).get("concaveTransferAutoDetailing") or {})
    if auto_detailing.get("supportTopologyHash") != support_topology_hash(project) or auto_detailing.get("status") != "pass":
        raise ValueError("自动生成的转接梁、节点和施工阶段深化证据缺失、失败或已过期。")
    reviewer = str(reviewer or "").strip()
    if not reviewer:
        raise ValueError("必须填写复核人。")
    claim = professional_credential if isinstance(professional_credential, ProfessionalCredential) else dict(professional_credential or {})
    verification = verify_professional_credential(claim)
    if not verification.get("verified"):
        raise ValueError("注册结构工程师执业资格未通过受信任的服务端登记库核验：" + str(verification.get("reason") or "unknown"))
    credential = ProfessionalCredential(**dict(verification["credential"]))
    if credential.license_type != "registered_structural_engineer":
        raise ValueError("异形转接结构正式深化必须由注册结构工程师复核。")
    if credential.holder_name.strip().casefold() != reviewer.casefold():
        raise ValueError("复核人姓名与注册结构工程师资格持有人不一致。")
    data_assurance = evaluate_transfer_engineering_data(project)
    if not data_assurance.get("formalDataReady"):
        raise ValueError("真实地质、水位和施工期资料未通过完整性与来源门禁。")
    benchmark = _benchmark_certificate()
    if benchmark.get("status") != "pass" or not benchmark.get("current"):
        raise ValueError("当前算法版本缺少有效的成熟结构软件基准验证证书。")
    workflow = review_status(project)
    if not workflow.get("approvalValid"):
        raise ValueError("项目四级设计—校核—审核—审定流程尚未完成或审批快照已失效。")
    if not workflow.get("registeredStructuralApproverValid"):
        raise ValueError("最终审定缺少经服务端登记库核验的注册结构工程师凭证或数字签名哈希。")
    normalized_evidence = {
        key: str(evidence.get(key) or "missing").strip().lower()
        for key in _REQUIRED_EVIDENCE
    }
    missing = [
        _REQUIRED_EVIDENCE[key]
        for key, value in normalized_evidence.items()
        if value not in _PASS_STATES
    ]
    normalized_status = str(status or "approved").strip().lower()
    if normalized_status in _PASS_STATES and missing:
        raise ValueError("以下深化证据尚未通过：" + "；".join(missing))
    record = {
        "status": normalized_status,
        "reviewer": reviewer,
        "professionalCredential": credential.model_dump(mode="json", by_alias=True),
        "notes": str(notes or "").strip() or None,
        "evidenceRefs": [str(item) for item in (evidence_refs or []) if str(item).strip()],
        "evidence": normalized_evidence,
        "supportTopologyHash": support_topology_hash(project),
        "transferTemplateId": transfer_audit.get("templateId"),
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "approvedAt": _now(),
    }
    advanced = dict(project.advanced_engineering or {})
    history = list(advanced.get("concaveTransferDetailingApprovalHistory") or [])
    previous = advanced.get("concaveTransferDetailingApproval")
    if previous:
        history.append(previous)
        history = history[-20:]
    advanced["concaveTransferDetailingApprovalHistory"] = history
    advanced["concaveTransferDetailingApproval"] = record
    project.advanced_engineering = advanced
    return evaluate_concave_transfer_delivery(project, transfer_audit)
