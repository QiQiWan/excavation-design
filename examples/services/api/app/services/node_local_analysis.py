from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.rules.gb50010.rc_section_rules import concrete_fc
from app.schemas.domain import Project


def _support_map(project: Project):
    return {s.id: s for s in project.retaining_system.supports} if project.retaining_system else {}


def _node_confinement_area_m2(node) -> float:
    area_mm2 = 0.0
    for group in node.reinforcement:
        if group.bar_type not in {"stirrup", "tie", "additional"}:
            continue
        count = max(int(group.count or 2), 1)
        area_mm2 += count * math.pi * float(group.diameter) ** 2 / 4.0
    return max(area_mm2 * 1e-6, 1.5e-4 if node.reinforcement else 5e-5)


def evaluate_node_local_response(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    if not ret:
        return {"status": "fail", "summary": {"message": "缺少围护结构"}, "nodes": []}
    support_by_id = _support_map(project)
    rows = []
    counts = {"pass": 0, "warning": 0, "fail": 0, "manual_review": 0}
    nonlinear_fe_count = 0
    for node in ret.support_nodes:
        support = support_by_id.get(node.support_id)
        if not support:
            continue
        force = abs(float(support.design_axial_force or support.effective_axial_force_standard or 0.0))
        width = float(support.section.width or support.section.diameter or 0.8)
        height = float(support.section.height or support.section.diameter or width)
        characteristic = max(width, height, 0.5)
        length = max(float(support.span_length or math.hypot(support.end.x-support.start.x, support.end.y-support.start.y)), 1.0)
        e_mod = float(support.material.elastic_modulus or (31.5e6 if support.section_type == "rc_rectangular" else 2.0e8))
        area = max(width * height, 0.05)
        k_axial = e_mod * area / length
        plate = node.bearing_plate
        plate_area = float(plate.bearing_area if plate else width * height)
        fc = concrete_fc(support.material.grade) * 1000.0
        k_bearing = max(fc * plate_area * 80.0, 1e3)
        k_wale = max(k_axial * 0.35, 1e3)
        tie_area = _node_confinement_area_m2(node)
        k_tie = max(2.0e8 * tie_area / characteristic, 1e3)
        eccentricity = abs(float(support.eccentricity_moment or 0.0)) / max(force, 1e-9)
        eccentricity = min(eccentricity, max(width, height))
        coupling = eccentricity / characteristic
        k_rotation_scaled = max(k_wale / 12.0 + k_tie / 8.0, 1e3)
        # Condensed three-DOF local model. The third generalized displacement is theta*Lc,
        # which keeps the stiffness matrix numerically scaled in translational units.
        k = np.array([
            [k_axial + k_bearing, -k_bearing, k_bearing * coupling],
            [-k_bearing, k_bearing + k_wale + k_tie, -k_bearing * coupling],
            [k_bearing * coupling, -k_bearing * coupling, k_rotation_scaled + k_bearing * coupling**2],
        ], dtype=float)
        f = np.array([force, 0.0, force * eccentricity / characteristic], dtype=float)
        try:
            u = np.linalg.solve(k, f)
            eigenvalues = np.linalg.eigvalsh((k + k.T) / 2.0)
            cond = float(np.linalg.cond(k))
        except np.linalg.LinAlgError:
            u = np.zeros(3); eigenvalues = np.array([-1.0]); cond = float("inf")
        min_eigen = float(np.min(eigenvalues))
        max_eigen = max(float(np.max(np.abs(eigenvalues))), 1e-9)
        stability_ratio = min_eigen / max_eigen
        slip = float(u[0] - u[1] + coupling * u[2])
        displacement_mm = abs(slip) * 1000.0
        rotation_mrad = abs(float(u[2]) / characteristic) * 1000.0
        bearing_reaction = abs(k_bearing * slip)
        wale_reaction = abs(k_wale * float(u[1]))
        tie_reaction = abs(k_tie * float(u[1]))
        reaction_sum = max(bearing_reaction + wale_reaction + tie_reaction, 1e-9)
        bearing_stress = force / max(plate_area, 1e-9)
        bearing_capacity = float(plate.bearing_capacity if plate and plate.bearing_capacity else 0.8 * fc)
        bearing_util = bearing_stress / max(bearing_capacity, 1e-9)
        confinement_factor = 1.10 if node.reinforcement else 1.0
        splitting_capacity = 0.45 * fc * area * confinement_factor
        splitting_util = force / max(splitting_capacity, 1e-9)
        eccentric_util = eccentricity / max(width / 6.0, 1e-6)
        max_util = max(bearing_util, splitting_util, eccentric_util)
        unstable = min_eigen <= 0.0 or stability_ratio < 1e-8
        requires_nonlinear_fe = max_util > 0.85 or displacement_mm > 2.0 or rotation_mrad > 2.0 or unstable
        if requires_nonlinear_fe:
            nonlinear_fe_count += 1
        status = "fail" if unstable or max_util > 1.05 or displacement_mm > 5.0 or rotation_mrad > 5.0 else "warning" if requires_nonlinear_fe or cond > 1e8 else "pass"
        counts[status] += 1
        rows.append({
            "nodeId": node.id, "nodeCode": node.code, "supportCode": support.code, "levelIndex": node.level_index,
            "designForceKn": round(force, 2), "bearingUtilization": round(bearing_util, 3), "splittingUtilization": round(splitting_util, 3),
            "eccentricityUtilization": round(eccentric_util, 3), "localSlipMm": round(displacement_mm, 3),
            "localRotationMrad": round(rotation_mrad, 3), "stiffnessConditionNumber": round(cond, 2),
            "stabilityEigenRatio": round(stability_ratio, 10), "status": status, "governingUtilization": round(max_util, 3),
            "loadPathShare": {
                "bearing": round(bearing_reaction / reaction_sum, 3),
                "wale": round(wale_reaction / reaction_sum, 3),
                "confinement": round(tie_reaction / reaction_sum, 3),
            },
            "requiresNonlinearFE": requires_nonlinear_fe,
            "recommendedAction": "建立节点实体/壳-接触专项模型，并增大承压板、核心区或抗劈裂约束" if requires_nonlinear_fe else "按节点大样和施工偏差要求实施",
            "drawingRef": "D-08" if support.support_role == "secondary_strut" else "D-01",
        })
    overall = "fail" if counts["fail"] else "warning" if counts["warning"] or counts["manual_review"] else "pass"
    return {
        "status": overall,
        "summary": {
            "nodeCount": len(rows), "counts": counts,
            "maxUtilization": max((r["governingUtilization"] for r in rows), default=0.0),
            "maxLocalSlipMm": max((r["localSlipMm"] for r in rows), default=0.0),
            "maxLocalRotationMrad": max((r["localRotationMrad"] for r in rows), default=0.0),
            "nonlinearFERequiredCount": nonlinear_fe_count,
            "minimumStabilityEigenRatio": min((r["stabilityEigenRatio"] for r in rows), default=0.0),
        },
        "nodes": rows,
        "method": "three-degree condensed local stiffness and strut-and-tie screening with bearing, confinement, eccentric rotation, eigen-stability and load-path decomposition",
        "boundary": "该模型用于节点筛查和大样设计分级。高利用率、显著滑移/转角或低特征值节点必须采用实体/壳有限元、接触、预埋件和施工偏差模型专项复核。",
    }
