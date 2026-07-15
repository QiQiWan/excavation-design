from __future__ import annotations

"""Preliminary deep-design screening for horizontal excavation bracing.

The module deliberately separates three levels of evidence:

1. topology/constructability checks supplied by ``support_layout_quality``;
2. fast member and construction-effect screening used during candidate ranking;
3. the full staged wall-wale-support analysis performed by ``calculation.engine``.

The formulas here are transparent preliminary models.  They are not a replacement
for the project calculation book, connection design, or an independently checked
finite-element/frame model.
"""

import math
from collections import defaultdict
from typing import Any

from app.schemas.domain import Project, RetainingSystem, SupportElement

EPS = 1.0e-9
STEEL_E_KN_M2 = 2.06e8
CONCRETE_E_KN_M2 = {
    "C30": 3.00e7,
    "C35": 3.15e7,
    "C40": 3.25e7,
    "C45": 3.35e7,
    "C50": 3.45e7,
}
CONCRETE_FC_MPA = {"C30": 14.3, "C35": 16.7, "C40": 19.1, "C45": 21.1, "C50": 23.1}
STEEL_FY_MPA = {"Q235": 215.0, "Q345": 305.0, "Q355": 305.0, "Q390": 335.0, "Q420": 355.0}


def _length(support: SupportElement) -> float:
    return max(float(support.span_length or math.hypot(support.end.x - support.start.x, support.end.y - support.start.y)), 0.1)


def _section_properties(support: SupportElement) -> dict[str, float]:
    sec = support.section
    if support.section_type == "steel_pipe":
        d = max(float(sec.diameter or 0.609), 0.05)
        t = max(min(float(sec.wall_thickness or 0.016), 0.45 * d), 0.002)
        di = max(d - 2.0 * t, 0.001)
        area = math.pi * (d * d - di * di) / 4.0
        inertia = math.pi * (d**4 - di**4) / 64.0
        depth = d
    elif support.section_type == "h_steel":
        # A compact preliminary equivalent when flange/web dimensions have not
        # been entered.  Detailed steel design must use the actual profile table.
        b = max(float(sec.width or 0.4), 0.1)
        h = max(float(sec.height or 0.4), 0.1)
        area = 0.22 * b * h
        inertia = 0.10 * b * h**3
        depth = h
    else:
        b = max(float(sec.width or 0.8), 0.2)
        h = max(float(sec.height or 0.8), 0.2)
        area = b * h
        inertia = b * h**3 / 12.0
        depth = h
    radius = math.sqrt(max(inertia / max(area, EPS), EPS))
    return {"areaM2": area, "inertiaM4": inertia, "radiusM": radius, "depthM": depth}


def _material_properties(support: SupportElement) -> dict[str, float]:
    grade = str(support.material.grade or "").upper()
    if support.section_type in {"steel_pipe", "h_steel"} or grade.startswith("Q"):
        return {
            "elasticModulusKnM2": float(support.material.elastic_modulus or STEEL_E_KN_M2),
            "designStrengthMpa": STEEL_FY_MPA.get(grade, 305.0),
            "thermalExpansion": 1.2e-5,
            "materialDensityKnM3": 78.5,
        }
    concrete_grade = grade if grade in CONCRETE_E_KN_M2 else "C35"
    return {
        "elasticModulusKnM2": float(support.material.elastic_modulus or CONCRETE_E_KN_M2[concrete_grade]),
        "designStrengthMpa": CONCRETE_FC_MPA[concrete_grade],
        "thermalExpansion": 1.0e-5,
        "materialDensityKnM3": 25.0,
    }


def _effective_unbraced_length(system: RetainingSystem, support: SupportElement) -> float:
    length = _length(support)
    stations = [0.0, length]
    dx = support.end.x - support.start.x
    dy = support.end.y - support.start.y
    for column in system.columns or []:
        if support.code not in (column.support_codes or []):
            continue
        station = ((column.location.x - support.start.x) * dx + (column.location.y - support.start.y) * dy) / length
        if -0.25 <= station <= length + 0.25:
            stations.append(max(0.0, min(length, station)))
    ordered = sorted(set(round(v, 4) for v in stations))
    return max((b - a for a, b in zip(ordered[:-1], ordered[1:])), default=length)


def _force_envelope_from_stages(stage_results: list[Any] | None) -> dict[str, float]:
    forces: dict[str, float] = {}
    for stage in stage_results or []:
        for item in getattr(stage, "support_forces", None) or []:
            support_id = getattr(item, "support_id", None)
            if not support_id:
                continue
            value = abs(float(
                getattr(item, "axial_force_design", None)
                or getattr(item, "effective_axial_force", None)
                or getattr(item, "axial_force", None)
                or 0.0
            ))
            forces[str(support_id)] = max(forces.get(str(support_id), 0.0), value)
    return forces


def _force_evidence(
    project: Project,
    *,
    calculation_result: Any | None = None,
    stage_results_override: list[Any] | None = None,
    calculation_current_override: bool | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    if stage_results_override is not None:
        forces = _force_envelope_from_stages(stage_results_override)
        return forces, {
            "source": "current_calculation_run",
            "current": True if calculation_current_override is None else bool(calculation_current_override),
            "resultId": getattr(calculation_result, "id", None),
            "supportForceCount": len(forces),
            "staleCalculationIgnored": False,
            "contractVerification": {"current": True, "reason": "current calculation stage results supplied directly"},
            "assurance": dict(getattr(calculation_result, "calculation_assurance", None) or {}),
        }

    latest = calculation_result or (project.calculation_results[-1] if project.calculation_results else None)
    if latest is None:
        return {}, {
            "source": "none",
            "current": False,
            "resultId": None,
            "supportForceCount": 0,
            "staleCalculationIgnored": False,
            "contractVerification": {"current": False, "reason": "missing calculation result"},
            "assurance": {},
        }
    if calculation_current_override is None:
        from app.services.calculation_assurance import verify_current_calculation_contract
        verification = verify_current_calculation_contract(project, latest)
        current = bool(verification.get("current"))
    else:
        current = bool(calculation_current_override)
        verification = {"current": current, "reason": "caller supplied calculation currency"}
    forces = _force_envelope_from_stages(getattr(latest, "stage_results", None)) if current else {}
    return forces, {
        "source": "latest_current_calculation" if current else "stale_calculation_ignored",
        "current": current,
        "resultId": getattr(latest, "id", None),
        "supportForceCount": len(forces),
        "staleCalculationIgnored": not current,
        "contractVerification": verification,
        "assurance": dict(getattr(latest, "calculation_assurance", None) or {}),
    }


def _geotechnical_evidence(project: Project) -> dict[str, Any]:
    strata = list(project.strata or [])
    required = ("unit_weight", "friction_angle")
    complete = 0
    low_confidence = 0
    empirical_or_manual = 0
    for stratum in strata:
        params = stratum.parameters
        if all(getattr(params, key, None) is not None for key in required):
            complete += 1
        if str(stratum.confidence) == "low":
            low_confidence += 1
        if str(stratum.parameter_source) in {"empirical", "manual"}:
            empirical_or_manual += 1
    coverage = dict(getattr(project.geological_model, "coverage_audit", None) or {}) if project.geological_model else {}
    coverage_pass = bool(coverage.get("pass", coverage.get("covered", bool(project.geological_model))))
    return {
        "stratumCount": len(strata),
        "boreholeCount": len(project.boreholes or []),
        "requiredParameterCompleteCount": complete,
        "requiredParameterCompletenessRatio": round(complete / len(strata), 4) if strata else 0.0,
        "lowConfidenceStratumCount": low_confidence,
        "empiricalOrManualStratumCount": empirical_or_manual,
        "geologicalModelAvailable": bool(project.geological_model),
        "coverageAuditAvailable": bool(coverage),
        "coveragePass": coverage_pass,
    }


def _average_soil_parameters(project: Project) -> tuple[float, float]:
    gammas: list[float] = []
    phis: list[float] = []
    for borehole in project.boreholes or []:
        for layer in borehole.layers or []:
            # Borehole layers carry geometry; material parameters are normally in
            # the project geological sections. Keep conservative defaults when a
            # unified parameter table is not available in the workspace object.
            _ = layer
    for segment in (project.excavation.segments if project.excavation else []):
        section = segment.representative_section
        for layer in (section.layers if section else []):
            p = layer.parameters
            if p.unit_weight is not None:
                gammas.append(float(p.unit_weight))
            if p.friction_angle is not None:
                phis.append(float(p.friction_angle))
    return (sum(gammas) / len(gammas) if gammas else 18.0, sum(phis) / len(phis) if phis else 25.0)


def _screening_demand(project: Project, support: SupportElement, actual: dict[str, float], level_count: int) -> dict[str, float | str]:
    if support.id in actual and actual[support.id] > 0.0:
        base = actual[support.id]
        source = "staged_calculation_envelope"
    elif support.design_axial_force and support.design_axial_force > 0.0:
        base = float(support.design_axial_force)
        source = "stored_design_axial_force"
    else:
        depth = max(float(project.excavation.depth if project.excavation else 10.0), 1.0)
        gamma, phi_deg = _average_soil_parameters(project)
        phi = math.radians(max(5.0, min(phi_deg, 45.0)))
        ka = math.tan(math.pi / 4.0 - phi / 2.0) ** 2
        surcharge = max(float(project.design_settings.surcharge or 0.0), 0.0)
        average_pressure = ka * (surcharge + 0.5 * gamma * depth)
        vertical_tributary = depth / max(level_count, 1)
        plan_tributary = max(
            float(support.start_tributary_width or 0.0),
            float(support.end_tributary_width or 0.0),
            float(support.bay_spacing or project.design_settings.default_support_spacing or 5.0),
            1.0,
        )
        # The projection factor prevents a highly oblique brace from appearing
        # artificially efficient in the preliminary axial-force model.
        dx = support.end.x - support.start.x
        dy = support.end.y - support.start.y
        length = max(math.hypot(dx, dy), EPS)
        projection = max(abs(dx) / length, abs(dy) / length, 0.35)
        base = average_pressure * vertical_tributary * plan_tributary / projection
        source = "tributary_pressure_screening"
    return {"baseAxialKn": max(base, 0.0), "source": source}


def _member_screening(project: Project, system: RetainingSystem, support: SupportElement, actual: dict[str, float], level_count: int) -> dict[str, Any]:
    props = _section_properties(support)
    mat = _material_properties(support)
    length = _length(support)
    unbraced = _effective_unbraced_length(system, support)
    k_factor = 1.0 if support.support_role in {"main_strut", "secondary_strut"} else 1.1
    effective_length = k_factor * unbraced
    slenderness = effective_length / max(props["radiusM"], EPS)
    euler_kn = math.pi**2 * mat["elasticModulusKnM2"] * props["inertiaM4"] / max(effective_length**2, EPS)
    squash_kn = props["areaM2"] * mat["designStrengthMpa"] * 1000.0
    # Preliminary stability capacity.  It intentionally remains below both the
    # squash load and Euler load and therefore cannot replace the code-specific
    # compression-member curve used by the final member check.
    stability_capacity = min(0.85 * squash_kn, 0.75 * euler_kn)

    demand = _screening_demand(project, support, actual, level_count)
    base = float(demand["baseAxialKn"])
    temperature_delta = float(support.temperature_delta_c if support.temperature_delta_c is not None else project.design_settings.temperature_range_c or 20.0)
    restraint = max(0.0, min(float(project.design_settings.support_thermal_restraint_factor), 1.0))
    thermal = mat["elasticModulusKnM2"] * props["areaM2"] * mat["thermalExpansion"] * abs(temperature_delta) * restraint
    preload_ratio = float(support.preload_ratio if support.preload_ratio is not None else project.design_settings.support_preload_ratio)
    preload = float(support.preload if support.preload is not None else base * preload_ratio)
    gap_mm = float(project.design_settings.support_joint_gap_mm)
    # Joint gap is converted into a bounded fraction of the current pressure
    # demand. A pure EA/L * gap model is excessively stiff for temporary joints
    # and would dominate every preliminary candidate before contact/slip is modelled.
    gap_force = base * float(project.design_settings.support_gap_force_factor) * min(gap_mm / 3.0, 2.0)
    design_axial = base + 0.50 * preload + thermal + gap_force

    deviation_mm = float(support.construction_deviation_mm if support.construction_deviation_mm is not None else project.design_settings.support_installation_deviation_mm)
    node_eccentricity = max(deviation_mm / 1000.0, float(support.centerline_offset_m or 0.0) * 0.10)
    eccentricity_moment = design_axial * node_eccentricity
    if support.section_type == "rc_rectangular":
        moment_capacity = 0.12 * mat["designStrengthMpa"] * 1000.0 * max(float(support.section.width or 0.8), 0.2) * props["depthM"]**2
    else:
        elastic_modulus = props["inertiaM4"] / max(props["depthM"] / 2.0, EPS)
        moment_capacity = mat["designStrengthMpa"] * 1000.0 * elastic_modulus
    axial_util = design_axial / max(stability_capacity, EPS)
    moment_util = eccentricity_moment / max(moment_capacity, EPS)
    interaction = axial_util + moment_util

    slenderness_limit = float(project.design_settings.support_screening_slenderness_limit)
    utilization_limit = float(project.design_settings.support_target_utilization)
    fail = interaction > 1.15 or slenderness > 1.20 * slenderness_limit
    warning = interaction > utilization_limit or slenderness > slenderness_limit
    status = "fail" if fail else "warning" if warning else "pass"
    return {
        "supportId": support.id,
        "supportCode": support.code,
        "levelIndex": int(support.level_index),
        "role": support.support_role,
        "sectionType": support.section_type,
        "lengthM": round(length, 3),
        "effectiveUnbracedLengthM": round(unbraced, 3),
        "slenderness": round(slenderness, 2),
        "slendernessLimit": round(slenderness_limit, 2),
        "baseAxialKn": round(base, 3),
        "preloadKn": round(preload, 3),
        "thermalKn": round(thermal, 3),
        "gapClosureKn": round(gap_force, 3),
        "designAxialKn": round(design_axial, 3),
        "stabilityCapacityKn": round(stability_capacity, 3),
        "eulerLoadKn": round(euler_kn, 3),
        "eccentricityM": round(node_eccentricity, 4),
        "eccentricityMomentKnm": round(eccentricity_moment, 3),
        "axialUtilization": round(axial_util, 4),
        "momentUtilization": round(moment_util, 4),
        "interactionUtilization": round(interaction, 4),
        "screeningDemandSource": demand["source"],
        "status": status,
    }


def _connectivity_metrics(system: RetainingSystem) -> dict[str, Any]:
    by_level: dict[int, list[SupportElement]] = defaultdict(list)
    for support in system.supports or []:
        by_level[int(support.level_index)].append(support)
    level_rows: list[dict[str, Any]] = []
    isolated_faces: set[str] = set()
    single_edge_pairs = 0
    for level, supports in sorted(by_level.items()):
        graph: dict[str, set[str]] = defaultdict(set)
        edge_count: dict[tuple[str, str], int] = defaultdict(int)
        for support in supports:
            a, b = str(support.start_face_code or ""), str(support.end_face_code or "")
            if not a or not b:
                continue
            graph[a].add(b)
            graph[b].add(a)
            edge_count[tuple(sorted((a, b)))] += 1
        faces = set(graph)
        visited: set[str] = set()
        components = 0
        for face in faces:
            if face in visited:
                continue
            components += 1
            stack = [face]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(graph[current] - visited)
        isolated_faces.update(face for face, neighbours in graph.items() if not neighbours)
        single_edge_pairs += sum(1 for count in edge_count.values() if count == 1)
        level_rows.append({
            "levelIndex": level,
            "faceCount": len(faces),
            "componentCount": components,
            "wallPairCount": len(edge_count),
            "singleMemberWallPairCount": sum(1 for count in edge_count.values() if count == 1),
        })
    return {
        "levels": level_rows,
        "maximumConnectivityComponentCount": max((row["componentCount"] for row in level_rows), default=0),
        "singleMemberWallPairCount": single_edge_pairs,
        "isolatedFaceCount": len(isolated_faces),
    }


def evaluate_support_deep_design(
    project: Project,
    system: RetainingSystem | None = None,
    *,
    include_members: bool = True,
    calculation_result: Any | None = None,
    stage_results_override: list[Any] | None = None,
    calculation_current_override: bool | None = None,
) -> dict[str, Any]:
    system = system or project.retaining_system
    if not system or not system.supports:
        return {
            "status": "blocked",
            "summary": "尚未生成水平支撑体系。",
            "metrics": {"supportCount": 0},
            "memberChecks": [],
            "issues": ["缺少水平支撑，无法执行深化设计筛查。"],
            "designActions": ["先完成平面形状识别和支撑体系生成。"],
            "screeningPass": False,
            "hardPass": False,
            "calculationReady": False,
            "formalDesignReady": False,
            "evidenceGrade": "D",
        }

    actual, force_evidence = _force_evidence(
        project,
        calculation_result=calculation_result,
        stage_results_override=stage_results_override,
        calculation_current_override=calculation_current_override,
    )
    level_count = max(len({int(item.level_index) for item in system.supports}), 1)
    members = [_member_screening(project, system, item, actual, level_count) for item in system.supports]
    failures = [item for item in members if item["status"] == "fail"]
    warnings = [item for item in members if item["status"] == "warning"]
    connectivity = _connectivity_metrics(system)
    total_volume = sum(_section_properties(s)["areaM2"] * _length(s) for s in system.supports)
    max_util = max((float(item["interactionUtilization"]) for item in members), default=0.0)
    max_slenderness = max((float(item["slenderness"]) for item in members), default=0.0)
    max_unbraced = max((float(item["effectiveUnbracedLengthM"]) for item in members), default=0.0)
    construction_effect_ratio = max(
        ((float(item["preloadKn"]) + float(item["thermalKn"]) + float(item["gapClosureKn"])) / max(float(item["baseAxialKn"]), 1.0) for item in members),
        default=0.0,
    )
    level_demands: dict[int, list[float]] = defaultdict(list)
    for item in members:
        level_demands[int(item["levelIndex"])].append(float(item["designAxialKn"]))
    level_force_cv: list[float] = []
    level_force_peak_ratio: list[float] = []
    for values in level_demands.values():
        positive = [value for value in values if value > EPS]
        if not positive:
            continue
        average = sum(positive) / len(positive)
        variance = sum((value - average) ** 2 for value in positive) / len(positive)
        level_force_cv.append(math.sqrt(variance) / max(average, EPS))
        level_force_peak_ratio.append(max(positive) / max(average, EPS))
    maximum_force_cv = max(level_force_cv, default=0.0)
    maximum_force_peak_ratio = max(level_force_peak_ratio, default=1.0)
    node_count = len(system.support_nodes or [])
    node_unchecked = sum(1 for node in (system.support_nodes or []) if node.check_status in {"manual_review", "warning", "fail"})

    source_counts = defaultdict(int)
    for item in members:
        source_counts[str(item.get("screeningDemandSource") or "unknown")] += 1
    support_count = len(system.supports)
    staged_count = source_counts["staged_calculation_envelope"]
    stored_count = source_counts["stored_design_axial_force"]
    fallback_count = source_counts["tributary_pressure_screening"]
    staged_coverage = staged_count / max(support_count, 1)
    geotechnical = _geotechnical_evidence(project)
    assurance = dict(force_evidence.get("assurance") or {})
    assurance_status = str(assurance.get("status") or assurance.get("overallStatus") or "unknown").lower()
    eligible_for_issue = bool(assurance.get("eligibleForOfficialIssue") or assurance.get("eligible_for_official_issue"))

    screening_pass = not failures
    calculation_ready = bool(screening_pass and force_evidence.get("current") and staged_coverage >= 0.95)
    geotechnical_ready = bool(
        geotechnical["stratumCount"] > 0
        and geotechnical["requiredParameterCompletenessRatio"] >= 0.95
        and geotechnical["lowConfidenceStratumCount"] == 0
        and geotechnical["geologicalModelAvailable"]
        and geotechnical["coveragePass"]
    )
    formal_design_ready = bool(calculation_ready and node_unchecked == 0 and geotechnical_ready and (eligible_for_issue or assurance_status == "pass"))
    if calculation_ready and formal_design_ready:
        evidence_grade = "A"
    elif calculation_ready:
        evidence_grade = "B"
    elif staged_count > 0 or stored_count == support_count:
        evidence_grade = "C"
    else:
        evidence_grade = "D"

    status = "fail" if failures else "warning" if warnings or node_unchecked or not calculation_ready else "pass"
    issues: list[str] = []
    actions: list[str] = []
    if failures:
        issues.append(f"{len(failures)} 根支撑的稳定/偏心组合筛查超过硬控制范围。")
        actions.append("优先调整支撑站位和临时立柱，缩短计算长度；随后增大截面或切换支撑体系。")
    if warnings:
        issues.append(f"{len(warnings)} 根支撑接近目标利用率或长细比筛查限值。")
        actions.append("对高利用率构件执行完整施工阶段轴力包络、二阶效应和节点刚度复核。")
    if force_evidence.get("staleCalculationIgnored"):
        issues.append("检测到历史计算结果与当前方案合同不一致，已禁止其参与支撑承载力判定。")
        actions.append("按当前几何、支撑拓扑、土层参数和施工阶段重新执行完整计算。")
    if staged_coverage < 0.95:
        issues.append(f"当前分阶段计算轴力仅覆盖 {staged_count}/{support_count} 根支撑，其余采用存储轴力或分担宽度估算。")
        actions.append("补齐所有支撑在各施工阶段的轴力包络后，再进入计算就绪状态。")
    if geotechnical["stratumCount"] == 0 or geotechnical["requiredParameterCompletenessRatio"] < 0.95:
        issues.append("土层重度和内摩擦角等控制参数不完整，主动土压力快速估算的证据等级受限。")
        actions.append("补齐分层勘察参数、参数来源和置信度，并复核地质模型覆盖范围。")
    if geotechnical["lowConfidenceStratumCount"]:
        issues.append(f"存在 {geotechnical['lowConfidenceStratumCount']} 个低置信度土层，正式设计需要勘察复核或敏感性包络。")
        actions.append("对低置信度参数执行不利取值、上下限敏感性分析和监测反演校准。")
    if connectivity["singleMemberWallPairCount"]:
        issues.append("部分墙面对仅由单根构件连接，施工拆换或局部失效时缺少替代传力路径。")
        actions.append("在不增加非法交叉的前提下复核关键墙面对的冗余、分区施工和换撑路径。")
    if construction_effect_ratio > 0.35:
        issues.append("温度、预加轴力和节点间隙对轴力包络的占比较高。")
        actions.append("将温度范围、预加轴力协议、安装间隙和实测锁定值纳入施工工况。")
    if maximum_force_cv > 0.35 or maximum_force_peak_ratio > 1.80:
        issues.append("同层支撑轴力分配离散，局部站位或分担宽度可能不均。")
        actions.append("调整支撑站位、端部角撑覆盖范围和局部扩宽区分仓，使同层轴力包络更均衡。")
    if node_unchecked:
        issues.append(f"{node_unchecked}/{node_count} 个支撑—围檩节点尚未达到通过状态。")
        actions.append("完成端板/承压板、局部围檩、加劲肋、锚固及混凝土节点区验算。")
    if not issues:
        issues.append("当前方案已具备完整计算证据；正式发行仍受全局质量闸门、校审签署和施工图完整性控制。")

    metrics = {
        "supportCount": support_count,
        "supportLevelCount": level_count,
        "memberFailCount": len(failures),
        "memberWarningCount": len(warnings),
        "maximumInteractionUtilization": round(max_util, 4),
        "maximumSlenderness": round(max_slenderness, 2),
        "maximumEffectiveUnbracedLengthM": round(max_unbraced, 3),
        "maximumConstructionEffectRatio": round(construction_effect_ratio, 4),
        "maximumSupportForceCoefficientOfVariation": round(maximum_force_cv, 4),
        "maximumSupportForcePeakToMeanRatio": round(maximum_force_peak_ratio, 4),
        "supportMaterialVolumeM3": round(total_volume, 3),
        "supportNodeCount": node_count,
        "supportNodeUncheckedCount": node_unchecked,
        "stagedCalculationMemberCount": staged_count,
        "storedDesignForceMemberCount": stored_count,
        "tributaryScreeningMemberCount": fallback_count,
        "stagedCalculationCoverageRatio": round(staged_coverage, 4),
        **{key: value for key, value in connectivity.items() if key != "levels"},
    }
    return {
        "status": status,
        "summary": (
            f"支撑深化筛查：{support_count} 根、{level_count} 层；最大组合利用率 {max_util:.3f}，"
            f"分阶段轴力覆盖 {staged_coverage:.0%}；证据等级 {evidence_grade}；"
            f"失败 {len(failures)} 根、预警 {len(warnings)} 根。"
        ),
        "model": {
            "name": "wall-wale-strut staged preliminary deep-design screening",
            "memberDemand": "current staged envelope > stored design force > tributary pressure screening",
            "constructionEffects": "N_eff = N + 0.5*N_preload + N_temperature + N_gap",
            "stability": "N_capacity = min(0.85*A*f, 0.75*pi^2*E*I/(K*L)^2)",
            "interaction": "eta = N_eff/N_capacity + M_ecc/M_capacity",
            "forceBalance": "CV_N,l = std(N_l)/mean(N_l); peak ratio = max(N_l)/mean(N_l)",
            "scope": "candidate ranking and design diagnosis; final calculation and code-specific member curves remain mandatory",
        },
        "metrics": metrics,
        "connectivity": connectivity,
        "memberChecks": members if include_members else [],
        "governingMembers": sorted(members, key=lambda item: float(item["interactionUtilization"]), reverse=True)[:20],
        "issues": issues,
        "designActions": actions,
        "screeningPass": screening_pass,
        "hardPass": screening_pass,
        "calculationReady": calculation_ready,
        "formalDesignReady": formal_design_ready,
        "evidenceGrade": evidence_grade,
        "evidence": {"forceEnvelope": force_evidence, "geotechnical": geotechnical},
        "readiness": {
            "screeningPass": screening_pass,
            "currentCalculation": bool(force_evidence.get("current")),
            "stagedForceCoveragePass": staged_coverage >= 0.95,
            "nodeDetailingPass": node_unchecked == 0,
            "geotechnicalEvidencePass": geotechnical_ready,
            "calculationAssurancePass": eligible_for_issue or assurance_status == "pass",
        },
    }


def optimize_support_deep_design(project: Project, *, max_iterations: int | None = None) -> dict[str, Any]:
    """Bounded preliminary member/column iteration on the adopted topology.

    The topology is kept fixed.  The routine may enlarge member sections and
    regenerate temporary columns to control the effective unbraced length.  A
    topology change remains the responsibility of the support-layout optimizer.
    """
    system = project.retaining_system
    if not system or not system.supports:
        return evaluate_support_deep_design(project, system)
    from app.services.support_layout import make_column_elements, make_support_wale_nodes

    iterations = max(1, min(int(max_iterations or project.design_settings.max_design_iterations or 3), 6))
    target = float(project.design_settings.support_target_utilization)
    history: list[dict[str, Any]] = []
    changed_supports: set[str] = set()
    before = evaluate_support_deep_design(project, system, include_members=True)
    current = before

    for iteration in range(1, iterations + 1):
        actions: list[dict[str, Any]] = []
        need_shorter_unbraced = False
        checks = {str(item["supportId"]): item for item in current.get("memberChecks", [])}
        by_id = {item.id: item for item in system.supports}
        for support_id, check in checks.items():
            support = by_id.get(support_id)
            if support is None:
                continue
            utilization = float(check.get("interactionUtilization") or 0.0)
            slenderness = float(check.get("slenderness") or 0.0)
            slenderness_limit = float(check.get("slendernessLimit") or project.design_settings.support_screening_slenderness_limit)
            if slenderness > slenderness_limit and float(check.get("effectiveUnbracedLengthM") or 0.0) > 8.0:
                need_shorter_unbraced = True
            if utilization <= target and slenderness <= slenderness_limit:
                continue
            before_section = support.section.model_dump(mode="json", by_alias=True)
            if support.section_type == "rc_rectangular":
                scale = max(1.05, min(1.25, math.sqrt(max(utilization / max(target, 0.1), 1.0))))
                support.section.width = min(1.50, round(max(float(support.section.width or 0.8) * scale, 0.8) / 0.05) * 0.05)
                support.section.height = min(1.50, round(max(float(support.section.height or 0.8) * scale, 0.8) / 0.05) * 0.05)
                support.section.name = f"{int(round(support.section.width * 1000))}x{int(round(support.section.height * 1000))} RC"
            elif support.section_type == "steel_pipe":
                diameters = [0.609, 0.711, 0.800, 0.914, 1.016, 1.200]
                thicknesses = [0.016, 0.018, 0.020, 0.022, 0.025, 0.028]
                current_d = float(support.section.diameter or 0.609)
                current_t = float(support.section.wall_thickness or 0.016)
                support.section.diameter = next((value for value in diameters if value > current_d + 1.0e-6), diameters[-1])
                support.section.wall_thickness = next((value for value in thicknesses if value >= current_t), thicknesses[-1])
                support.section.name = f"D{int(round(support.section.diameter * 1000))}x{int(round(support.section.wall_thickness * 1000))} steel pipe"
            else:
                support.section.width = min(0.80, max(float(support.section.width or 0.4) + 0.05, 0.4))
                support.section.height = min(1.20, max(float(support.section.height or 0.4) + 0.10, 0.4))
                support.section.name = f"H-equivalent {support.section.width:.2f}x{support.section.height:.2f}m"
            changed_supports.add(support.id)
            actions.append({
                "supportId": support.id,
                "supportCode": support.code,
                "action": "section_upgrade",
                "before": before_section,
                "after": support.section.model_dump(mode="json", by_alias=True),
                "reason": f"eta={utilization:.3f}, slenderness={slenderness:.1f}",
            })

        if need_shorter_unbraced:
            existing_limit = max(
                (float(item.get("effectiveUnbracedLengthM") or 0.0) for item in current.get("memberChecks", [])),
                default=18.0,
            )
            target_span = max(8.0, min(15.0, existing_limit * 0.75))
            system.columns = make_column_elements(project.excavation, system.supports, max_unbraced_span_m=target_span)
            actions.append({"action": "regenerate_temporary_columns", "targetMaximumUnbracedSpanM": round(target_span, 3), "columnCount": len(system.columns)})
        system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)
        current = evaluate_support_deep_design(project, system, include_members=True)
        history.append({
            "iteration": iteration,
            "actions": actions,
            "status": current.get("status"),
            "metrics": current.get("metrics"),
        })
        if current.get("hardPass") and float((current.get("metrics") or {}).get("maximumInteractionUtilization", 99.0)) <= target:
            break
        if not actions:
            break

    result = dict(current)
    result["before"] = {"status": before.get("status"), "metrics": before.get("metrics"), "summary": before.get("summary")}
    result["optimizationHistory"] = history
    result["changedSupportIds"] = sorted(changed_supports)
    result["iterationCount"] = len(history)
    result["topologyChanged"] = False
    result["requiresTopologyUpgrade"] = not bool(result.get("hardPass"))
    system.layout_summary = dict(system.layout_summary or {})
    system.layout_summary["supportDeepDesign"] = {
        key: value for key, value in result.items() if key != "memberChecks"
    }
    for support in system.supports:
        if support.id in changed_supports:
            support.section_optimization_status = "section_upgraded"
            support.section_optimization_note = "V3.35 evidence-gated support deep-design iteration; final staged member and node checks remain required."
        elif result.get("requiresTopologyUpgrade"):
            support.section_optimization_status = "topology_upgrade_required"
    return result
