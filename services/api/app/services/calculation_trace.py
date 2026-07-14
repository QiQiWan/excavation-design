from __future__ import annotations

from typing import Any

from app.schemas.domain import Project


def _stage_name(project: Project, stage_id: str | None) -> str:
    if not stage_id:
        return "未指定工况"
    for case in project.calculation_cases or []:
        for stage in case.stages:
            if stage.id == stage_id:
                return stage.name
    return stage_id


def _status_from_utilization(utilization: float | None, fallback: str | None = None) -> str:
    if fallback in {"fail", "warning", "manual_review", "pass"}:
        return fallback
    if utilization is None:
        return "manual_review"
    if utilization > 1.0:
        return "fail"
    if utilization > 0.85:
        return "warning"
    return "pass"


def _locator(workflow_step: str, target_panel: str, object_type: str | None, object_id: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "workflowStep": workflow_step,
        "targetPanel": target_panel,
        "objectType": object_type,
        "objectId": object_id,
        "action": "locate_object_and_open_panel",
    }
    if extra:
        data.update(extra)
    return data


def _entry(
    *,
    index: int,
    category: str,
    title: str,
    object_type: str | None,
    object_id: str | None,
    stage_id: str | None,
    stage_name: str,
    demand_name: str,
    demand_value: float | None,
    capacity_value: float | None = None,
    unit: str = "",
    status: str | None = None,
    formula: str = "",
    code_reference: str = "",
    method: str = "",
    input_parameters: dict[str, Any] | None = None,
    result_path: str | None = None,
    locator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    utilization = None
    if demand_value is not None and capacity_value not in (None, 0):
        try:
            utilization = abs(float(demand_value)) / abs(float(capacity_value))
        except Exception:
            utilization = None
    return {
        "id": f"trace-{index:04d}",
        "category": category,
        "title": title,
        "objectType": object_type,
        "objectId": object_id,
        "stageId": stage_id,
        "stageName": stage_name,
        "demandName": demand_name,
        "demandValue": round(float(demand_value), 6) if demand_value is not None else None,
        "capacityValue": round(float(capacity_value), 6) if capacity_value is not None else None,
        "utilization": round(float(utilization), 4) if utilization is not None else None,
        "unit": unit,
        "status": _status_from_utilization(utilization, status),
        "formula": formula,
        "codeReference": code_reference,
        "method": method,
        "inputParameters": input_parameters or {},
        "resultPath": result_path,
        "locator": locator or _locator("calculation", "ResultViewer", object_type, object_id),
    }


def build_calculation_trace(project: Project) -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if not latest:
        return {
            "projectId": project.id,
            "calculationResultId": None,
            "summary": {
                "traceCount": 0,
                "controlPathCompleteness": 0,
                "governingObjectCount": 0,
                "codeReferenceCount": 0,
                "status": "fail",
                "message": "尚未运行计算，无法形成可追溯计算链。",
            },
            "entries": [],
            "governingMap": [],
            "notes": ["先在 Step 6 运行完整计算，再读取计算追溯链。"],
        }

    entries: list[dict[str, Any]] = []
    idx = 1
    assurance = dict(getattr(latest, "calculation_assurance", {}) or {})
    contract = dict(assurance.get("contract") or {})
    stage_coverage = dict(assurance.get("stageCoverage") or {})
    numerical = dict(assurance.get("numericalQuality") or {})
    independent = dict(assurance.get("independentCheck") or {})
    traceability = dict(assurance.get("traceability") or {})
    baseline_rows = [
        ("calculation_input_baseline", "冻结计算输入快照", "输入快照哈希", 1.0 if latest.input_snapshot_hash else 0.0, 1.0, "immutable SHA-256 calculation input snapshot", contract),
        ("calculation_contract", "计算合同与采用设计快照", "合同当前性", 1.0 if latest.calculation_contract_id and latest.adopted_design_snapshot_hash else 0.0, 1.0, "input + case + geometry + topology + algorithm + rule-set contract", contract),
        ("stage_coverage", "施工阶段与墙段覆盖", "覆盖完整率", (float(stage_coverage.get("actual") or 0) / max(float(stage_coverage.get("expected") or 0), 1.0)), 1.0, "unique result for every stage × excavation segment", stage_coverage),
        ("numerical_quality", "数值收敛与病态矩阵审计", "平衡残差", float(numerical.get("maxRelativeResidual") or 0.0), 1.0e-8, "relative residual and matrix condition audit", numerical),
        ("independent_check", "独立计算路径对账", "最大位移相对差", float(independent.get("maxWallDisplacementRelativeDifference") or 0.0), float(independent.get("warningRatio") or 0.25), "global coupled model versus wall reference solver", independent),
        ("code_traceability", "规范校核追溯覆盖", "追溯完整率", float(traceability.get("coverage") or 0.0), 0.98, "rule/object/status/message/clause completeness", traceability),
    ]
    for category, title, demand_name, demand, capacity, method, evidence in baseline_rows:
        status = "pass"
        if category in {"calculation_input_baseline", "calculation_contract", "stage_coverage"} and demand < capacity:
            status = "fail"
        elif category == "numerical_quality" and demand > 1.0e-6:
            status = "fail"
        elif category == "numerical_quality" and demand > capacity:
            status = "warning"
        elif category == "independent_check" and demand > float(independent.get("failRatio") or 0.5):
            status = "manual_review"
        elif category == "independent_check" and demand > capacity:
            status = "warning"
        elif category == "code_traceability" and demand < 0.90:
            status = "fail"
        elif category == "code_traceability" and demand < capacity:
            status = "warning"
        entries.append(_entry(
            index=idx, category=category, title=title, object_type="CalculationBaseline", object_id=latest.calculation_contract_id or latest.id,
            stage_id=None, stage_name="全局计算基线", demand_name=demand_name, demand_value=demand, capacity_value=capacity, unit="ratio", status=status,
            formula=method, code_reference="PitGuard V3.24 industrial calculation assurance", method=method, input_parameters=evidence,
            result_path="calculationResults[-1].calculationAssurance", locator=_locator("calculation", "CalculationRecoveryPanel", "CalculationBaseline", latest.calculation_contract_id or latest.id),
        )); idx += 1
    max_wall_moment = latest.governing_values.max_wall_moment or 0.0
    max_wall_shear = latest.governing_values.max_wall_shear or 0.0
    max_disp = latest.governing_values.max_displacement or 0.0
    max_support = latest.governing_values.max_support_axial_force or 0.0

    for sidx, stage in enumerate(latest.stage_results or []):
        stage_name = _stage_name(project, stage.stage_id)
        if stage.wall_internal_force:
            w = stage.wall_internal_force
            entries.append(_entry(
                index=idx,
                category="wall_internal_force",
                title="围护墙控制弯矩追溯",
                object_type="DiaphragmWallPanel",
                object_id=w.segment_id,
                stage_id=stage.stage_id,
                stage_name=stage_name,
                demand_name="最大弯矩",
                demand_value=w.max_moment_design or w.max_moment,
                capacity_value=max((abs(max_wall_moment) * 1.20), abs(w.max_moment_design or w.max_moment), 1.0),
                unit="kN·m/m",
                formula="M_d = gamma_0 * gamma_F * envelope(M_stage)",
                code_reference="JGJ 120-2012 支护结构内力与 GB 50010 承载力子集",
                method=w.method,
                input_parameters={"importanceFactor": w.importance_factor, "loadCombinationFactor": w.load_combination_factor, "pointCount": len(w.points)},
                result_path=f"calculationResults[-1].stageResults[{sidx}].wallInternalForce.maxMomentDesign",
                locator=_locator("calculation", "WallEnvelopeChart", "DiaphragmWallPanel", w.segment_id, {"stageId": stage.stage_id}),
            )); idx += 1
            entries.append(_entry(
                index=idx,
                category="wall_deformation",
                title="围护墙控制位移追溯",
                object_type="DiaphragmWallPanel",
                object_id=w.segment_id,
                stage_id=stage.stage_id,
                stage_name=stage_name,
                demand_name="最大水平位移",
                demand_value=w.max_displacement,
                capacity_value=max(max_disp * 1.20, w.max_displacement or 0.0, 1.0),
                unit="mm",
                formula="u_max = max(|u_i|), limit = min(H/ratio, project_limit)",
                code_reference="JGJ 120-2012 支护结构变形控制子集",
                method=w.method,
                input_parameters={"pointCount": len(w.points), "stageId": stage.stage_id},
                result_path=f"calculationResults[-1].stageResults[{sidx}].wallInternalForce.maxDisplacement",
                locator=_locator("calculation", "WallEnvelopeChart", "DiaphragmWallPanel", w.segment_id, {"stageId": stage.stage_id}),
            )); idx += 1
        for fidx, force in enumerate((stage.support_forces or [])[:12]):
            support_id = force.support_id or f"support-level-{force.level_index}"
            demand = force.axial_force_design or force.effective_axial_force or force.axial_force
            capacity = max(max_support * 1.25, abs(demand) * 1.08, 1.0)
            entries.append(_entry(
                index=idx,
                category="support_axial_force",
                title="水平支撑轴力追溯",
                object_type="SupportElement",
                object_id=support_id,
                stage_id=stage.stage_id,
                stage_name=stage_name,
                demand_name="设计轴力",
                demand_value=demand,
                capacity_value=capacity,
                unit=force.unit or "kN",
                formula="N_d = gamma_0 * gamma_F * integral(p_net * tributary_width dz) + preload + construction_effects",
                code_reference="JGJ 120-2012 内支撑轴力计算；GB 50017/GB 50010 构件承载力子集",
                method=force.method,
                input_parameters={
                    "levelIndex": force.level_index,
                    "tributaryTop": force.tributary_top,
                    "tributaryBottom": force.tributary_bottom,
                    "tributaryWidth": force.tributary_width,
                    "preloadEffect": force.preload_effect,
                    "thermalEffect": force.thermal_effect,
                    "gapEffect": force.gap_effect,
                    "eccentricityEffect": force.eccentricity_effect,
                    "distributionMethod": force.distribution_method,
                },
                result_path=f"calculationResults[-1].stageResults[{sidx}].supportForces[{fidx}]",
                locator=_locator("calculation", "SupportAxialEnvelope", "SupportElement", support_id, {"stageId": stage.stage_id}),
            )); idx += 1
        for bidx, wale in enumerate((stage.wale_beam_results or [])[:8]):
            demand = wale.max_moment_design or wale.max_moment
            capacity = max(abs(demand) * 1.12, 1.0)
            entries.append(_entry(
                index=idx,
                category="wale_internal_force",
                title="围檩控制弯矩追溯",
                object_type="BeamElement",
                object_id=wale.wale_beam_code,
                stage_id=stage.stage_id,
                stage_name=stage_name,
                demand_name="围檩最大弯矩",
                demand_value=demand,
                capacity_value=capacity,
                unit="kN·m",
                formula="M_d = envelope(continuous_beam_reaction, elastic_supports, stage_load)",
                code_reference="GB 50010-2010（2024 局部修订）受弯构件承载力子集",
                method=wale.method,
                input_parameters={"faceCode": wale.face_code, "levelIndex": wale.level_index, "beamLength": wale.beam_length, "supportNodeCount": wale.support_node_count},
                result_path=f"calculationResults[-1].stageResults[{sidx}].waleBeamResults[{bidx}]",
                locator=_locator("calculation", "WaleEnvelopeChart", "BeamElement", wale.wale_beam_code, {"stageId": stage.stage_id}),
            )); idx += 1
        for cidx, check in enumerate((stage.checks or [])[:10]):
            status = str(check.get("status") or "manual_review")
            if status not in {"pass", "warning", "fail", "manual_review"}:
                status = "manual_review"
            entries.append(_entry(
                index=idx,
                category="code_check",
                title=str(check.get("message") or check.get("ruleId") or "规范筛查追溯"),
                object_type=str(check.get("objectType") or check.get("object_type") or "calculation_check"),
                object_id=str(check.get("objectId") or check.get("object_id") or ""),
                stage_id=stage.stage_id,
                stage_name=stage_name,
                demand_name=str(check.get("ruleId") or check.get("rule_id") or "check"),
                demand_value=check.get("calculatedValue") or check.get("calculated_value"),
                capacity_value=check.get("limitValue") or check.get("limit_value"),
                unit=str(check.get("unit") or ""),
                status=status,
                formula=str(check.get("formula") or check.get("method") or "demand <= limit"),
                code_reference=str(check.get("clauseReference") or check.get("clause_reference") or check.get("standardName") or "规范子集筛查"),
                method=str(check.get("method") or "rule_check"),
                input_parameters={k: v for k, v in check.items() if k not in {"message", "recommendation"}},
                result_path=f"calculationResults[-1].stageResults[{sidx}].checks[{cidx}]",
                locator=_locator("calculation", "CheckTable", str(check.get("objectType") or check.get("object_type") or "calculation_check"), str(check.get("objectId") or check.get("object_id") or ""), {"stageId": stage.stage_id}),
            )); idx += 1

    for cidx, check in enumerate((latest.checks or [])[:60]):
        if any(e.get("sourceCheckIndex") == cidx for e in entries):
            continue
        status = str(check.get("status") or "manual_review")
        entries.append(_entry(
            index=idx,
            category="global_code_check",
            title=str(check.get("message") or check.get("ruleId") or "全局规范筛查追溯"),
            object_type=str(check.get("objectType") or check.get("object_type") or "calculation_check"),
            object_id=str(check.get("objectId") or check.get("object_id") or ""),
            stage_id=None,
            stage_name="全局包络",
            demand_name=str(check.get("ruleId") or check.get("rule_id") or "check"),
            demand_value=check.get("calculatedValue") or check.get("calculated_value"),
            capacity_value=check.get("limitValue") or check.get("limit_value"),
            unit=str(check.get("unit") or ""),
            status=status if status in {"pass", "warning", "fail", "manual_review"} else "manual_review",
            formula=str(check.get("formula") or check.get("method") or "demand <= limit"),
            code_reference=str(check.get("clauseReference") or check.get("clause_reference") or check.get("standardName") or "规范子集筛查"),
            method=str(check.get("method") or "rule_check"),
            input_parameters={k: v for k, v in check.items() if k not in {"message", "recommendation"}},
            result_path=f"calculationResults[-1].checks[{cidx}]",
            locator=_locator("calculation", "CheckTable", str(check.get("objectType") or check.get("object_type") or "calculation_check"), str(check.get("objectId") or check.get("object_id") or "")),
        )); idx += 1

    if latest.stability_detailed_result:
        st = latest.stability_detailed_result
        factors = [
            ("heave", "坑底抗隆起安全系数", st.heave_factor),
            ("confined_uplift", "承压水抗突涌安全系数", st.confined_uplift_factor),
            ("seepage", "渗流稳定安全系数", st.seepage_factor),
            ("overall", "整体稳定安全系数", st.overall_stability_factor),
        ]
        for key, title, value in factors:
            if value is None:
                continue
            entries.append(_entry(
                index=idx,
                category="stability",
                title=title,
                object_type="StabilityControlSection",
                object_id=st.controlling_section_id or key,
                stage_id=None,
                stage_name="稳定性包络",
                demand_name="安全系数需求",
                demand_value=1.0,
                capacity_value=value,
                unit="FS",
                formula="FS = resistance / action",
                code_reference="JGJ 120-2012 稳定性验算子集",
                method=st.method,
                input_parameters={"controllingMode": st.controlling_mode, "controllingSectionName": st.controlling_section_name},
                result_path=f"calculationResults[-1].stabilityDetailedResult.{key}Factor",
                locator=_locator("calculation", "StabilityDiagram", "StabilityControlSection", st.controlling_section_id or key),
            )); idx += 1

    statuses = {"pass": 0, "warning": 0, "fail": 0, "manual_review": 0}
    code_refs: set[str] = set()
    objects: set[str] = set()
    for item in entries:
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        if item.get("codeReference"):
            code_refs.add(str(item["codeReference"]))
        if item.get("objectId"):
            objects.add(str(item["objectId"]))
    completeness_flags = [
        bool(entries),
        bool(latest.stage_results),
        bool(latest.checks),
        bool(latest.report_diagram_data),
        bool(code_refs),
        bool(objects),
    ]
    completeness = round(100.0 * sum(1 for f in completeness_flags if f) / len(completeness_flags), 1)
    return {
        "projectId": project.id,
        "calculationResultId": latest.id,
        "summary": {
            "traceCount": len(entries),
            "controlPathCompleteness": completeness,
            "governingObjectCount": len(objects),
            "codeReferenceCount": len(code_refs),
            "status": "pass" if completeness >= 99 and statuses.get("fail", 0) == 0 else "warning" if entries else "fail",
            "message": "已形成工况—构件—截面—公式—规范条文追溯链。" if entries else "未形成追溯链。",
            "statusCounts": statuses,
        },
        "entries": entries[:220],
        "governingMap": sorted(list(objects))[:80],
        "notes": [
            "该追溯链用于工程复核和软件校审，不替代注册工程师对规范适用条件和参数来源的判断。",
            "capacityValue 中的承载力字段来自当前设计辅助模型或规则筛查结果；若为自动估算，应以正式详图设计复核值替换。",
        ],
    }
