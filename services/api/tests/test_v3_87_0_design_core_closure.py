from __future__ import annotations

from app.main import app
from app.schemas.domain import (
    CalculationResult,
    ExternalCollaborationRecord,
    Point2D,
    Polyline2D,
    PressureProfile,
    Project,
    StageCalculationResult,
    SupportForceResult,
    WallInternalForcePoint,
    WallInternalForceResult,
)
from app.services.design_core_v387 import (
    add_external_collaboration,
    build_delivery_quality,
    build_design_core_workflow,
    build_member_envelopes,
    build_parameter_confirmation,
    build_reinforcement_closure,
    build_rule_evidence,
    build_scheme_search_assurance,
    confirm_parameter_records,
    prepare_design_snapshot,
)
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.support_layout import SupportLayoutConfig
from app.version import SOFTWARE_VERSION, version_manifest


def _project() -> Project:
    outline = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
    excavation = make_excavation_model(
        "V3.87 design core",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in outline], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    return Project(name="V3.87 design core", excavation=excavation, retainingSystem=system)


def _attach_result(project: Project) -> None:
    support = project.retaining_system.supports[0]
    wall = project.retaining_system.diaphragm_walls[0]
    stage = StageCalculationResult(
        stage_id="stage-1",
        segment_id=wall.segment_id,
        pressure_profile=PressureProfile(points=[]),
        support_forces=[SupportForceResult(
            support_id=support.id,
            level_index=support.level_index,
            elevation=support.elevation,
            tributary_top=0.0,
            tributary_bottom=-5.0,
            axial_force=1200.0,
            axial_force_design=1500.0,
        )],
        wall_internal_force=WallInternalForceResult(
            segment_id=wall.segment_id,
            stage_id="stage-1",
            points=[WallInternalForcePoint(depth=5.0, elevation=-5.0, shear=320.0, moment=1800.0, displacement=0.018)],
            max_moment=1800.0,
            max_shear=320.0,
            max_displacement=0.018,
        ),
    )
    result = CalculationResult(project_id=project.id, case_id="case-1", stage_results=[stage], result_hash="calc-hash-v387")
    project.calculation_results = [result]


def test_v387_version_manifest() -> None:
    assert SOFTWARE_VERSION == "3.87.11"
    manifest = version_manifest()
    assert manifest["exportSchemaVersion"] == "3.87"
    assert "single-primary-design-flow" in manifest["resultPipelineVersion"]


def test_parameter_governance_identifies_formal_blockers() -> None:
    project = _project()
    result = build_parameter_confirmation(project)
    assert result["schema"] == "pitguard-parameter-governance-v387"
    assert result["total"] >= 10
    assert result["formalBlockerCount"] >= 1
    assert any(row["parameterKey"] == "design.surcharge" for row in result["records"])


def test_parameter_confirmation_is_explicit_and_traceable() -> None:
    project = _project()
    before = build_parameter_confirmation(project)
    blockers = [row for row in before["records"] if row["critical"] and not row["usableForFormalDesign"]]
    update = confirm_parameter_records(project, [{"parameterKey": row["parameterKey"], "formalDesignAllowed": True} for row in blockers], actor="checker")
    after = build_parameter_confirmation(project)
    assert update["count"] == len(blockers)
    assert after["formalBlockerCount"] <= before["formalBlockerCount"]
    assert all(row.confirmed_by == "checker" for row in project.parameter_provenance if row.parameter_key in set(update["updated"]))


def test_rule_evidence_is_clause_level_and_conservative() -> None:
    project = _project()
    evidence = build_rule_evidence(project)
    assert evidence["ruleCount"] >= 16
    assert evidence["executedRuleCount"] == 0
    assert all("implementationStatus" in row for row in evidence["rows"])
    assert "不代表标准全文" in evidence["boundary"]


def test_scheme_search_assurance_requires_diversity_and_full_calculation() -> None:
    project = _project()
    project.retaining_system.layout_summary["candidateSchemes"] = [
        {"id": "A", "label": "综合均衡", "schemeFamily": "direct_grid", "fullCalculation": {"status": "pass"}},
        {"id": "B", "label": "变形优先", "schemeFamily": "transfer_frame", "fullCalculation": {"status": "pass"}},
        {"id": "C", "label": "空间优先", "schemeFamily": "ring_chord", "fullCalculation": {"status": "pass"}},
    ]
    project.retaining_system.layout_summary["selectedCandidateId"] = "A"
    result = build_scheme_search_assurance(project)
    assert result["familyCount"] == 3
    assert result["fullyCalculatedCount"] == 3
    assert result["selectedCandidateFullyCalculated"] is True


def test_member_envelope_has_explicit_units_and_governing_stage() -> None:
    project = _project()
    _attach_result(project)
    result = build_member_envelopes(project)
    assert result["recordCount"] >= 4
    assert {row["responseType"] for row in result["records"]} >= {"wall_moment", "wall_shear", "wall_displacement", "support_axial_force"}
    assert all(row["unit"] and row["controllingStageId"] == "stage-1" for row in result["records"])


def test_reinforcement_closure_covers_walls_supports_beams_and_nodes() -> None:
    project = _project()
    result = build_reinforcement_closure(project)
    assert result["schema"] == "pitguard-reinforcement-feedback-closure-v387"
    assert result["componentCount"] > 0
    assert result["closureLoop"][0] == "选择实际钢筋"


def test_delivery_quality_requires_complete_drawings_and_report() -> None:
    project = _project()
    _attach_result(project)
    result = build_delivery_quality(project)
    assert result["status"] == "blocked"
    assert result["missingDrawingTypes"]
    assert result["missingReportSections"]
    assert "模型—图纸—钢筋表一致性" in result["qualityChecks"]


def test_design_snapshot_unifies_all_hash_domains() -> None:
    project = _project()
    _attach_result(project)
    result = prepare_design_snapshot(project, purpose="internal_review", actor="designer", persist=True)
    manifest = result["manifest"]
    assert manifest["consistencyHash"]
    assert manifest["designBasisHash"]
    assert manifest["parameterHash"]
    assert manifest["reinforcementHash"]
    assert len(project.design_snapshots) == 1


def test_external_collaboration_creates_design_review_without_field_workflow() -> None:
    project = _project()
    payload = ExternalCollaborationRecord(
        category="construction_reference",
        title="开挖顺序调整联系单",
        summary="施工单位提出原则性顺序调整，请核对原设计控制边界。",
        design_review_required=True,
    ).model_dump(mode="json", by_alias=False)
    result = add_external_collaboration(project, payload)
    assert result["reviewRequest"] is not None
    assert len(project.design_review_requests) == 1
    assert len(project.field_execution_snapshots) == 0
    assert len(project.deviation_events) == 0


def test_nine_stage_design_workflow_excludes_legacy_field_objects() -> None:
    project = _project()
    overview = build_design_core_workflow(project)
    assert overview["schema"] == "pitguard-design-core-workflow-v387"
    assert len(overview["stages"]) == 9
    assert overview["legacyConstructionFieldModules"]["primaryWorkflowUsage"] is False
    assert "外部施工或现场信息" in overview["externalCollaboration"]["boundary"]


def test_api_and_online_docs_expose_v387_design_core() -> None:
    paths = {route.path for route in app.routes}
    required = {
        "/api/projects/{project_id}/design-core",
        "/api/projects/{project_id}/design-core/parameters",
        "/api/projects/{project_id}/design-core/rules",
        "/api/projects/{project_id}/design-core/schemes",
        "/api/projects/{project_id}/design-core/member-envelopes",
        "/api/projects/{project_id}/design-core/reinforcement-closure",
        "/api/projects/{project_id}/design-core/delivery-quality",
        "/api/projects/{project_id}/design-core/design-snapshots",
        "/api/projects/{project_id}/design-core/collaboration",
    }
    assert required <= paths
    docs = build_online_documentation()
    ids = {row["id"] for row in docs["chapters"]}
    assert {"design-core", "scheme-search", "rebar-closure", "delivery-qc"} <= ids
