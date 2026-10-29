"""Re-evaluate response-dependent checks against the final stage solutions."""
from __future__ import annotations

import math
from typing import Any

from app.calculation.final_envelope import build_final_wall_envelopes
from app.rules.gb50010.rc_section_rules import rectangular_flexural_capacity_knm_per_m, check_rectangular_shear_capacity, rebar_fy
from app.rules.gb50010.detailing_rules import check_crack_width
from app.rules.jgj120_2012.stability_rules import check_wall_deformation


def check_dict(check) -> dict[str, Any]:
    return check.model_dump(mode="json", by_alias=True)


def wall_deformation_check(object_id: str, depth: float, displacement: float | None, environment_grade: str) -> dict[str, Any]:
    if depth <= 0 or displacement is None or not math.isfinite(float(displacement)):
        return {"ruleId": "JGJ120-WALL-DEFORMATION-SUBSET", "objectId": object_id,
            "objectType": "DiaphragmWallPanel", "status": "manual_review", "calculatedValue": None,
            "limitValue": None, "unit": "mm", "applicability": "not_applicable" if depth <= 0 else "applicable",
            "message": "初始未开挖状态不套用开挖变形限值。" if depth <= 0 else "位移求解不可用，未按零位移判定通过。",
            "clauseReference": "deformation check requires a solved standard-load displacement"}
    return check_dict(check_wall_deformation(object_id, depth, displacement, environment_grade))


def supplied_wall_rebar(wall) -> tuple[float | None, Any | None]:
    bars = [g for g in wall.reinforcement if g.bar_type == "longitudinal"]
    candidates = []
    for group in bars:
        if group.spacing and group.spacing > 0 and group.diameter > 0:
            candidates.append((math.pi * group.diameter**2 / 4 * 1000 / group.spacing, group))
    # Different zones may use different steel grades: minimum area alone is
    # not the governing flexural capacity.
    return min(candidates, key=lambda item: rectangular_flexural_capacity_knm_per_m(
        item[0], wall.thickness, wall.concrete_grade, item[1].grade)) if candidates else (None, None)


def supplied_beam_checks(beam, design, supplied_groups) -> list[dict[str, Any]]:
    """Check specified bars without treating newly suggested steel as adopted.

    A bar count without its tension-face allocation is not enough to infer a
    flexural capacity. Spacing records use the least furnished face/zone.
    """
    width, height = beam.section.width, beam.section.height
    longitudinal = [(math.pi*g.diameter**2/4*1000/g.spacing, g)
        for g in supplied_groups if g.bar_type == "longitudinal" and g.spacing and g.spacing > 0 and g.diameter > 0]
    capacity = None
    shear_capacity = None
    if width and height:
        if longitudinal:
            area, group = min(longitudinal, key=lambda pair: rectangular_flexural_capacity_knm_per_m(
                pair[0], height, beam.material.grade, pair[1].grade))
            capacity = rectangular_flexural_capacity_knm_per_m(area, height, beam.material.grade, group.grade) * width
        concrete_capacity = check_rectangular_shear_capacity(0.0, height, beam.material.grade)["concreteShearCapacity"] * width
        stirrup_capacities = [rebar_fy(g.grade) * (math.pi*g.diameter**2/4*g.stirrup_legs)
            * max(height*1000-70, .65*height*1000)/g.spacing/1000
            for g in supplied_groups if g.bar_type == "stirrup" and g.spacing and g.spacing > 0
            and g.diameter > 0 and g.stirrup_legs and g.stirrup_legs >= 2]
        # Missing stirrup evidence is not a fictitious generated contribution.
        shear_capacity = concrete_capacity + min(stirrup_capacities) if stirrup_capacities else None
    rows = []
    for suffix, demand, limit, unit in [
        ("FLEXURE", design.max_moment_design, capacity, "kN*m"),
        ("SHEAR", design.max_shear_design, shear_capacity, "kN"),
    ]:
        rows.append({"ruleId": f"GB50010-WALE-{suffix}-SUBSET", "objectId": beam.id,
            "objectType": "BeamElement", "status": "manual_review" if limit is None else "pass" if demand <= limit else "fail",
            "calculatedValue": demand, "limitValue": limit, "unit": unit, "evidenceScope": "supplied_reinforcement",
            "message": "输入配筋在最小配置区的截面筛查；建议配筋未替换输入。" if limit is not None else "输入钢筋的受力面/间距或箍筋肢数信息不足，承载力未计算；建议配筋未采用。",
            "clauseReference": "GB/T 50010 rectangular beam subset; supplied reinforcement only"})
    return rows


def refresh_final_stage_checks(project, case, stage_results, stage_states) -> dict[str, dict[str, Any]]:
    walls = {wall.segment_id: wall for wall in project.retaining_system.diaphragm_walls}
    for row in stage_results:
        wall = walls[row.segment_id]
        state = stage_states[row.stage_id]
        force = row.wall_internal_force
        # Numerical and response-dependent checks are rebuilt after coupling.
        row.checks = [c for c in row.checks if not any(token in str(c.get("ruleId", ""))
            for token in ["DEFORMATION", "FLEXURE", "SHEAR-SUBSET", "CRACK", "NUMERICAL-EQUILIBRIUM", "MATRIX-CONDITION"])]
        row.checks.append(wall_deformation_check(wall.id, state["excavationDepthM"],
            force.max_displacement if force else None, project.design_settings.environment_grade))
        wall_solves = (row.coupled_system_result.get("solveEvidence") or {}).get("wallSolvers") or {}
        for basis in ("standard", "design"):
            evidence = dict(wall_solves.get(basis) or {})
            row.checks.append({"ruleId": "PITGUARD-WALL-SOLVER-EVIDENCE", "objectId": wall.id,
                "objectType": "DiaphragmWallPanel", "status": "pass" if evidence.get("status") == "solved" else "fail",
                "calculatedValue": None, "limitValue": None, "loadBasis": basis,
                "message": "墙体求解来源已记录；简化回退不得作为设计作用解。",
                "clauseReference": "independent standard/design wall solver availability", "diagnostics": evidence})
        for basis, coupled in [("standard", row.global_coupled_result), ("design", row.design_global_coupled_result)]:
            diagnostics = dict(coupled.equilibrium_diagnostics or {}) if coupled else {}
            residual = diagnostics.get("relativeResidual")
            limit = float(project.design_settings.maximum_equilibrium_relative_residual)
            finite = isinstance(residual, (int, float)) and math.isfinite(residual)
            status = "manual_review" if not finite else "pass" if residual <= limit else "fail"
            if coupled and (coupled.fallback or coupled.ill_conditioned_blocked):
                status = "fail"
            row.checks.append({"ruleId": "PITGUARD-NUMERICAL-EQUILIBRIUM", "objectId": row.segment_id,
                "objectType": "GlobalCoupledSystem", "status": status, "calculatedValue": residual,
                "limitValue": limit, "unit": "relative residual", "loadBasis": basis,
                "message": "最终求解向量的方程残差；模型适用性独立审查。", "diagnostics": diagnostics,
                "clauseReference": "numerical equation residual, not a structural-code safety check"})
            condition = coupled.scaled_condition_number if coupled else None
            condition_limit = float(project.design_settings.maximum_matrix_condition_number)
            row.checks.append({"ruleId": "PITGUARD-MATRIX-CONDITION", "objectId": row.segment_id,
                "objectType": "GlobalCoupledSystem", "status": "manual_review" if condition is None else "pass" if condition <= condition_limit else "fail",
                "calculatedValue": condition, "limitValue": condition_limit, "unit": "dimensionless", "loadBasis": basis,
                "message": "最终缩放矩阵条件数；原始与缩放条件数保留于求解记录。",
                "clauseReference": "numerical conditioning gate"})
        if force:
            area, bar = supplied_wall_rebar(wall)
            capacity = rectangular_flexural_capacity_knm_per_m(area, wall.thickness, wall.concrete_grade, bar.grade) if area is not None else None
            md, vd = force.max_moment_design, force.max_shear_design
            row.checks.append({"ruleId": "GB50010-FLEXURE-SUBSET", "objectId": wall.id,
                "objectType": "DiaphragmWallPanel", "status": "manual_review" if capacity is None or md is None else "pass" if md <= capacity else "fail",
                "calculatedValue": md, "limitValue": capacity, "unit": "kN*m/m", "evidenceScope": "supplied_reinforcement",
                "message": "按输入纵筋各面/各区最小截面承载力筛查，计入钢筋级别差异；需求配筋建议另存，未替换输入钢筋。" if area is not None else "缺少可识别的输入纵筋直径和间距，承载力未计算；建议配筋不能代替现状验算。",
                "clauseReference": "GB/T 50010 rectangular-section subset; face and anchorage applicability requires review",
                "providedAs": area, "solveId": row.solve_id})
            if vd is not None:
                shear = check_rectangular_shear_capacity(vd, wall.thickness, wall.concrete_grade)
                row.checks.append({"ruleId": "GB50010-SHEAR-SUBSET", "objectId": wall.id,
                    "objectType": "DiaphragmWallPanel", "status": shear["status"],
                    "calculatedValue": vd, "limitValue": shear["concreteShearCapacity"], "unit": "kN/m",
                    "message": "最终设计剪力与给定截面混凝土抗剪子集比较；未计入未确认横向钢筋贡献。",
                    "clauseReference": "GB/T 50010 concrete shear subset", "solveId": row.solve_id})
            if capacity is not None and bar:
                crack = check_dict(check_crack_width(wall.id, force.max_moment, capacity, bar.spacing,
                    bar.diameter, project.design_settings.environment_grade, bar.grade))
                crack.update(loadBasis="standard", evidenceScope="supplied_reinforcement")
                row.checks.append(crack)
        row.checks.append({"ruleId": "PITGUARD-STAGE-LOAD-CONTRACT", "objectId": wall.id,
            "objectType": "StageLoad", "status": "pass" if row.load_evidence else "manual_review",
            "calculatedValue": None, "limitValue": None, "unit": "kPa", "message": "标准/设计压力记录与消费者一致；具体组合公式及适用范围见荷载记录。",
            "clauseReference": "explicit action-level combination; not a normative validation certificate",
            "loadRecordId": row.load_evidence.get("load_id"), "solveId": row.solve_id})
        if state["assumptions"]:
            row.checks.append({"ruleId": "PITGUARD-STAGE-STATE-ASSUMPTIONS", "objectId": row.stage_id,
                "objectType": "ConstructionStage", "status": "manual_review", "calculatedValue": None, "limitValue": None,
                "unit": "-", "message": "；".join(state["assumptions"]), "clauseReference": "designer confirmation of stage boundary state"})
        row.checks.append({"ruleId": "PITGUARD-STAGE-HISTORY-LIMITATION", "objectId": row.stage_id,
            "objectType": "ConstructionStage", "status": "manual_review", "calculatedValue": None, "limitValue": None,
            "unit": "-", "message": "当前为独立线弹性阶段状态，未传递土体塑性历史、安装位移及预应力初始状态。",
            "clauseReference": "staged model applicability boundary", "historyTransfer": "not_implemented"})
        for check in row.checks:
            check.update(stageId=row.stage_id, segmentId=row.segment_id)
        row.stability_checks = [c for c in row.checks if str(c.get("ruleId", "")).startswith("JGJ120")]
        row.rc_checks = [c for c in row.checks if str(c.get("ruleId", "")).startswith(("GB50010", "GBT50010"))]
        row.coupled_system_result.update(wallMaxMoment=force.max_moment if force else None,
            wallMaxDisplacement=force.max_displacement if force else None,
            finalSolveId=row.solve_id, finalResponse=True)
        coupled = row.global_coupled_result
        if coupled:
            row.coupled_system_result.update({
                "globalMatrixSize": coupled.matrix_size, "globalDofSummary": coupled.dof_summary,
                "globalMaxWallDisplacement": coupled.max_wall_displacement,
                "globalMaxSupportAxialForce": coupled.max_support_axial_force,
                "globalEquilibriumDiagnostics": coupled.equilibrium_diagnostics,
                "fallback": coupled.fallback, "globalSpatialMatrixSize": coupled.spatial_matrix_size,
                "globalSpatialDofSummary": coupled.spatial_dof_summary,
                "wallRotationNodeCount": len(coupled.wall_rotation_profile),
                "waleRotationNodeCount": len(coupled.wale_node_profile),
                "columnVerticalDofCount": len(coupled.column_vertical_dofs),
                "slabReplacementStiffness": coupled.slab_replacement_stiffness,
                "slabReplacementStatus": coupled.slab_replacement_status,
                "slabReplacementSource": coupled.slab_replacement_source,
                "slabReplacementRequired": coupled.slab_replacement_required,
                "slabReplacementComponents": coupled.slab_replacement_components,
            })
        row.coupled_system_result.update(waleResultCount=len(row.wale_beam_results),
            maxWaleMoment=max((abs(w.max_moment) for w in row.wale_beam_results), default=None),
            maxWaleShear=max((abs(w.max_shear) for w in row.wale_beam_results), default=None))
        if force:
            row.wall_internal_force_placeholder.update(status="calculated" if force.max_displacement is not None and force.max_moment_design is not None and force.max_shear_design is not None else "partial",
                solveId=row.solve_id, maxMoment=force.max_moment, maxShear=force.max_shear,
                maxDisplacement=force.max_displacement, maxMomentDesign=force.max_moment_design,
                maxShearDesign=force.max_shear_design)
    return build_final_wall_envelopes(stage_results)
