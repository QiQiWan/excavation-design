from __future__ import annotations

from copy import deepcopy

import pytest

from app.calculation.engine import build_default_construction_cases, run_calculation, run_single_candidate_calculation
from app.quality.formal_gate import build_formal_report_gate
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import (
    Borehole,
    ConstructionStage,
    GeologicalModel,
    GroundwaterRecord,
    Point2D,
    Polyline2D,
    Project,
    SoilParameters,
    Stratum,
)
from app.services.concave_transfer_delivery import save_concave_transfer_detailing_approval
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.review_workflow import transition_review
from app.services.engineering_evidence_verification import (
    attach_engineering_evidence,
    verify_engineering_evidence,
)
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.services.support_layout_repair import adopt_support_layout_candidate
from app.services.support_candidate_contract import support_candidate_source_hash
from app.services.support_topology_contract import support_topology_hash
from app.version import version_manifest


L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def _project(template: str) -> Project:
    excavation = make_excavation_model(
        f"V3.70 {template}",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template=template),
    )
    return Project(name=f"V3.70 {template}", excavation=excavation, retainingSystem=system)


@pytest.fixture(scope="module")
def calculated_hub_project() -> Project:
    project = _project("junction_hub_frame")
    project.calculation_cases = build_default_construction_cases(project)
    before_hash = support_topology_hash(project)
    result = run_calculation(
        project,
        project.calculation_cases[0],
        auto_repair=False,
        include_candidate_comparison=False,
    )
    project.advanced_engineering["v370TestBeforeTopologyHash"] = before_hash
    project.advanced_engineering["v370TestResultTopologyHash"] = result.support_topology_hash
    return project


def test_v370_manifest_identifies_planar_multitopology_release() -> None:
    manifest = version_manifest()
    assert manifest["softwareVersion"] == "3.87.7"
    assert "design-core-governance-joint-optimization-detailing-delivery" in manifest["algorithmVersion"]
    assert "planar-6dof-member-envelope-rebar-feedback-kernel" in manifest["structuralKernelVersion"]
    assert manifest["exportSchemaVersion"] == "3.87"


def test_v367_readiness_is_split_into_four_explicit_levels() -> None:
    project = _project("compact_elbow_ring")
    audit = project.retaining_system.layout_summary["transferSystem"]
    readiness = audit["readiness"]
    assert readiness == {
        "geometryClosed": True,
        "loadPathClosed": True,
        "structuralModelClosed": True,
        "constructionStageClosed": False,
        "proxyCalculationReady": True,
        "formalCalculationReady": False,
    }
    assert audit["frameAnalysis"]["status"] == "pass"
    assert audit["frameAnalysis"]["analysisMode"] == "nominal_candidate_screening"
    assert audit["calculationReady"] is True
    assert audit["formalCalculationReady"] is False


def test_v368_three_topologies_have_distinct_members_and_column_support() -> None:
    systems = {template: _project(template).retaining_system for template in TEMPLATES}
    role_sets = {
        template: {beam.beam_role for beam in system.ring_beams}
        for template, system in systems.items()
    }
    assert role_sets["compact_elbow_ring"] == {"transfer_ring_beam"}
    assert "transfer_frame_beam" in role_sets["junction_hub_frame"]
    assert "transfer_brace" in role_sets["ring_chord_frame"]
    assert len({len(system.ring_beams) for system in systems.values()}) == 3
    assert all(len(system.columns) >= 6 for system in systems.values())
    assert all(
        beam.transfer_system_id and beam.start_node_id and beam.end_node_id and beam.load_path_id
        for system in systems.values()
        for beam in system.ring_beams
    )


def test_v369_optimizer_returns_topology_diverse_formal_abc() -> None:
    project = _project("compact_elbow_ring")
    _best, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 15,
            "candidatePoolLimit": 10,
        },
    )
    assert len(candidates) == 3
    assert {row.variable_summary["transferSystemTemplate"] for row in candidates} == set(TEMPLATES)
    assert len({row.variable_summary["schemeFamily"] for row in candidates}) == 3
    assert all(row.hard_constraints["passed"] for row in candidates)
    assert all(row.variable_summary["formalSchemeEligible"] for row in candidates)
    assert all(row.plan_geometry["transferBeams"] for row in candidates)



def test_v369_complete_candidate_rebuild_preserves_selected_transfer_topology() -> None:
    project = _project("compact_elbow_ring")
    _best, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 15,
            "candidatePoolLimit": 10,
        },
    )
    candidate = next(row for row in candidates if row.variable_summary["transferSystemTemplate"] == "junction_hub_frame")
    summary = run_single_candidate_calculation(project, candidate, index=1, use_cache=False)
    assert summary.get("error") is None
    assert summary["transferSystemTemplate"] == "junction_hub_frame"
    assert summary["transferTopologyClass"] == "junction_hub_frame"
    assert summary["transferBeamCount"] == 30
    assert summary["transferFrameStatus"] == "pass"
    assert summary["formalCalculationReady"] is True
    assert summary["autoDetailingStatus"] == "pass"


def test_v369_candidate_adoption_preserves_selected_transfer_topology() -> None:
    project = _project("compact_elbow_ring")
    _best, candidates = optimize_support_layout_candidates(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 15,
            "candidatePoolLimit": 10,
        },
    )
    from app.schemas.domain import SupportLayoutRepairSummary

    project.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
        candidateSourceHash=support_candidate_source_hash(project),
        candidateState="formal_ready",
        formalCandidateCount=3,
        comparisonEligibility={"state": "formal_ready", "currentSourceHash": support_candidate_source_hash(project)},
        candidateCount=3,
        bestCandidateId=candidates[0].id,
        selectedCandidateId=candidates[0].id,
        candidates=candidates,
        status="warning",
        summary="V3.70 adoption regression",
    )
    candidate = next(row for row in candidates if row.variable_summary["transferSystemTemplate"] == "junction_hub_frame")
    adopted = adopt_support_layout_candidate(project, candidate.id)
    audit = project.retaining_system.layout_summary["transferSystem"]
    assert adopted.status != "fail"
    assert audit["templateId"] == "junction_hub_frame"
    assert audit["topologyClass"] == "junction_hub_frame"
    assert len(project.retaining_system.ring_beams) == 30
    assert {beam.beam_role for beam in project.retaining_system.ring_beams} == {
        "transfer_ring_beam",
        "transfer_frame_beam",
    }

def test_v368_full_stage_frame_analysis_populates_transfer_beam_design(calculated_hub_project: Project) -> None:
    project = deepcopy(calculated_hub_project)
    audit = project.retaining_system.layout_summary["transferSystem"]
    readiness = audit["readiness"]
    frame = project.advanced_engineering["concaveTransferFrameAnalysis"]
    auto_detailing = project.advanced_engineering["concaveTransferAutoDetailing"]

    assert readiness["constructionStageClosed"] is True
    assert readiness["formalCalculationReady"] is True
    assert audit["formalCalculationReady"] is True
    assert frame["status"] == "pass"
    assert frame["stageCount"] > 0
    assert frame["maximumRelativeResidual"] < 1e-8
    assert frame["maximumDisplacementM"] > 0.0
    assert all(beam.design_result is not None for beam in project.retaining_system.ring_beams)
    assert all(beam.analysis_status == "calculated" for beam in project.retaining_system.ring_beams)
    assert auto_detailing["status"] == "pass"
    assert auto_detailing["metrics"]["designedTransferBeamCount"] == len(project.retaining_system.ring_beams)


def test_v370_topology_hash_tracks_structure_not_calculation_state(calculated_hub_project: Project) -> None:
    project = deepcopy(calculated_hub_project)
    assert project.advanced_engineering["v370TestBeforeTopologyHash"] == support_topology_hash(project)
    assert project.advanced_engineering["v370TestResultTopologyHash"] == support_topology_hash(project)
    project.retaining_system.ring_beams[0].axis.points[0].x += 0.125
    assert support_topology_hash(project) != project.advanced_engineering["v370TestBeforeTopologyHash"]


def _install_synthetic_credential_registry(tmp_path, monkeypatch) -> dict[str, dict[str, str | bool]]:
    registry_path = tmp_path / "verified-professional-credentials.json"
    registry_path.write_text(
        """{
  \"schema\": \"pitguard-professional-credential-registry-v1\",
  \"credentials\": [
    {
      \"registryRecordId\": \"TEST-ONLY-STRUCT-0001\",
      \"licenseType\": \"registered_structural_engineer\",
      \"licenseNumber\": \"TEST-STRUCT-0001\",
      \"holderName\": \"Synthetic Test Engineer\",
      \"jurisdiction\": \"TEST\",
      \"organization\": \"PitGuard synthetic test fixture\",
      \"status\": \"verified\",
      \"validUntil\": \"2099-12-31\",
      \"verificationSource\": \"synthetic-unit-test-registry\",
      \"verificationReference\": \"TEST-ONLY-NOT-A-REAL-LICENSE\"
    },
    {
      \"registryRecordId\": \"TEST-ONLY-GEO-0001\",
      \"licenseType\": \"registered_geotechnical_engineer\",
      \"licenseNumber\": \"TEST-GEO-0001\",
      \"holderName\": \"Synthetic Geotechnical Reviewer\",
      \"jurisdiction\": \"TEST\",
      \"organization\": \"PitGuard synthetic test fixture\",
      \"status\": \"verified\",
      \"validUntil\": \"2099-12-31\",
      \"verificationSource\": \"synthetic-unit-test-registry\",
      \"verificationReference\": \"TEST-ONLY-NOT-A-REAL-LICENSE\"
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PITGUARD_PROFESSIONAL_CREDENTIAL_REGISTRY", str(registry_path))
    return {
        "structural": {
            "licenseType": "registered_structural_engineer",
            "licenseNumber": "TEST-STRUCT-0001",
            "holderName": "Synthetic Test Engineer",
            "jurisdiction": "TEST",
            "verified": False,
        },
        "geotechnical": {
            "licenseType": "registered_geotechnical_engineer",
            "licenseNumber": "TEST-GEO-0001",
            "holderName": "Synthetic Geotechnical Reviewer",
            "jurisdiction": "TEST",
            "verified": False,
        },
    }


def _attach_synthetic_formal_evidence(
    project: Project,
    tmp_path,
    monkeypatch,
    credentials: dict[str, dict[str, str | bool]],
) -> None:
    """Attach complete, explicitly synthetic evidence for gate regression only."""
    project.strata = [
        Stratum(
            code="SYN-1",
            name="Synthetic silty clay",
            parameterSource="test",
            confidence="high",
            parameters=SoilParameters(
                unitWeight=19.0,
                saturatedUnitWeight=20.0,
                effectiveUnitWeight=9.8,
                cohesion=22.0,
                frictionAngle=24.0,
                elasticModulus=18000.0,
                poissonRatio=0.32,
                compressionModulus=8.0,
                permeabilityX=1.0e-7,
                permeabilityY=1.0e-7,
                permeabilityZ=5.0e-8,
                k0=0.6,
                horizontalSubgradeModulus=18000.0,
            ),
        )
    ]
    project.boreholes = [
        Borehole(
            code=f"SYN-BH-{index}",
            x=float(index * 20),
            y=float(index * 10),
            collarElevation=0.0,
            depth=35.0,
            waterLevels=[
                GroundwaterRecord(
                    waterLevel=-1.5,
                    description="Synthetic unit-test observation",
                    observedAt=f"2026-07-0{index}T08:00:00+08:00",
                )
            ],
        )
        for index in range(1, 4)
    ]
    project.geological_model = GeologicalModel(coverageAudit={"status": "pass", "source": "synthetic-unit-test"})
    topology_hash = support_topology_hash(project)
    case = project.calculation_cases[0]
    existing = {stage.stage_type for stage in case.stages}
    for stage_type, name in (
        ("excavation", "Synthetic excavation review stage"),
        ("bottom_slab", "Synthetic bottom slab stage"),
        ("support_removal", "Synthetic support removal stage"),
    ):
        if stage_type not in existing:
            case.stages.append(
                ConstructionStage(
                    name=name,
                    excavationElevation=-16.0,
                    activeSupportIds=[],
                    supportTopologyHash=topology_hash,
                    stageType=stage_type,
                    groundwaterLevelInside=-1.5,
                    groundwaterLevelOutside=-1.5,
                )
            )
    for stage in case.stages:
        stage.support_topology_hash = topology_hash
        stage.groundwater_level_inside = -1.5
        stage.groundwater_level_outside = -1.5
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    attach_engineering_evidence(
        project,
        domain="borehole",
        object_ids=[item.id for item in project.boreholes],
        filename="synthetic-investigation.json",
        content=b"synthetic investigation evidence; test only",
        content_type="application/json",
        revision="SYN-R1",
    )
    attach_engineering_evidence(
        project,
        domain="groundwater",
        object_ids=[record.id for item in project.boreholes for record in item.water_levels],
        filename="synthetic-groundwater.csv",
        content=b"synthetic groundwater evidence; test only",
        content_type="text/csv",
        observed_at="2026-07-01T08:00:00+08:00",
    )
    attach_engineering_evidence(
        project,
        domain="construction_stage",
        object_ids=[stage.id for stage in case.stages],
        filename="synthetic-construction-plan.pdf",
        content=b"synthetic construction plan evidence; test only",
        content_type="application/pdf",
        revision="SYN-C1",
    )
    verify_engineering_evidence(
        project,
        domain="borehole",
        object_ids=[item.id for item in project.boreholes],
        actor="Synthetic Geotechnical Reviewer",
        credential=credentials["geotechnical"],
        digital_signature_hash="d" * 64,
    )
    verify_engineering_evidence(
        project,
        domain="groundwater",
        object_ids=[record.id for item in project.boreholes for record in item.water_levels],
        actor="Synthetic Geotechnical Reviewer",
        credential=credentials["geotechnical"],
        digital_signature_hash="d" * 64,
    )
    verify_engineering_evidence(
        project,
        domain="construction_stage",
        object_ids=[stage.id for stage in case.stages],
        actor="Synthetic Test Engineer",
        credential=credentials["structural"],
        digital_signature_hash="e" * 64,
    )


def test_v371_formal_delivery_blocks_missing_real_data_and_credential(calculated_hub_project: Project) -> None:
    project = deepcopy(calculated_hub_project)
    quality = evaluate_support_layout_quality(project)
    before = build_formal_report_gate(project, quality, None)
    assert "shape_transfer_detailing" in {item.category for item in before.blocking_items}

    with pytest.raises(ValueError):
        save_concave_transfer_detailing_approval(
            project,
            evidence={
                "frameAnalysisStatus": "pass",
                "nodeDetailingStatus": "pass",
                "stageReviewStatus": "approved",
                "reactionIterationStatus": "pass",
                "spatialEffectStatus": "pass",
                "torsionDetailingStatus": "pass",
            },
            reviewer="unverified-reviewer",
            notes="This must remain blocked.",
        )


def test_v371_formal_delivery_requires_current_verified_synthetic_gate_fixture(
    calculated_hub_project: Project,
    tmp_path,
    monkeypatch,
) -> None:
    project = deepcopy(calculated_hub_project)
    monkeypatch.setattr(
        "app.services.concave_transfer_delivery._benchmark_certificate",
        lambda: {"status": "pass", "current": True, "referenceSoftware": "synthetic-test-only"},
    )
    credentials = _install_synthetic_credential_registry(tmp_path, monkeypatch)
    _attach_synthetic_formal_evidence(project, tmp_path, monkeypatch, credentials)
    credential = credentials["structural"]
    transition_review(project, "designer", "Synthetic Designer", "submit")
    transition_review(project, "checker", "Synthetic Checker", "accept")
    transition_review(project, "reviewer", "Synthetic Reviewer", "accept")
    transition_review(
        project,
        "approver",
        "Synthetic Test Engineer",
        "approve",
        credential=credential,
        digital_signature_hash="b" * 64,
    )
    readiness = save_concave_transfer_detailing_approval(
        project,
        evidence={
            "frameAnalysisStatus": "pass",
            "nodeDetailingStatus": "pass",
            "stageReviewStatus": "approved",
            "reactionIterationStatus": "pass",
            "spatialEffectStatus": "pass",
            "torsionDetailingStatus": "pass",
        },
        reviewer="Synthetic Test Engineer",
        professional_credential=credential,
        notes="Synthetic regression fixture; not a real engineering approval.",
        evidence_refs=["test:synthetic-data", "test:opensees-certificate"],
    )
    assert readiness["officialIssueReady"] is True
    after = build_formal_report_gate(project, evaluate_support_layout_quality(project), None)
    assert "shape_transfer_detailing" not in {item.category for item in after.blocking_items}

    project.retaining_system.ring_beams[0].axis.points[0].x += 0.1
    changed = build_formal_report_gate(project, evaluate_support_layout_quality(project), None)
    assert "shape_transfer_detailing" in {item.category for item in changed.blocking_items}
