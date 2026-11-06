from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.schemas.domain import ConstructionPlanStage, DesignControlStage, FieldExecutionSnapshot
from app.services.calculation_state import invalidate_calculation_state
from app.services.workflow_v381 import (
    assess_field_snapshot,
    build_scenario_envelope,
    design_control_signature,
    evaluate_construction_plan_stage,
    evaluate_construction_preparation_gate,
    evaluate_design_issue_gate,
    evaluate_field_release_gate,
    generate_design_scenarios,
    invalidate_design_scenario_results,
    migrate_legacy_stages,
    synchronize_design_control_case,
    set_design_scenario_approval,
    validate_design_control_stages,
    workflow_overview,
)
from app.storage.repository import ProjectRepository, get_repository
from app.tasks.manager import task_manager

router = APIRouter(prefix="/api/projects/{project_id}/workflow", tags=["business-workflow-v381"])


@router.get("")
def get_workflow_overview(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    changed = not bool(project.design_control_stages)
    result = workflow_overview(project)
    if changed:
        repo.save(project, action="workflow.migrate_on_read", summary="Migrate legacy calculation stages to designer control stages")
    return result


@router.post("/migrate-legacy-stages")
def migrate_stages(project_id: str, force: bool = False, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    result = migrate_legacy_stages(project, force=force)
    sync_case, sync = synchronize_design_control_case(project)
    if result.get("migrated"):
        invalidate_calculation_state(
            project,
            reason="施工阶段语义已迁移为设计控制工况",
            rebuild_cases=False,
            preserve_cases=True,
        )
    repo.save(project, action="workflow.migrate_legacy_stages", summary="Migrate legacy stages to design control stages")
    return {"migration": result, "synchronization": sync, "caseId": sync_case.id if sync_case else None, "workflow": workflow_overview(project)}


@router.get("/design-control-stages")
def get_design_control_stages(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    if not project.design_control_stages:
        migrate_legacy_stages(project)
        repo.save(project, action="workflow.initialize_design_control_stages", summary="Initialize design control stages")
    return {
        "semanticType": "design_control_stage",
        "stages": [row.model_dump(mode="json", by_alias=True) for row in project.design_control_stages],
        "validation": validate_design_control_stages(project),
        "responsibility": "设计单位",
        "excludedFields": ["实际开挖日期", "现场安装时间", "现场实测轴力", "阶段验收"],
    }


@router.put("/design-control-stages")
def save_design_control_stages(
    project_id: str,
    stages: list[DesignControlStage] = Body(default_factory=list),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    previous_signature = design_control_signature(list(project.design_control_stages or []))
    project.design_control_stages = stages
    validation = validate_design_control_stages(project)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail={"code": "DESIGN_CONTROL_STAGE_INVALID", "validation": validation})
    current_signature = design_control_signature(list(project.design_control_stages or []))
    numerical_inputs_changed = previous_signature != current_signature
    case = next(
        (row for row in reversed(project.calculation_cases or []) if row.source == "synchronized" and row.name == "设计控制工况计算"),
        None,
    )
    if numerical_inputs_changed or case is None:
        case, sync = synchronize_design_control_case(project)
        calculation_state = invalidate_calculation_state(
            project,
            reason="设计控制工况数值或控制边界已修改",
            rebuild_cases=False,
            preserve_cases=True,
        )
        scenario_state = invalidate_design_scenario_results(project, reason="设计控制工况已修改")
    else:
        sync = {
            "synchronized": False,
            "reason": "approval_metadata_only",
            "caseId": case.id if case else None,
            "stageCount": len(stages),
            "validation": validation,
        }
        calculation_state = {
            "status": "preserved",
            "reason": "仅审批/冻结状态变化，数值输入未变化",
            "requiresRecalculation": False,
        }
        scenario_state = {
            "status": "preserved",
            "reason": "仅审批/冻结状态变化，既有情景结果保持有效",
        }
    repo.save(project, action="workflow.save_design_control_stages", summary="Save designer-owned control stages")
    return {
        "stages": [row.model_dump(mode="json", by_alias=True) for row in project.design_control_stages],
        "validation": validation,
        "synchronization": sync,
        "caseId": case.id if case else None,
        "numericalInputsChanged": numerical_inputs_changed,
        "calculationInvalidated": numerical_inputs_changed,
        "calculationState": calculation_state,
        "scenarioState": scenario_state,
    }


@router.post("/design-scenarios/generate")
def generate_scenarios(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    suite = generate_design_scenarios(project)
    repo.save(project, action="workflow.generate_design_scenarios", summary="Generate design envelope scenarios")
    return {"suite": suite, "scenarios": [row.model_dump(mode="json", by_alias=True) for row in project.design_scenarios]}


@router.get("/design-scenarios")
def get_design_scenarios(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return {"scenarios": [row.model_dump(mode="json", by_alias=True) for row in project.design_scenarios], "envelope": dict((project.advanced_engineering or {}).get("designScenarioEnvelope") or {})}


@router.patch("/design-scenarios/approval")
def update_design_scenario_approval(
    project_id: str,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    scenario_ids = [str(item) for item in list(payload.get("scenarioIds") or [])]
    if not scenario_ids:
        raise HTTPException(status_code=422, detail={"code": "DESIGN_SCENARIO_SELECTION_REQUIRED"})
    try:
        result = set_design_scenario_approval(
            project,
            scenario_ids,
            approval_status=str(payload.get("approvalStatus") or "approved"),
            enabled=payload.get("enabled"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo.save(project, action="workflow.update_design_scenario_approval", summary="Update design scenario approvals")
    return {"approval": result, "scenarios": [row.model_dump(mode="json", by_alias=True) for row in project.design_scenarios]}


@router.post("/design-scenarios/execute")
def execute_design_scenarios(
    project_id: str,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    selected = [str(item) for item in list(payload.get("scenarioIds") or [])]
    approved = [
        row.id for row in project.design_scenarios
        if row.enabled and row.approval_status == "approved" and row.category != "baseline"
    ]
    if selected and any(item not in approved for item in selected):
        raise HTTPException(status_code=422, detail={"code": "DESIGN_SCENARIO_NOT_APPROVED", "approvedScenarioIds": approved})
    if not selected and not approved:
        raise HTTPException(status_code=422, detail={"code": "APPROVED_DESIGN_SCENARIO_REQUIRED"})
    try:
        task_manager.ensure_worker_available()
        task = task_manager.submit(
            project_id=project_id,
            operation="design_scenario_envelope",
            payload={
                "scenarioIds": selected,
                "maxScenarios": max(1, min(int(payload.get("maxScenarios") or 12), 50)),
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return task.as_dict(include_logs=True)


@router.post("/design-scenarios/envelope")
def create_scenario_envelope(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    result = build_scenario_envelope(project)
    repo.save(project, action="workflow.build_scenario_envelope", summary="Build envelope from completed scenario calculations")
    return result


@router.get("/gates/design-issue")
def design_issue_gate(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_design_issue_gate(repo.require(project_id))


@router.get("/gates/construction-preparation")
def construction_preparation_gate(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_construction_preparation_gate(repo.require(project_id))


@router.get("/gates/field-release")
def field_release_gate(project_id: str, construction_plan_stage_id: str | None = None, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_field_release_gate(repo.require(project_id), construction_plan_stage_id)


@router.get("/construction-plan-stages")
def get_construction_plan_stages(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    rows = []
    for plan in project.construction_plan_stages:
        rows.append({
            **plan.model_dump(mode="json", by_alias=True),
            "compliance": evaluate_construction_plan_stage(project, plan),
        })
    return {"stages": rows, "responsibility": "施工单位提交；设计单位只处理超出设计允许域的事项。"}


@router.post("/construction-plan-stages")
def add_construction_plan_stage(
    project_id: str,
    stage: ConstructionPlanStage,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    if not any(row.id == stage.design_control_stage_id for row in project.design_control_stages):
        raise HTTPException(status_code=422, detail="施工计划阶段必须绑定当前设计控制工况。")
    project.construction_plan_stages = [row for row in project.construction_plan_stages if row.id != stage.id] + [stage]
    compliance = evaluate_construction_plan_stage(project, stage)
    repo.save(project, action="workflow.save_construction_plan_stage", summary="Save contractor construction plan stage")
    return {"stage": stage.model_dump(mode="json", by_alias=True), "compliance": compliance, "gate": evaluate_construction_preparation_gate(project)}


@router.get("/construction-plan-stages/{plan_stage_id}/compliance")
def construction_plan_compliance(project_id: str, plan_stage_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    stage = next((row for row in project.construction_plan_stages if row.id == plan_stage_id), None)
    if stage is None:
        raise HTTPException(status_code=404, detail="Construction plan stage not found")
    return evaluate_construction_plan_stage(project, stage)


@router.post("/field-snapshots")
def add_field_snapshot(
    project_id: str,
    snapshot: FieldExecutionSnapshot,
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    if not any(row.id == snapshot.construction_plan_stage_id for row in project.construction_plan_stages):
        raise HTTPException(status_code=422, detail="现场快照必须绑定施工计划阶段。")
    project.field_execution_snapshots = [row for row in project.field_execution_snapshots if row.id != snapshot.id] + [snapshot]
    assessment = assess_field_snapshot(project, snapshot, persist=True)
    repo.save(project, action="workflow.add_field_snapshot", summary="Add field execution snapshot and deviation assessment")
    return {"snapshot": snapshot.model_dump(mode="json", by_alias=True), "assessment": assessment, "gate": evaluate_field_release_gate(project, snapshot.construction_plan_stage_id)}


@router.get("/field-snapshots")
def get_field_snapshots(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return {"snapshots": [row.model_dump(mode="json", by_alias=True) for row in project.field_execution_snapshots]}


@router.get("/deviations")
def get_deviations(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return {
        "events": [row.model_dump(mode="json", by_alias=True) for row in project.deviation_events],
        "openCount": sum(row.status not in {"accepted", "closed"} for row in project.deviation_events),
    }


@router.patch("/deviations/{event_id}")
def update_deviation(
    project_id: str,
    event_id: str,
    payload: dict = Body(default_factory=dict),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    event = next((row for row in project.deviation_events if row.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Deviation event not found")
    if "status" in payload:
        allowed = {"open", "assigned", "responded", "accepted", "closed"}
        status = str(payload["status"])
        if status not in allowed:
            raise HTTPException(status_code=422, detail={"code": "DEVIATION_STATUS_INVALID", "allowed": sorted(allowed)})
        event.status = status
    if "resolution" in payload:
        event.resolution = str(payload["resolution"])
    event.updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    repo.save(project, action="workflow.update_deviation", summary=f"Update deviation {event_id}")
    return event.model_dump(mode="json", by_alias=True)
