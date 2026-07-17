from __future__ import annotations

from typing import Any

from app.schemas.domain import Project


def build_local_node_submodel_checks(project: Project) -> list[dict[str, Any]]:
    """Transparent analytical screening of support-to-wale load transfer.

    This is intentionally an engineering submodel (bearing + spreading + bursting proxy),
    not a shell/solid finite-element claim. It creates a reviewable numerical gate and
    preserves the need for local FEM/detailing when utilization is high.
    """
    system = project.retaining_system
    if not system:
        return []
    support_by_id = {row.id: row for row in system.supports}
    checks: list[dict[str, Any]] = []
    for node in system.support_nodes or []:
        support = support_by_id.get(node.support_id)
        axial = float(getattr(support, "design_axial_force", 0.0) or 0.0)
        plate = node.bearing_plate
        area_m2 = float(getattr(plate, "bearing_area", 0.0) or 0.0) if plate else 0.0
        stress_mpa = axial / max(area_m2 * 1000.0, 1e-9) if area_m2 > 0.0 else None
        capacity_mpa = float(getattr(plate, "bearing_capacity", 0.0) or 0.0) if plate else 0.0
        if capacity_mpa <= 0.0:
            capacity_mpa = 0.85 * 16.7  # conservative C35-scale screening only
        utilization = stress_mpa / capacity_mpa if stress_mpa is not None else None
        bursting_force = 0.18 * axial
        status = "manual_review" if utilization is None else "fail" if utilization > 1.0 else "warning" if utilization > 0.85 else "pass"
        checks.append({
            "ruleId": "PITGUARD-LOCAL-NODE-ANALYTICAL-SUBMODEL",
            "objectId": node.id,
            "objectType": "SupportWaleNode",
            "status": status,
            "calculatedValue": round(utilization, 4) if utilization is not None else None,
            "limitValue": 1.0,
            "unit": "utilization",
            "message": (
                f"节点局部承压利用率 {utilization:.3f}；节点横向劈裂力代理值 {bursting_force:.1f} kN。"
                if utilization is not None else "缺少承压板面积，无法完成节点局部承压子模型。"
            ),
            "clauseReference": "GB/T 50010 局部受压与节点构造；GB 50017 连接与局部稳定；项目专项节点设计",
            "formula": "sigma=N/A_plate; eta=sigma/f_bearing; T_bursting=0.18N (screening)",
            "evidenceLevel": "analytical_local_submodel",
            "implementationState": "screening_implemented",
            "missingInputs": [] if area_m2 > 0.0 else ["bearingPlate.bearingArea"],
            "details": {
                "supportCode": getattr(support, "code", None),
                "nodeCode": node.code,
                "axialForceKn": round(axial, 3),
                "plateAreaM2": round(area_m2, 6) if area_m2 else None,
                "bearingStressMpa": round(stress_mpa, 4) if stress_mpa is not None else None,
                "bearingCapacityMpa": round(capacity_mpa, 4),
                "burstingForceProxyKn": round(bursting_force, 3),
                "boundary": "高利用率节点仍需壳/实体局部模型或经验证的企业节点详图复核。",
            },
        })
    return checks
