from __future__ import annotations

import math
from typing import Any

from app.rules.gb50010.detailing_rules import required_rebar_anchorage_length_mm
from app.schemas.domain import Project, ReinforcementGroup
from app.services.support_topology_contract import support_topology_hash
from app.version import SOFTWARE_VERSION

_REQUIRED_REBAR_TYPES = {"longitudinal", "stirrup", "distribution", "tie", "additional"}


def _bar_area(diameter_mm: float) -> float:
    return math.pi * float(diameter_mm) ** 2 / 4.0


def _torsion_detailing(beam: Any) -> dict[str, Any]:
    width_m = max(float(beam.section.width or beam.section.diameter or 1.0), 0.30)
    height_m = max(float(beam.section.height or beam.section.diameter or 1.0), 0.30)
    torsion_knm = max(float(getattr(beam, "design_torsion", 0.0) or 0.0), 0.0)
    core_width_mm = max((width_m - 0.10) * 1000.0, 200.0)
    core_height_mm = max((height_m - 0.10) * 1000.0, 200.0)
    core_area_mm2 = core_width_mm * core_height_mm
    core_perimeter_mm = 2.0 * (core_width_mm + core_height_mm)
    fy = 360.0
    t_nmm = torsion_knm * 1.0e6
    required_closed_stirrup_area_per_m = t_nmm / max(2.0 * core_area_mm2 * fy, 1.0) * 1000.0
    required_longitudinal_area = t_nmm * core_perimeter_mm / max(2.0 * core_area_mm2 * fy, 1.0)
    stirrup_diameter = 12.0
    stirrup_legs = 4
    selected_spacing = 200.0
    for spacing in (200.0, 150.0, 100.0, 75.0):
        provided = stirrup_legs * _bar_area(stirrup_diameter) * 1000.0 / spacing
        if provided >= max(required_closed_stirrup_area_per_m, 1.0):
            selected_spacing = spacing
            break
    provided_stirrup = stirrup_legs * _bar_area(stirrup_diameter) * 1000.0 / selected_spacing
    longitudinal_diameter = 20.0
    longitudinal_count = max(4, int(math.ceil(required_longitudinal_area / max(_bar_area(longitudinal_diameter), 1.0))))
    # Keep bars symmetric around the closed perimeter.
    longitudinal_count = int(math.ceil(longitudinal_count / 4.0) * 4)
    provided_longitudinal = longitudinal_count * _bar_area(longitudinal_diameter)
    status = "pass" if provided_stirrup >= required_closed_stirrup_area_per_m and provided_longitudinal >= required_longitudinal_area else "fail"
    return {
        "status": status,
        "designTorsionKnm": round(torsion_knm, 3),
        "coreAreaMm2": round(core_area_mm2, 1),
        "corePerimeterMm": round(core_perimeter_mm, 1),
        "requiredClosedStirrupAreaPerM": round(required_closed_stirrup_area_per_m, 1),
        "providedClosedStirrupAreaPerM": round(provided_stirrup, 1),
        "stirrupDiameterMm": stirrup_diameter,
        "stirrupSpacingMm": selected_spacing,
        "stirrupLegs": stirrup_legs,
        "requiredLongitudinalTorsionAreaMm2": round(required_longitudinal_area, 1),
        "providedLongitudinalTorsionAreaMm2": round(provided_longitudinal, 1),
        "longitudinalDiameterMm": longitudinal_diameter,
        "longitudinalCount": longitudinal_count,
        "formulaBoundary": "闭合薄壁空间桁架筛查；正式配筋应按适用规范条文和节点三维有限元复核。",
    }


def _ensure_torsion_reinforcement(beam: Any, detail: dict[str, Any]) -> None:
    existing = [group for group in beam.reinforcement if str(group.design_source or "").startswith("V3.71 torsion")]
    if existing:
        return
    beam.reinforcement.extend([
        ReinforcementGroup(
            name="转接环梁抗扭闭合箍筋",
            bar_type="stirrup",
            diameter=float(detail["stirrupDiameterMm"]),
            spacing=float(detail["stirrupSpacingMm"]),
            grade="HRB400",
            location_description="沿转接梁全长闭合布置，节点区按加密要求继续收紧",
            zone_type="full_length",
            stirrup_legs=int(detail["stirrupLegs"]),
            design_source="V3.71 torsion closed-stirrup screening",
            area_per_meter=float(detail["providedClosedStirrupAreaPerM"]),
            required_area_per_meter=float(detail["requiredClosedStirrupAreaPerM"]),
            check_status="pass" if detail["status"] == "pass" else "fail",
        ),
        ReinforcementGroup(
            name="转接环梁抗扭纵向附加筋",
            bar_type="additional",
            diameter=float(detail["longitudinalDiameterMm"]),
            count=int(detail["longitudinalCount"]),
            grade="HRB400",
            location_description="沿截面周边四角及边中均匀布置并连续锚固",
            zone_type="full_length",
            design_source="V3.71 torsion longitudinal reinforcement screening",
            check_status="pass" if detail["status"] == "pass" else "fail",
        ),
    ])


def _node_detailing(project: Project, node: Any) -> dict[str, Any]:
    support = next((item for item in project.retaining_system.supports if item.id == node.support_id), None)
    force = max(float(getattr(support, "design_axial_force", 0.0) or 0.0), 0.0) if support else 0.0
    plate = node.bearing_plate
    area = max(float(getattr(plate, "bearing_area", 0.0) or 0.0), 1.0e-4)
    bearing_stress_kpa = force / area
    concrete_capacity_kpa = 15000.0
    utilization = bearing_stress_kpa / concrete_capacity_kpa
    main_diameter = max((float(group.diameter) for group in node.reinforcement if group.bar_type in {"longitudinal", "additional"}), default=20.0)
    anchorage = required_rebar_anchorage_length_mm(main_diameter, "HRB400", seismic=False)
    haunch_required = utilization > 0.80 or force > 3000.0
    support_width = max(float(getattr(getattr(support, "section", None), "width", 0.8) or 0.8), 0.4) if support else 0.8
    support_height = max(float(getattr(getattr(support, "section", None), "height", 0.8) or 0.8), 0.4) if support else 0.8
    haunch = {
        "required": haunch_required,
        "lengthM": round(max(0.5, 0.75 * support_height), 3) if haunch_required else 0.0,
        "widthM": round(max(0.4, 1.25 * support_width), 3) if haunch_required else 0.0,
        "depthM": round(max(0.25, 0.35 * support_height), 3) if haunch_required else 0.0,
    }
    status = "pass" if plate is not None and utilization <= 1.0 and bool(node.reinforcement) else "fail"
    spatial = {
        "status": status,
        "designAxialForceKn": round(force, 3),
        "bearingAreaM2": round(area, 4),
        "bearingStressKpa": round(bearing_stress_kpa, 3),
        "bearingCapacityKpa": concrete_capacity_kpa,
        "bearingUtilization": round(utilization, 4),
        "requiredAnchorageLengthMm": anchorage,
        "haunch": haunch,
        "anchorageNote": "纵向受力筋与抗扭附加筋应穿越节点核心区并满足直锚、弯锚或机械锚固的项目级构造复核。",
    }
    node.spatial_detailing = spatial
    if plate is not None:
        plate.bearing_stress = round(bearing_stress_kpa, 3)
        plate.bearing_capacity = concrete_capacity_kpa
        plate.check_status = "pass" if utilization <= 1.0 else "fail"
        plate.design_note = "V3.71 局部承压筛查；扩散角、裂缝、节点核心区及三维应力需专业复核。"
    return spatial


def build_concave_transfer_detailing_package(project: Project) -> dict[str, Any]:
    system = project.retaining_system
    if system is None:
        return {"status": "not_applicable", "required": False, "summary": "围护体系尚未生成。"}
    audit = dict((system.layout_summary or {}).get("transferSystem") or {})
    required = bool(audit.get("required"))
    if not required:
        return {"status": "not_required", "required": False, "summary": "当前平面不需要异形转接体系深化。"}

    beams = [
        beam for beam in (system.ring_beams or [])
        if str(beam.code).startswith(("TR-", "TF-", "TB-"))
        or str(getattr(beam, "beam_role", "")).startswith("transfer_")
    ]
    beam_rows: list[dict[str, Any]] = []
    for beam in beams:
        torsion = _torsion_detailing(beam)
        _ensure_torsion_reinforcement(beam, torsion)
        design = beam.design_result
        rebar_types = {str(item.bar_type) for item in (beam.reinforcement or [])}
        missing_types = sorted(_REQUIRED_REBAR_TYPES - rebar_types)
        spatial_complete = getattr(beam, "spatial_analysis_status", "missing") in {"calculated", "verified"}
        planar_moment = abs(float(getattr(beam, "design_moment", 0.0) or 0.0))
        eccentric_in_plane_moment = abs(float(getattr(beam, "design_eccentric_in_plane_moment", 0.0) or 0.0))
        combined_in_plane_moment = planar_moment + eccentric_in_plane_moment
        moment_capacity = float(getattr(design, "moment_capacity", 0.0) or 0.0) if design is not None else 0.0
        in_plane_interaction = combined_in_plane_moment / max(moment_capacity, 1.0e-9) if moment_capacity > 0.0 else None
        in_plane_status = "pass" if in_plane_interaction is not None and in_plane_interaction <= 1.0 else "fail"
        passed = bool(
            design is not None
            and getattr(design, "check_status", "fail") != "fail"
            and getattr(beam, "analysis_status", "missing") in {"calculated", "verified"}
            and spatial_complete
            and torsion["status"] == "pass"
            and in_plane_status == "pass"
            and not missing_types
        )
        beam_rows.append({
            "beamId": beam.id,
            "beamCode": beam.code,
            "beamRole": beam.beam_role,
            "analysisStatus": getattr(beam, "analysis_status", "missing"),
            "spatialAnalysisStatus": getattr(beam, "spatial_analysis_status", "missing"),
            "axialForceKn": beam.design_axial_force,
            "momentKnm": beam.design_moment,
            "eccentricInPlaneMomentKnm": beam.design_eccentric_in_plane_moment,
            "combinedInPlaneMomentKnm": round(combined_in_plane_moment, 3),
            "momentCapacityKnm": round(moment_capacity, 3) if moment_capacity > 0.0 else None,
            "inPlaneMomentInteraction": round(in_plane_interaction, 4) if in_plane_interaction is not None else None,
            "inPlaneMomentStatus": in_plane_status,
            "shearKn": beam.design_shear,
            "torsionKnm": beam.design_torsion,
            "outOfPlaneMomentKnm": beam.design_out_of_plane_moment,
            "designStatus": getattr(design, "check_status", "missing") if design else "missing",
            "reinforcementGroupCount": len(beam.reinforcement or []),
            "missingReinforcementTypes": missing_types,
            "torsionDetailing": torsion,
            "passed": passed,
        })

    ring_nodes = [node for node in (system.support_nodes or []) if str(node.node_type) == "ring_strut_to_ring"]
    node_rows = []
    for node in ring_nodes:
        spatial_detailing = _node_detailing(project, node)
        passed = bool(node.bearing_plate is not None and node.reinforcement and node.check_status != "fail" and spatial_detailing["status"] == "pass")
        node_rows.append({
            "nodeId": node.id,
            "nodeCode": node.code,
            "supportCode": node.support_code,
            "status": node.check_status,
            "bearingPlateDefined": node.bearing_plate is not None,
            "reinforcementGroupCount": len(node.reinforcement or []),
            "spatialDetailing": spatial_detailing,
            "passed": passed,
        })

    readiness = dict(audit.get("readiness") or {})
    frame_status = "pass" if beams and all(row["passed"] for row in beam_rows) else "fail"
    node_status = "pass" if ring_nodes and all(row["passed"] for row in node_rows) else "fail"
    stage_status = "pass" if bool(readiness.get("constructionStageClosed")) else "fail"
    reaction_iteration = dict((project.advanced_engineering or {}).get("wallWaleTransferReactionIteration") or {})
    iteration_status = "pass" if reaction_iteration.get("converged") else "fail"
    status = "pass" if frame_status == node_status == stage_status == iteration_status == "pass" else "fail"
    package = {
        "schema": "pitguard-concave-transfer-detailing-v2",
        "version": SOFTWARE_VERSION,
        "required": True,
        "status": status,
        "supportTopologyHash": support_topology_hash(project),
        "transferTemplateId": audit.get("templateId"),
        "transferTopologyClass": audit.get("topologyClass"),
        "evidence": {
            "frameAnalysisStatus": frame_status,
            "nodeDetailingStatus": node_status,
            "stageReviewStatus": stage_status,
            "reactionIterationStatus": iteration_status,
            "spatialEffectStatus": "pass" if beams and all(row["spatialAnalysisStatus"] in {"calculated", "verified"} for row in beam_rows) else "fail",
            "torsionDetailingStatus": "pass" if beams and all(row["torsionDetailing"]["status"] == "pass" for row in beam_rows) else "fail",
        },
        "metrics": {
            "transferBeamCount": len(beams),
            "designedTransferBeamCount": sum(row["passed"] for row in beam_rows),
            "ringNodeCount": len(ring_nodes),
            "detailedRingNodeCount": sum(row["passed"] for row in node_rows),
            "maximumFrameDisplacementM": (audit.get("frameAnalysis") or {}).get("maximumDisplacementM"),
            "maximumFrameResidual": (audit.get("frameAnalysis") or {}).get("maximumRelativeResidual"),
            "maximumTorsionKnm": max((float(row.get("torsionKnm") or 0.0) for row in beam_rows), default=0.0),
            "maximumEccentricInPlaneMomentKnm": max((float(row.get("eccentricInPlaneMomentKnm") or 0.0) for row in beam_rows), default=0.0),
            "maximumCombinedInPlaneMomentKnm": max((float(row.get("combinedInPlaneMomentKnm") or 0.0) for row in beam_rows), default=0.0),
            "maximumInPlaneMomentInteraction": max((float(row.get("inPlaneMomentInteraction") or 0.0) for row in beam_rows), default=0.0),
            "maximumBearingUtilization": max((float((row.get("spatialDetailing") or {}).get("bearingUtilization") or 0.0) for row in node_rows), default=0.0),
            "haunchRequiredNodeCount": sum(bool((row.get("spatialDetailing") or {}).get("haunch", {}).get("required")) for row in node_rows),
        },
        "beamSchedule": beam_rows,
        "nodeSchedule": node_rows,
        "summary": (
            "异形转接梁系已形成平面内力、三维偏心/扭转、反力迭代、抗扭筋、局部承压、加腋和锚固自动深化证据；正式发行仍需注册结构工程师核验并签署。"
            if status == "pass"
            else "异形转接梁系深化仍有缺项；请检查反力迭代、空间效应、抗扭配筋、局部承压、加腋、锚固或施工阶段闭合状态。"
        ),
        "manualReviewBoundary": [
            "节点级空间子模型已考虑偏心、扭转、刚域和半刚性分配；整体 6-DOF 空间杆系/有限元仍需基准复核。",
            "立柱与永久柱网、坡道、栈桥及出土通道的冲突需项目级复核。",
            "抗扭筋、局部承压、加腋、锚固和施工缝必须由注册结构工程师审签。",
        ],
    }
    advanced = dict(project.advanced_engineering or {})
    advanced["concaveTransferAutoDetailing"] = package
    project.advanced_engineering = advanced
    return package
