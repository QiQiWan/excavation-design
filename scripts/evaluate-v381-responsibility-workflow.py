from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from app.schemas.domain import ConstructionPlanStage, FieldExecutionSnapshot, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.support_layout import SupportLayoutConfig
from app.services.workflow_v381 import (
    assess_field_snapshot,
    build_scenario_envelope,
    evaluate_construction_plan_stage,
    generate_design_scenarios,
    migrate_legacy_stages,
    run_design_scenario_suite,
    set_design_scenario_approval,
    synchronize_design_control_case,
    workflow_overview,
)


def build_project() -> Project:
    outline = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
    excavation = make_excavation_model(
        "V3.81 responsibility evaluation",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in outline], closed=True),
        0.0,
        -16.0,
    )
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    return Project(name="V3.81 responsibility evaluation", excavation=excavation, retainingSystem=retaining)


def main() -> None:
    project = build_project()
    migration = migrate_legacy_stages(project)
    case, synchronization = synchronize_design_control_case(project)
    scenario_suite = generate_design_scenarios(project)
    final_stage_id = project.design_control_stages[-1].id
    approved_adverse = [
        next(row for row in project.design_scenarios if row.stage_id == final_stage_id and row.category == "surcharge"),
        next(row for row in project.design_scenarios if row.stage_id == final_stage_id and row.category == "groundwater"),
    ]
    scenario_approval = set_design_scenario_approval(project, [row.id for row in approved_adverse], approval_status="approved", enabled=True)
    scenario_started = time.perf_counter()
    scenario_execution = run_design_scenario_suite(project, [approved_adverse[0].id], max_scenarios=1)
    scenario_duration = time.perf_counter() - scenario_started
    scenario_execution.pop("fullResults", None)
    project.advanced_engineering["designScenarioExecution"] = scenario_execution
    scenario_envelope = build_scenario_envelope(project)
    control = project.design_control_stages[-1]

    acceptable_plan = ConstructionPlanStage(
        design_control_stage_id=control.id,
        planned_excavation_elevation=(control.excavation_elevation_lower + control.excavation_elevation_upper) / 2.0,
        planned_support_ids=list(control.required_support_ids),
        planned_groundwater_level=(control.groundwater_level_limit - 1.0) if control.groundwater_level_limit is not None else None,
        planned_surcharge=(0.8 * control.surcharge_limit) if control.surcharge_limit is not None else None,
        approval_status="submitted",
    )
    prohibited_plan = ConstructionPlanStage(
        design_control_stage_id=control.id,
        planned_excavation_elevation=control.excavation_elevation_lower - float(control.overexcavation_limit or 0.0) - 1.0,
        planned_support_ids=[],
        planned_surcharge=float(control.surcharge_limit or 1.0) * 1.5,
    )
    acceptable_compliance = evaluate_construction_plan_stage(project, acceptable_plan)
    prohibited_compliance = evaluate_construction_plan_stage(project, prohibited_plan)
    overview_without_construction = workflow_overview(project)

    project.construction_plan_stages = [acceptable_plan]
    normal_snapshot = FieldExecutionSnapshot(
        construction_plan_stage_id=acceptable_plan.id,
        actual_excavation_elevation=acceptable_plan.planned_excavation_elevation,
        active_support_ids=list(acceptable_plan.planned_support_ids),
        quality="verified",
    )
    abnormal_snapshot = FieldExecutionSnapshot(
        construction_plan_stage_id=acceptable_plan.id,
        actual_excavation_elevation=control.excavation_elevation_lower - float(control.overexcavation_limit or 0.0) - 0.5,
        active_support_ids=[],
        quality="verified",
    )
    normal_assessment = assess_field_snapshot(project, normal_snapshot, persist=False)
    abnormal_assessment = assess_field_snapshot(project, abnormal_snapshot, persist=True)
    overview = workflow_overview(project)
    docs = build_online_documentation()

    result = {
        "schema": "pitguard-v3.81-responsibility-workflow-evaluation-v1",
        "softwareVersion": docs["version"],
        "migration": migration,
        "synchronization": synchronization,
        "synchronizedCaseId": case.id if case else None,
        "designControlStageCount": len(project.design_control_stages),
        "designScenarioCount": len(project.design_scenarios),
        "scenariosPerStage": int(len(project.design_scenarios) / max(len(project.design_control_stages), 1)),
        "scenarioCategories": sorted({row.category for row in project.design_scenarios}),
        "scenarioApproval": {
            "approvedCount": scenario_approval["approvedCount"],
            "updatedCount": len(scenario_approval["updatedScenarioIds"]),
        },
        "scenarioExecution": {
            "durationSeconds": round(scenario_duration, 3),
            "summary": scenario_execution["summary"],
            "executedScenarioCode": scenario_execution["summaries"][0]["scenarioCode"] if scenario_execution["summaries"] else None,
            "executedScenarioStatus": scenario_execution["summaries"][0]["status"] if scenario_execution["summaries"] else None,
            "maxWallDisplacement": scenario_execution["summaries"][0]["maxWallDisplacement"] if scenario_execution["summaries"] else None,
            "maxSupportAxialForce": scenario_execution["summaries"][0]["maxSupportAxialForce"] if scenario_execution["summaries"] else None,
            "minimumSafetyFactor": scenario_execution["summaries"][0]["minSafetyFactor"] if scenario_execution["summaries"] else None,
        },
        "scenarioEnvelope": {
            "status": scenario_envelope["status"],
            "candidateResultCount": scenario_envelope["candidateResultCount"],
            "pendingFormalScenarioCount": len(scenario_envelope["pendingFormalScenarioCodes"]),
            "maxWallDisplacement": scenario_envelope["envelope"]["maxWallDisplacement"],
            "minSafetyFactor": scenario_envelope["envelope"]["minSafetyFactor"],
        },
        "acceptablePlan": {
            "grade": acceptable_compliance["grade"],
            "status": acceptable_compliance["status"],
            "withinDesignEnvelope": acceptable_compliance["withinDesignEnvelope"],
        },
        "prohibitedPlan": {
            "grade": prohibited_compliance["grade"],
            "status": prohibited_compliance["status"],
            "prohibited": prohibited_compliance["prohibited"],
            "issueCount": prohibited_compliance["issueCount"],
        },
        "normalFieldSnapshot": {
            "status": normal_assessment["status"],
            "withinDesignEnvelope": normal_assessment["withinDesignEnvelope"],
            "workHoldRecommended": normal_assessment["workHoldRecommended"],
        },
        "abnormalFieldSnapshot": {
            "status": abnormal_assessment["status"],
            "withinDesignEnvelope": abnormal_assessment["withinDesignEnvelope"],
            "workHoldRecommended": abnormal_assessment["workHoldRecommended"],
            "recalculationRequired": abnormal_assessment["recalculationRequired"],
            "designerReviewRequired": abnormal_assessment["designerReviewRequired"],
            "eventCount": len(abnormal_assessment["events"]),
            "criticalEventCount": sum(row["severity"] == "critical" for row in abnormal_assessment["events"]),
        },
        "gateSeparation": {
            "designExplicitExclusionCount": len(overview["designIssue"]["explicitExclusions"]),
            "constructionPlanBlocksConstructionOnly": "CONSTRUCTION_PLAN" in overview_without_construction["constructionPreparation"]["blockingCodes"],
            "unapprovedPlanBlocksConstructionOnly": "CONSTRUCTION_PLAN_APPROVAL" in overview["constructionPreparation"]["blockingCodes"],
            "fieldSnapshotBlocksFieldOnly": "FIELD_SNAPSHOT" in overview["fieldExecution"]["blockingCodes"],
            "responsibilityDomainCount": len(overview["responsibilityBoundary"]),
        },
        "onlineDocumentation": {
            "chapterIds": [row["id"] for row in docs["chapters"]],
            "roadmapLastVersion": docs["releaseRoadmap"][-1]["version"],
            "responsibilityDomainCount": len(docs["responsibilityWorkflow"]["domains"]),
        },
        "boundary": "该评估验证业务对象、责任门禁和偏差闭环；不以未执行的不利情景伪造结构计算结果。",
    }
    output = ROOT / "docs" / "releases" / "V3_81_0_RESPONSIBILITY_WORKFLOW_EVALUATION.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
