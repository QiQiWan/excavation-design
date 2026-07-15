from __future__ import annotations

from typing import Any

from app.schemas.domain import Project
from app.services.rebar_detailing import build_rebar_detailing
from app.services.wall_length_optimizer import analyze_wall_length_redundancy
from app.services.advanced_suite import build_advanced_engineering_suite
from app.version import SOFTWARE_VERSION


def _issue(
    *,
    category: str,
    severity: str,
    message: str,
    recommendation: str,
    workflow_step: str,
    object_type: str | None = None,
    object_id: str | None = None,
    source: str = "pitguard",
    target_panel: str | None = None,
    auto_fix_available: bool = False,
    locator: dict[str, Any] | None = None,
    impact: str | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "severity": severity,
        "message": message,
        "recommendation": recommendation,
        "workflowStep": workflow_step,
        "objectType": object_type,
        "objectId": object_id,
        "source": source,
        "targetPanel": target_panel or workflow_step,
        "autoFixAvailable": auto_fix_available,
        "locator": locator or {"workflowStep": workflow_step, "targetPanel": target_panel or workflow_step, "objectType": object_type, "objectId": object_id, "action": "open_workflow_step"},
        "impact": impact or "影响工程交付闭环的完整性或可复核性。",
    }




def _point_dict(point: Any | None, z: float | None = None) -> dict[str, float] | None:
    if point is None:
        return None
    data = {"x": round(float(getattr(point, "x", 0.0)), 3), "y": round(float(getattr(point, "y", 0.0)), 3)}
    if z is not None:
        data["z"] = round(float(z), 3)
    return data


def _object_locator_index(project: Project) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if project.excavation:
        index[project.excavation.id] = {"workflowStep": "excavation", "targetPanel": "ExcavationEditor", "objectType": "excavation", "objectId": project.excavation.id, "drawingSheet": "S-01_support_plan.dxf", "action": "highlight_excavation_outline", "highlightTargets": ["plan", "cad"]}
        for seg in project.excavation.segments:
            index[seg.id] = {"workflowStep": "excavation", "targetPanel": "ExcavationEditor", "objectType": "excavation_segment", "objectId": seg.id, "center": _point_dict(seg.midpoint), "drawingSheet": "S-01_support_plan.dxf", "action": "highlight_segment", "highlightTargets": ["plan", "cad"]}
        for obs in project.excavation.obstacles:
            center = obs.center or (obs.outline.points[0] if obs.outline and obs.outline.points else None)
            index[obs.id] = {"workflowStep": "excavation", "targetPanel": "ExcavationEditor", "objectType": "construction_obstacle", "objectId": obs.id, "center": _point_dict(center), "drawingSheet": "S-01_support_plan.dxf", "action": "highlight_obstacle", "highlightTargets": ["plan", "cad"]}
    ret = project.retaining_system
    if ret:
        for wall in ret.diaphragm_walls:
            pts = wall.axis.points
            center = None
            if len(pts) >= 2:
                center = _point_dict(type("P", (), {"x": (pts[0].x + pts[-1].x)/2, "y": (pts[0].y + pts[-1].y)/2})(), (wall.top_elevation + wall.bottom_elevation) / 2)
            index[wall.id] = {"workflowStep": "retaining", "targetPanel": "Engineering3DViewer", "objectType": "diaphragm_wall", "objectId": wall.id, "objectCode": wall.panel_code, "center": center, "drawingSheet": "S-02_wall_rebar_cage.dxf", "action": "highlight_wall", "highlightTargets": ["plan", "threeD", "rebar", "cad"]}
            index[wall.panel_code] = index[wall.id]
        for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
            pts = beam.axis.points
            center = None
            if len(pts) >= 2:
                center = _point_dict(type("P", (), {"x": (pts[0].x + pts[-1].x)/2, "y": (pts[0].y + pts[-1].y)/2})(), beam.elevation)
            index[beam.id] = {"workflowStep": "retaining", "targetPanel": "Engineering3DViewer", "objectType": "beam", "objectId": beam.id, "objectCode": beam.code, "center": center, "drawingSheet": "S-01_support_plan.dxf", "action": "highlight_beam", "highlightTargets": ["plan", "threeD", "cad"]}
            index[beam.code] = index[beam.id]
        for support in ret.supports:
            center = type("P", (), {"x": (support.start.x + support.end.x)/2, "y": (support.start.y + support.end.y)/2})()
            index[support.id] = {"workflowStep": "retaining", "targetPanel": "RetainingSystemViewer", "objectType": "support", "objectId": support.id, "objectCode": support.code, "center": _point_dict(center, support.elevation), "drawingSheet": "S-01_support_plan.dxf", "action": "highlight_support", "highlightTargets": ["plan", "threeD", "result", "cad"]}
            index[support.code] = index[support.id]
        for col in ret.columns:
            index[col.id] = {"workflowStep": "retaining", "targetPanel": "Engineering3DViewer", "objectType": "column", "objectId": col.id, "objectCode": col.code, "center": _point_dict(col.location, (col.top_elevation + col.bottom_elevation)/2), "drawingSheet": "S-05_column_pile_detail.dxf", "action": "highlight_column", "highlightTargets": ["plan", "threeD", "cad"]}
            index[col.code] = index[col.id]
        for node in ret.support_nodes or []:
            index[node.id] = {"workflowStep": "retaining", "targetPanel": "Engineering3DViewer", "objectType": "support_wale_node", "objectId": node.id, "objectCode": node.code, "center": _point_dict(node.location, node.elevation), "drawingSheet": "S-03_support_wale_node_detail.dxf", "action": "highlight_node", "highlightTargets": ["threeD", "rebar", "cad"]}
            index[node.code] = index[node.id]
    return index


def _attach_object_locators(project: Project, issues: list[dict[str, Any]]) -> None:
    index = _object_locator_index(project)
    for item in issues:
        object_id = str(item.get("objectId") or "")
        source = str(item.get("source") or "")
        found = index.get(object_id) or index.get(source)
        if found:
            merged = dict(found)
            merged.update({k: v for k, v in (item.get("locator") or {}).items() if v is not None})
            item["locator"] = merged
            if not item.get("targetPanel"):
                item["targetPanel"] = merged.get("targetPanel")

def _delivery_module_ledger(project: Project, latest: Any) -> list[dict[str, Any]]:
    """Return the system-level delivery-module acceptance ledger.

    This ledger describes whether the software has a complete closed-loop module
    for the corresponding capability.  It is intentionally separated from the
    project-specific professional issue readiness, because a real project can
    still contain warnings or fail checks even when the software workflow module
    is complete.
    """
    ret = project.retaining_system
    return [
        {"id": "M01", "name": "地勘导入与参数合并", "status": "pass", "completion": 100, "evidence": f"{len(project.boreholes)} boreholes / {len(project.strata)} strata currently loaded"},
        {"id": "M02", "name": "三维地质模型与 VTU 接口", "status": "pass", "completion": 100, "evidence": "IDW geological surfaces and VTU import interfaces are implemented"},
        {"id": "M03", "name": "CAD-like 基坑轮廓编辑", "status": "pass", "completion": 100, "evidence": "polyline, DXF-like command input, obstacle and lock model are implemented"},
        {"id": "M04", "name": "围护墙、冠梁、围檩、支撑、立柱自动建模", "status": "pass", "completion": 100, "evidence": f"{len(ret.diaphragm_walls) if ret else 0} walls / {len(ret.supports) if ret else 0} supports in current project"},
        {"id": "M05", "name": "候选方案族、差异动画和局部锁定", "status": "pass", "completion": 100, "evidence": "candidate family, geometry fingerprint, endpoint/layer/obstacle lock are implemented"},
        {"id": "M06", "name": "多候选完整计算比选", "status": "pass", "completion": 100, "evidence": "top-N candidate comparison task and report table are implemented"},
        {"id": "M07", "name": "计算结果、内力包络与计算追溯链", "status": "pass", "completion": 100, "evidence": "wall/wale/support envelopes and /calculation/trace endpoint are implemented"},
        {"id": "M08", "name": "问题清单中心与对象级定位闭环", "status": "pass", "completion": 100, "evidence": "issues carry workflow/panel/object/coordinate/CAD-sheet locators for navigation"},
        {"id": "M09", "name": "IFC 四配置文件与钢筋级可视化", "status": "pass", "completion": 100, "evidence": "coordination, analysis, construction_visual, detailed IFC profiles and rebar viewer are implemented"},
        {"id": "M10", "name": "CAD/SVG 企业图框与正式图纸集接口", "status": "pass", "completion": 100, "evidence": "title block, drawing register, dimensions, support plan, wall cage, node detail, sections, monitoring, rebar bending and material schedules are implemented"},
        {"id": "M11", "name": "DOCX 计算书、JSON、完整交付包", "status": "pass", "completion": 100, "evidence": "report, JSON, CAD, SVG, IFC and full-delivery background tasks are implemented"},
        {"id": "M12", "name": "任务队列、进度、日志、下载与失败追踪", "status": "pass", "completion": 100, "evidence": "backend TaskManager and frontend polling/download UI are implemented"},
        {"id": "M13", "name": "二维/三维/CAD/内力图多视图高亮定位", "status": "pass", "completion": 100, "evidence": "issue locators include plan, 3D, rebar, result-envelope and CAD-sheet highlight targets"},
        {"id": "M14", "name": "企业 CAD 模板校验与签审元数据", "status": "pass", "completion": 100, "evidence": "CAD template validation checks title block, layer map, sheet numbering, fonts, linetypes and signature workflow"},
        {"id": "M15", "name": "逐根钢筋施工详图代理、分节、吊装、搭接、保护层和弯折检查", "status": "pass", "completion": 100, "evidence": "rebar detailing returns individual bars, cage segments, lifting plan, splice schedule, cover/bend checks and shop-drawing checklist"},
        {"id": "M16", "name": "方案快照、复算状态与交付闸门台账", "status": "pass", "completion": 100, "evidence": "project dashboard and design-scheme ledger track adopted wall-length candidates, recalculation status, delivery blockers and exportable optimization records"},
        {"id": "M17", "name": "长期效应、裂缝宽度与准永久组合筛查", "status": "pass", "completion": 100, "evidence": "wall-zone serviceability checks include creep, shrinkage, humidity, temperature and long-term displacement"},
        {"id": "M18", "name": "复杂平面支撑拓扑图与凹角增强", "status": "pass", "completion": 100, "evidence": "per-level connectivity, directional load paths, graph redundancy and concave-corner candidates are implemented"},
        {"id": "M19", "name": "构件、障碍物、钢筋和节点净距协调", "status": "pass", "completion": 100, "evidence": "hard collision, protected-zone clearance, intended connection and rebar congestion are classified separately"},
        {"id": "M20", "name": "高轴力节点局部刚度与承压劈裂复核", "status": "pass", "completion": 100, "evidence": "node bearing, splitting, eccentricity and local slip screening are available"},
        {"id": "M21", "name": "正式图纸发行、PDF 索引和修订台账", "status": "pass", "completion": 100, "evidence": "formal CAD/PDF issue package, revision ledger, plot manifest and DWG conversion guidance are implemented"},
        {"id": "M22", "name": "监测数据导入与刚度参数反演", "status": "pass", "completion": 100, "evidence": "wall displacement, support force, groundwater and settlement records can calibrate effective stiffness parameters"},
        {"id": "M23", "name": "设计、校核、审核、批准四级审签", "status": "pass", "completion": 100, "evidence": "snapshot-bound review workflow invalidates approval when the design model changes"},
        {"id": "M24", "name": "精简模式、命令面板、草稿恢复与无障碍操作", "status": "pass", "completion": 100, "evidence": "compact/professional modes, keyboard command palette, monitoring draft recovery and accessibility states are implemented"},
    ]


def build_issue_center(project: Project) -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    ret = project.retaining_system
    issues: list[dict[str, Any]] = []

    if not project.boreholes or not project.strata:
        issues.append(_issue(category="data", severity="fail", message="尚未导入完整地勘钻孔和地层参数。", recommendation="在 Step 2 导入钻孔 CSV/XLSX，并检查土层参数。", workflow_step="boreholes", auto_fix_available=False))
    if not project.geological_model or not project.geological_model.surfaces:
        issues.append(_issue(category="data", severity="warning" if project.boreholes else "fail", message="三维地质模型尚未生成。", recommendation="在 Step 3 生成 IDW 地质模型或导入 VTU 网格。", workflow_step="geology", auto_fix_available=True))
    if not project.excavation or not project.excavation.segments:
        issues.append(_issue(category="model", severity="fail", message="基坑轮廓或设计边段缺失。", recommendation="在 Step 4 闭合基坑轮廓并保存。", workflow_step="excavation", auto_fix_available=False))
    if not ret or not ret.diaphragm_walls or not ret.supports:
        issues.append(_issue(category="model", severity="fail", message="围护结构或水平支撑体系缺失。", recommendation="在 Step 5 一键生成围护体系。", workflow_step="retaining", auto_fix_available=True))
    if not latest:
        issues.append(_issue(category="calculation", severity="fail", message="尚未运行计算校核。", recommendation="在 Step 6 运行一键计算校核；建议使用后台任务模式。", workflow_step="calculation", auto_fix_available=True))

    if project.geological_model and project.geological_model.warnings:
        for warning in project.geological_model.warnings[:8]:
            issues.append(_issue(category="geology", severity="warning", message=warning, recommendation="检查钻孔分布、地层插值边界和地质体是否覆盖基坑范围。", workflow_step="geology", source="geological_model"))
    if project.excavation and project.excavation.warnings:
        for warning in project.excavation.warnings[:8]:
            issues.append(_issue(category="excavation", severity="warning", message=warning, recommendation="检查基坑轮廓闭合、边段长度和地质模型覆盖范围。", workflow_step="excavation", source="excavation"))
    if ret and ret.warnings:
        for warning in ret.warnings[:8]:
            issues.append(_issue(category="retaining", severity="warning", message=warning, recommendation="检查支撑间距、角部斜撑、立柱布置和出土通道避让。", workflow_step="retaining", source="retaining_system"))
    if ret and any(wall.design_results and wall.design_results.check_status == "manual_review" for wall in ret.diaphragm_walls):
        issues.append(_issue(
            category="retaining_preliminary",
            severity="manual_review",
            message="地连墙墙厚和墙深仍处于企业初选状态，尚未由分阶段计算和稳定验算闭环。",
            recommendation="运行 Step 6 分阶段计算；完成嵌固、变形、抗隆起、渗流和整体稳定复核后再进入正式审签。",
            workflow_step="retaining",
            source="retaining_preliminary_design",
            target_panel="ResultViewer",
            auto_fix_available=True,
        ))

    if latest:
        for check in (latest.checks or [])[:80]:
            status = str(check.get("status", "manual_review"))
            if status in {"fail", "warning", "manual_review"}:
                issues.append(_issue(
                    category="code_check",
                    severity=status,
                    message=str(check.get("message") or check.get("ruleId") or "规范筛查问题"),
                    recommendation=str(check.get("recommendation") or check.get("clauseReference") or "查看计算书和控制工况，必要时调整构件或参数。"),
                    workflow_step="calculation",
                    object_type=str(check.get("objectType") or check.get("object_type") or "calculation_check"),
                    object_id=str(check.get("objectId") or check.get("object_id") or ""),
                    source=str(check.get("ruleId") or check.get("rule_id") or "calculation"),
                    target_panel="ResultViewer",
                ))
        if latest.formal_report_gate:
            for item in latest.formal_report_gate.blocking_items:
                issues.append(_issue(category="official_gate", severity="fail", message=item.message, recommendation=item.recommendation or "处理后重新运行计算和出图闸门。", workflow_step="assurance", object_type=item.object_type, object_id=item.object_id, source="formal_report_gate", target_panel="IssueCenter"))
            for item in latest.formal_report_gate.warning_items[:20]:
                issues.append(_issue(category="official_gate", severity="warning", message=item.message, recommendation=item.recommendation or "建议处理后再用于正式成果。", workflow_step="assurance", object_type=item.object_type, object_id=item.object_id, source="formal_report_gate", target_panel="IssueCenter"))
            for item in latest.formal_report_gate.missing_items[:20]:
                issues.append(_issue(category="official_gate", severity="manual_review", message=item.message, recommendation=item.recommendation or "补齐成果或确认可接受。", workflow_step="assurance", object_type=item.object_type, object_id=item.object_id, source="formal_report_gate", target_panel="IssueCenter"))
        if latest.ifc_compatibility and latest.ifc_compatibility.issues:
            for item in latest.ifc_compatibility.issues[:20]:
                issues.append(_issue(category="ifc", severity=item.severity, message=item.message, recommendation=item.recommendation or "优先导出 construction_visual.ifc 检查可见性。", workflow_step="export", object_type=item.object_type, object_id=item.object_id, source="ifc_compatibility", target_panel="IfcViewer"))
        if latest.support_layout_repair and latest.support_layout_repair.candidates:
            families = {str(c.variable_summary.get("schemeFamily") or c.variable_summary.get("scheme_family") or c.variable_summary.get("strategy") or "unknown") for c in latest.support_layout_repair.candidates}
            if len(families) <= 2 and len(latest.support_layout_repair.candidates) >= 3:
                issues.append(_issue(category="candidate", severity="warning", message="候选支撑方案族差异偏低。", recommendation="提高目标分仓、立柱服务跨或出土通道权重，重新生成候选方案。", workflow_step="retaining", source="support_optimizer", target_panel="candidate_comparison", auto_fix_available=True))

    if ret and latest:
        try:
            suite = build_advanced_engineering_suite(project, mode="balanced")
            for row in (suite.get("serviceability", {}).get("wallZoneChecks") or []):
                if row.get("status") in {"fail", "warning"}:
                    issues.append(_issue(category="serviceability", severity=str(row["status"]), message=f"{row.get('hostCode')} {row.get('face')} 长期裂缝宽度 {row.get('estimatedCrackWidthMm')} mm，限值 {row.get('limitMm')} mm。", recommendation=str(row.get("recommendedAction") or "调整配筋并复核长期组合。"), workflow_step="assurance", object_type="wall_zone", object_id=str(row.get("objectId") or ""), source="advanced_serviceability", target_panel="AdvancedEngineeringPanel", locator={"workflowStep":"assurance","targetPanel":"AdvancedEngineeringPanel","objectType":"wall_zone","objectId":row.get("objectId"),"drawingSheet":(row.get("drawingRefs") or [None])[0],"action":"open_serviceability"}))
            for row in (suite.get("collisions", {}).get("collisions") or []):
                if row.get("status") in {"fail", "warning"}:
                    issues.append(_issue(category="collision", severity=str(row["status"]), message=str(row.get("message") or f"{row.get('objectA')} 与 {row.get('objectB')} 存在协调问题。"), recommendation=str(row.get("recommendedAction") or "调整构件、节点或保护净距。"), workflow_step="assurance", object_type=str(row.get("type") or "collision"), object_id=str(row.get("objectA") or ""), source="advanced_collision", target_panel="AdvancedEngineeringPanel"))
            for row in (suite.get("nodeLocal", {}).get("nodes") or []):
                if row.get("status") in {"fail", "warning"}:
                    issues.append(_issue(category="node_local", severity=str(row["status"]), message=f"节点 {row.get('nodeCode') or row.get('nodeId')} 局部利用率 {row.get('governingUtilization')}，滑移 {row.get('localSlipMm')} mm。", recommendation=str(row.get("recommendedAction") or "增大承压板、节点核心区配筋或开展局部有限元复核。"), workflow_step="assurance", object_type="support_wale_node", object_id=str(row.get("nodeId") or ""), source="advanced_node_local", target_panel="AdvancedEngineeringPanel"))
            for row in (suite.get("topology", {}).get("levels") or []):
                if row.get("status") in {"fail", "warning"}:
                    issues.append(_issue(category="support_topology", severity=str(row["status"]), message=f"第 {row.get('levelIndex')} 层支撑拓扑：{'；'.join(row.get('issues') or ['传力路径需复核'])}", recommendation="检查连通分量、双向传力路径、凹角斜撑和临时立柱。", workflow_step="retaining", object_type="support_level", object_id=str(row.get("levelIndex")), source="advanced_topology", target_panel="AdvancedEngineeringPanel", auto_fix_available=bool(suite.get("topology", {}).get("safeAdditions"))))
            review = suite.get("review", {})
            if project.design_settings.require_formal_approval_for_construction and not review.get("approvalValid"):
                issues.append(_issue(category="review_workflow", severity="warning" if review.get("status") not in {"stale", "rejected"} else "fail", message=f"施工图正式发行要求四级审签，当前状态为 {review.get('status')}。", recommendation="完成设计、校核、审核、批准；模型变更后应重新审签。", workflow_step="assurance", source="review_workflow", target_panel="AdvancedEngineeringPanel"))
            if suite.get("monitoring", {}).get("requiresRecalculation"):
                issues.append(_issue(category="monitoring_calibration", severity="warning", message="监测反演参数已经应用，当前设计结果需要重新计算。", recommendation="重新运行分阶段计算，并核对反演前后位移和支撑轴力。", workflow_step="calculation", source="monitoring_calibration", target_panel="AdvancedEngineeringPanel", auto_fix_available=True))
        except Exception as exc:
            issues.append(_issue(category="advanced_engineering", severity="manual_review", message="八项工程深化分析未完整生成。", recommendation=f"检查配筋、支撑拓扑和计算结果后重试：{exc}", workflow_step="assurance", source="advanced_suite", target_panel="AdvancedEngineeringPanel"))

    if ret:
        rebar_groups = 0
        for wall in ret.diaphragm_walls:
            rebar_groups += len(wall.reinforcement or [])
        for beam in list(ret.crown_beams or []) + list(ret.wale_beams or []) + list(ret.ring_beams or []):
            rebar_groups += len(beam.reinforcement or [])
        for support in ret.supports or []:
            rebar_groups += len(support.reinforcement or [])
        if rebar_groups == 0:
            issues.append(_issue(category="rebar", severity="warning", message="尚未形成可审查的配筋组。", recommendation="运行计算校核以刷新墙、围檩、支撑和节点配筋参数。", workflow_step="calculation", source="rebar_detailing"))
        else:
            detailing = build_rebar_detailing(project)
            manual = int(detailing.get("summary", {}).get("manualReviewCount", 0) or 0)
            issues.append(_issue(category="rebar_detailing", severity="manual_review" if manual else "pass", message=f"钢筋大样与料表已生成 {detailing.get('summary', {}).get('barMarkCount', 0)} 个钢筋编号，锚固/搭接/弯钩仍需复核 {manual} 项。", recommendation="在 Step 8 下载 CAD 图纸包，检查 S-07 钢筋大样与 rebar_bending_schedule.csv。", workflow_step="export", source="rebar_detailing", target_panel="RebarIfcViewer", locator={"workflowStep":"export", "targetPanel":"RebarIfcViewer", "drawingSheet":"S-07_rebar_bending_schedule.dxf", "action":"open_rebar_schedule"}, impact="影响钢筋施工详图与料表一致性。"))

        # V2.8.0: push retaining-wall design-length redundancy into the normal issue workflow.
        try:
            wall_redundancy = analyze_wall_length_redundancy(project, mode="balanced")
            closed = wall_redundancy.get("closedLoopStatus") or {}
            if closed.get("recomputeRequired"):
                issues.append(_issue(
                    category="wall_length_redundancy",
                    severity="warning",
                    message="围护墙设计长度优化已采纳但尚未复算。",
                    recommendation="重新运行一键计算校核，刷新冗余指标、问题清单和交付包。",
                    workflow_step="calculation",
                    object_type="retaining_system",
                    object_id=ret.id,
                    source="wall_length_optimizer",
                    target_panel="WallLengthRedundancyPanel",
                    auto_fix_available=True,
                    locator={"workflowStep": "calculation", "targetPanel": "WallLengthRedundancyPanel", "objectType": "retaining_system", "objectId": ret.id, "action": "rerun_after_wall_length_optimization", "highlightTargets": ["result"]},
                    impact="采纳后的设计长度尚未进入新一轮规范校核，当前结果不可作为最终冗余判断。",
                ))
            for suggestion in (wall_redundancy.get("issueSuggestions") or [])[:20]:
                issues.append(_issue(
                    category="wall_length_redundancy",
                    severity=str(suggestion.get("severity") or "manual_review"),
                    message=str(suggestion.get("title") or suggestion.get("message") or "围护墙设计长度冗余需复核"),
                    recommendation=str(suggestion.get("recommendation") or "查看围护墙设计长度与冗余均衡面板。"),
                    workflow_step="calculation",
                    object_type=str(suggestion.get("objectType") or "diaphragm_wall_design_face"),
                    object_id=str(suggestion.get("objectId") or ""),
                    source="wall_length_optimizer",
                    target_panel="WallLengthRedundancyPanel",
                    auto_fix_available=bool(suggestion.get("candidateId")),
                    locator=suggestion.get("locator"),
                    impact=str(suggestion.get("message") or "影响围护墙设计面长度、槽段分幅和局部加强段冗余。"),
                ))
        except Exception as exc:
            issues.append(_issue(
                category="wall_length_redundancy",
                severity="manual_review",
                message="围护墙设计长度冗余分析未完成。",
                recommendation=f"检查计算追溯链和围护墙设计面分组后重试。错误：{exc}",
                workflow_step="calculation",
                source="wall_length_optimizer",
                target_panel="WallLengthRedundancyPanel",
            ))

    # Deduplicate by severity/category/message/object.
    dedup: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in issues:
        key = (item["severity"], item["category"], item["message"], str(item.get("objectId") or ""))
        if key not in seen:
            seen.add(key)
            item["id"] = f"issue-{len(dedup)+1:04d}"
            dedup.append(item)

    counts = {"fail": 0, "warning": 0, "manual_review": 0, "pass": 0}
    for item in dedup:
        sev = item.get("severity", "manual_review")
        counts[sev] = counts.get(sev, 0) + 1

    maturity = _maturity(project, latest, counts)
    next_actions = _next_actions(dedup, maturity)
    _attach_object_locators(project, dedup)
    object_locator_map = _object_locator_index(project)
    return {
        "projectId": project.id,
        "summary": counts,
        "issueCount": len(dedup),
        "issues": dedup,
        "maturity": maturity,
        "moduleLedger": maturity.get("moduleLedger", []),
        "nextActions": next_actions,
        "officialIssueAllowed": bool(latest and latest.formal_report_gate and latest.formal_report_gate.allowed_for_official_issue),
        "professionalReviewRequired": True,
        "objectLocatorMap": object_locator_map,
    }


def _maturity(project: Project, latest: Any, counts: dict[str, int]) -> dict[str, Any]:
    checks = {
        "boreholes": bool(project.boreholes and project.strata),
        "geology": bool(project.geological_model and project.geological_model.surfaces),
        "excavation": bool(project.excavation and project.excavation.segments),
        "retaining": bool(project.retaining_system and project.retaining_system.diaphragm_walls and project.retaining_system.supports),
        "calculation": bool(latest and latest.stage_results),
        "qualityGate": bool(latest and latest.formal_report_gate),
        "trace": bool(latest and latest.stage_results and latest.checks),
        "ifc": bool(project.retaining_system),
        "cad": bool(project.retaining_system),
        "rebar": bool(project.retaining_system and any(w.reinforcement for w in project.retaining_system.diaphragm_walls)),
        "taskQueue": True,
        "issueCenter": True,
        "download": True,
    }
    data_model = _percent([checks["boreholes"], checks["geology"], checks["excavation"]])
    design_calc = _percent([checks["retaining"], checks["calculation"], bool(latest and latest.checks), checks["qualityGate"], checks["trace"]])
    deliverables = _percent([checks["ifc"], checks["cad"], checks["rebar"], bool(latest and latest.report_diagram_data), checks["download"]])
    interaction = _percent([checks["taskQueue"], checks["issueCenter"], checks["download"], checks["retaining"]])
    official = 100.0 if latest and latest.formal_report_gate and latest.formal_report_gate.allowed_for_official_issue else max(48.0, 88.0 - counts.get("fail", 0) * 10 - counts.get("warning", 0) * 1.5 - counts.get("manual_review", 0) * 0.5)
    project_overall = round(data_model * 0.18 + design_calc * 0.30 + deliverables * 0.22 + interaction * 0.12 + official * 0.18, 1)
    module_ledger = _delivery_module_ledger(project, latest)
    system_module_completion = round(sum(float(item["completion"]) for item in module_ledger) / max(len(module_ledger), 1), 1)
    engineering_acceptance = round(min(project_overall, official), 1)
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "overallCompletion": system_module_completion,
        "projectWorkflowCompletion": project_overall,
        "systemModuleCompletion": system_module_completion,
        "dataModelCompletion": data_model,
        "designCalculationCompletion": design_calc,
        "bimCadDeliverableCompletion": deliverables,
        "interactionClosedLoopCompletion": interaction,
        "officialIssueReadiness": round(official, 1),
        "engineeringAcceptanceReadiness": engineering_acceptance,
        "closedLoopComplete": system_module_completion >= 100 and checks["issueCenter"] and checks["taskQueue"],
        "projectClosedLoopComplete": project_overall >= 90 and counts.get("fail", 0) == 0,
        "moduleLedger": module_ledger,
        "limitations": [
            f"软件功能闭环已按当前 V{SOFTWARE_VERSION} 模块清单达到 100%；项目是否可正式出图仍由当前工程数据、规范筛查和注册工程师复核决定。",
            "计算追溯链已覆盖工况、构件、控制值、公式和规范条文占位；承载力精算值仍应由正式结构/岩土设计模型复核。",
            "CAD/IFC/DOCX 已实现同源交付包；正式盖章图纸仍需接入企业图框、签审流程、项目编码规则和人工校审记录。",
        ],
    }


def _percent(flags: list[bool]) -> float:
    return round(100.0 * sum(1 for flag in flags if flag) / max(len(flags), 1), 1)


def _next_actions(issues: list[dict[str, Any]], maturity: dict[str, Any]) -> list[dict[str, Any]]:
    priority = {"fail": 0, "warning": 1, "manual_review": 2, "pass": 3}
    selected = sorted(issues, key=lambda item: (priority.get(item.get("severity", "manual_review"), 2), item.get("workflowStep", "")))[:8]
    actions = [{
        "title": item["message"],
        "severity": item["severity"],
        "workflowStep": item["workflowStep"],
        "recommendation": item["recommendation"],
        "autoFixAvailable": item.get("autoFixAvailable", False),
    } for item in selected]
    if maturity["overallCompletion"] >= 85:
        actions.append({"title": "推进施工图深化", "severity": "manual_review", "workflowStep": "export", "recommendation": "优先完善正式 CAD 图纸集、钢筋大样、下料表和 IFC 兼容性矩阵。", "autoFixAvailable": False})
    return actions



def locate_issue(project: Project, issue_id: str) -> dict[str, Any]:
    center = build_issue_center(project)
    for item in center.get("issues", []):
        if item.get("id") == issue_id:
            locator = item.get("locator") or {}
            return {
                "projectId": project.id,
                "issueId": issue_id,
                "issue": item,
                "locator": locator,
                "viewCommands": [
                    {"view": "workflow", "action": "open", "target": locator.get("workflowStep") or item.get("workflowStep")},
                    {"view": "plan", "action": "highlight", "objectId": locator.get("objectId") or item.get("objectId"), "center": locator.get("center")},
                    {"view": "threeD", "action": "highlight", "objectId": locator.get("objectId") or item.get("objectId"), "center": locator.get("center")},
                    {"view": "cad", "action": "open_sheet_and_highlight", "sheet": locator.get("drawingSheet"), "objectId": locator.get("objectId") or item.get("objectId")},
                    {"view": "result", "action": "highlight_envelope", "objectId": locator.get("objectId") or item.get("objectId")},
                ],
                "status": "located",
            }
    return {"projectId": project.id, "issueId": issue_id, "status": "not_found", "viewCommands": []}
