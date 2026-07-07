from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt, Inches

from app.schemas.domain import Project
from app.reports.charts import generate_report_charts

SOFTWARE_VERSION = "2.0.9"

DISCLAIMER = (
    "本计算书由软件根据输入资料和当前规则库自动生成。当前结果用于方案设计和技术复核辅助，"
    "不应替代注册岩土工程师、结构工程师的专业判断。施工图设计、专家论证和正式报审前，"
    "应由具备相应资质的专业人员复核全部输入参数、计算模型、规范适用性和构造措施。"
)


def _text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _short(value: Any, limit: int = 80) -> str:
    text = _text(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _status_label(value: Any) -> str:
    text = _text(value)
    return {
        "manual_review": "review",
        "preliminary": "prelim",
        "not_applicable": "n/a",
    }.get(text, text)


def _stage_label(value: Any) -> str:
    text = _text(value)
    if text.startswith("stage-") and len(text) > 14:
        return text[:14]
    return text


def _add_table(document: Document, headers: list[str], rows: list[list[Any]], font_size: int = 8) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, header in enumerate(headers):
        hdr[i].text = header
    for row in rows or [["-"] * len(headers)]:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = _text(value)
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                para.paragraph_format.space_after = Pt(0)
                for run in para.runs:
                    run.font.size = Pt(font_size)


def _flatten_checks(project: Project) -> list[dict[str, Any]]:
    if not project.calculation_results:
        return []
    latest = project.calculation_results[-1]
    checks: list[dict[str, Any]] = []
    checks.extend(latest.checks or [])
    for stage in latest.stage_results:
        checks.extend(stage.checks or [])
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in checks:
        key = (
            str(item.get("ruleId")),
            str(item.get("objectId")),
            str(item.get("status")),
            str(item.get("calculatedValue")),
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _conclusion(checks: list[dict[str, Any]]) -> str:
    statuses = {c.get("status") for c in checks}
    if "fail" in statuses:
        return "自动筛查结论：存在 fail 项，当前方案不得直接用于施工图，应调整方案并由专业人员复核。"
    if not checks:
        return "自动筛查结论：未取得可用计算检查结果，须由专业人员复核。"
    pass_count = sum(1 for c in checks if c.get("status") == "pass")
    warning_count = sum(1 for c in checks if c.get("status") == "warning")
    manual_count = sum(1 for c in checks if c.get("status") == "manual_review")
    if warning_count:
        return "自动筛查结论：未发现 fail 项，但存在 warning/manual_review 项，可作为方案比选输入，须完成专业复核后方可用于正式设计。"
    if manual_count and pass_count:
        return "自动筛查结论：已实现的规则库子集未发现 fail 项，自动设计在当前子集范围内可通过；manual_review 项仍须由注册岩土/结构工程师复核。"
    if manual_count:
        return "自动筛查结论：结果以 manual_review 为主，软件未取得足够条件形成通过性结论，须由专业人员复核。"
    return "自动筛查结论：软件子集检查均为 pass；该结论仍不替代注册岩土/结构工程师的最终复核。"


def export_docx_report(project: Project, output_dir: str | Path) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{project.id}_calculation_report.docx"
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)
    for section in doc.sections:
        section.left_margin = Inches(0.55)
        section.right_margin = Inches(0.55)
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)

    latest = project.calculation_results[-1] if project.calculation_results else None
    checks = _flatten_checks(project)
    summary = Counter(str(c.get("status")) for c in checks)
    try:
        report_charts = generate_report_charts(project, out_dir)
    except Exception:
        report_charts = []
    support_plan_chart = next((c for c in report_charts if c.get("title") == "支撑布置评分平面图"), None)
    candidate_score_chart = next((c for c in report_charts if c.get("title") == "支撑优化候选方案评分图"), None)
    candidate_plan_chart = next((c for c in report_charts if c.get("title") == "支撑优化候选方案平面比选图"), None)

    doc.add_heading("PitGuard BIM Designer 基坑围护结构计算书", level=0)
    doc.add_paragraph(f"软件版本：{SOFTWARE_VERSION}")
    doc.add_paragraph(f"项目名称：{project.name}")
    doc.add_paragraph(f"项目地点：{project.location or '-'}")
    doc.add_paragraph(f"计算日期：{datetime.now().isoformat(timespec='seconds')}")
    doc.add_paragraph("专业复核要求：需由注册岩土/结构工程师复核。")
    doc.add_paragraph(_conclusion(checks))

    doc.add_heading("0 正式化检查与出图质量闸门", level=1)
    if latest and latest.formal_report_gate:
        gate = latest.formal_report_gate
        doc.add_paragraph(f"正式出图闸门：{gate.status}；是否允许正式出图：{'是' if gate.allowed_for_official_issue else '否'}。")
        doc.add_paragraph(gate.headline)
        _add_table(doc, ["类别", "数量"], [
            ["计算 fail", (latest.check_summary or {}).get("fail", 0)],
            ["计算 warning", (latest.check_summary or {}).get("warning", 0)],
            ["人工复核", (latest.check_summary or {}).get("manualReview", (latest.check_summary or {}).get("manual_review", 0))],
            ["支撑布置状态", latest.support_layout_quality.status if latest.support_layout_quality else "missing"],
            ["支撑布置评分", latest.support_layout_quality.score if latest.support_layout_quality else "-"],
            ["IFC 兼容性状态", latest.ifc_compatibility.status if latest.ifc_compatibility else "missing"],
            ["IFC 兼容性评分", latest.ifc_compatibility.score if latest.ifc_compatibility else "-"],
            ["阻断项", len(gate.blocking_items)],
            ["警告项", len(gate.warning_items)],
            ["缺项", len(gate.missing_items)],
        ])
        gate_rows = [["阻断", i.category, i.severity, i.message, i.recommendation] for i in gate.blocking_items[:10]]
        gate_rows += [["警告", i.category, i.severity, i.message, i.recommendation] for i in gate.warning_items[:12]]
        gate_rows += [["缺项", i.category, i.severity, i.message, i.recommendation] for i in gate.missing_items[:8]]
        _add_table(doc, ["类型", "类别", "等级", "说明", "建议"], gate_rows or [["-", "-", "pass", "未发现正式化检查阻断项。", "-"]], font_size=7)
        doc.add_heading("0.0 审图式首页清单", level=2)
        checklist_rows = []
        for section in gate.checklist_sections:
            counts = section.get("counts", {}) if isinstance(section, dict) else {}
            checklist_rows.append([
                section.get("title", "-") if isinstance(section, dict) else "-",
                section.get("status", "-") if isinstance(section, dict) else "-",
                counts.get("fail", 0), counts.get("warning", 0), counts.get("manual_review", 0), counts.get("pass", 0),
            ])
        _add_table(doc, ["清单项", "状态", "Fail", "Warning", "人工复核", "Pass"], checklist_rows or [["-", "pass", 0, 0, 0, 0]], font_size=8)
        doc.add_paragraph("说明：该首页清单用于导出前自查。存在阻断项时不得作为正式施工图提交；存在 warning/manual_review 时应逐项复核并补充说明。")
    else:
        doc.add_paragraph("尚未形成正式化检查结果。")
    if latest and latest.support_layout_quality:
        q = latest.support_layout_quality
        doc.add_heading("0.1 支撑布置合理性评分", level=2)
        doc.add_paragraph(q.summary)
        if latest.support_layout_repair:
            doc.add_paragraph(latest.support_layout_repair.summary)
            _add_table(doc, ["自动修复动作", "说明"], [[a.get("action"), a.get("description") or a.get("counts") or "-"] for a in latest.support_layout_repair.actions[:8]], font_size=7)
            if latest.support_layout_repair.candidates:
                _add_table(doc, ["排名", "候选方案", "评分", "目标分仓/m", "立柱服务跨/m", "支撑数", "立柱数", "硬约束", "导出状态"], [[c.rank, c.id, c.score, c.target_spacing, c.column_max_span, c.support_count, c.column_count, "满足" if c.hard_constraints.get("passed") else "未满足", "可导出" if c.export_readiness.get("ifcReady") else "需修复"] for c in latest.support_layout_repair.candidates[:5]], font_size=7)
            full_compare = list(getattr(latest.support_layout_repair, "candidate_full_calculations", []) or [])
            if not full_compare and latest.report_diagram_data:
                full_compare = list(latest.report_diagram_data.get("candidateFullCalculationComparison") or [])
            if full_compare:
                doc.add_heading("0.1.1 方案 A/B/C 完整计算比选", level=2)
                doc.add_paragraph("前 3 个候选方案已分别重建支撑体系并运行完整计算链路。下表不再使用轴力代理项排序，而是汇总支撑轴力、墙体位移、围檩内力、稳定性、IFC 风险和正式化闸门状态，用于方案阶段决策。")
                _add_table(doc, ["方案", "候选", "支撑数", "立柱数", "最大轴力/kN", "最大位移/mm", "围檩弯矩", "围檩剪力", "最小稳定系数", "IFC风险", "正式闸门"], [[
                    item.get("schemeLabel", "-"),
                    _short(item.get("candidateId", "-"), 22),
                    item.get("supportCount", "-"),
                    item.get("columnCount", "-"),
                    item.get("maxSupportAxialForce", "-"),
                    item.get("maxDisplacement", "-"),
                    item.get("maxWaleMoment", "-"),
                    item.get("maxWaleShear", "-"),
                    item.get("minStabilitySafetyFactor", "-"),
                    item.get("ifcRisk", item.get("ifcStatus", "-")),
                    f"{item.get('formalGateStatus', '-')} / {'允许' if item.get('formalGateAllowed') else '不允许'}",
                ] for item in full_compare[:3]], font_size=7)
                _add_table(doc, ["方案", "强度", "刚度", "稳定", "综合状态", "说明"], [[
                    item.get("schemeLabel", "-"),
                    item.get("strengthStatus", "-"),
                    item.get("stiffnessStatus", "-"),
                    item.get("stabilityStatus", "-"),
                    item.get("governingCheckStatus", "-"),
                    _short(item.get("note") or item.get("error") or "-", 90),
                ] for item in full_compare[:3]], font_size=7)
            if candidate_score_chart:
                doc.add_paragraph("支撑优化候选方案评分图：用于比较候选方案的综合评分。")
                try:
                    doc.add_picture(candidate_score_chart["path"], width=Inches(5.8))
                except Exception as exc:
                    doc.add_paragraph(f"无法插入候选方案评分图：{exc}")
            if candidate_plan_chart:
                doc.add_paragraph("支撑优化候选方案平面比选图：虚线/加粗线表示线位调整较明显的支撑。")
                try:
                    doc.add_picture(candidate_plan_chart["path"], width=Inches(5.8))
                except Exception as exc:
                    doc.add_paragraph(f"无法插入候选方案平面比选图：{exc}")
        if support_plan_chart:
            doc.add_paragraph("支撑布置评分平面图：显示支撑间距、交叉检查、障碍避让和立柱服务范围。")
            try:
                doc.add_picture(support_plan_chart["path"], width=Inches(5.8))
            except Exception as exc:
                doc.add_paragraph(f"无法插入支撑布置评分平面图：{exc}")
        _add_table(doc, ["指标", "数值"], [[k, v] for k, v in q.metrics.items()], font_size=8)
        _add_table(doc, ["类别", "等级", "对象", "说明", "建议"], [[i.category, i.severity, i.object_id or '-', i.message, i.recommendation] for i in q.issues[:20]] or [["-", "pass", "-", "未发现主要支撑布置问题。", "-"]], font_size=7)
    if latest and latest.ifc_compatibility:
        q = latest.ifc_compatibility
        doc.add_heading("0.2 IFC 兼容性自检", level=2)
        doc.add_paragraph(q.summary)
        _add_table(doc, ["指标", "数值"], [
            ["raw unicode", q.raw_unicode_found],
            ["zero dimensions", q.zero_dimension_count],
            ["invalid placement", q.invalid_placement_count],
            ["missing material association", q.missing_material_association_count],
            ["missing spatial containment", q.missing_spatial_containment_count],
        ], font_size=8)
        _add_table(doc, ["实体", "数量"], [[k, v] for k, v in q.entity_counts.items()], font_size=8)
        if q.viewer_profiles:
            _add_table(doc, ["Viewer", "状态", "风险", "评分", "风险项", "建议"], [[p.viewer, p.status, p.risk_level, p.score, "；".join(p.risk_items) or "-", p.recommendation] for p in q.viewer_profiles], font_size=7)
    if latest and latest.design_iteration_summary:
        doc.add_heading("0 V2.0 空间杆系耦合、稳定专项与施工图表达迭代摘要", level=1)
        _add_table(doc, ["方向", "是否实现", "说明"], [
            ["全局联立刚度", latest.design_iteration_summary.get("p6GlobalCoupledMatrix"), "墙体节点水平位移、围檩节点水平位移、支撑轴向弹簧和阶段激活/失活统一进入全局矩阵。"],
            ["计算书图表化", latest.design_iteration_summary.get("p7ReportCharts"), "自动生成墙体压力/位移/弯矩/剪力、围檩包络、支撑轴力和校核统计图。"],
            ["CAD 几何内核", latest.design_iteration_summary.get("p8CadGeometryKernel"), "多段线 offset、倒角/圆角、修复、自交检查、DXF 图层语义、捕捉和坐标批量输入接口。"],
            ["地下水与稳定专项", latest.design_iteration_summary.get("p9GroundwaterStabilitySpecials"), "承压水突涌、降水水位差、分层渗透系数、软弱下卧层、整体稳定搜索接口。"],
            ["强度/刚度/稳定性复核", latest.design_iteration_summary.get("p10DesignReviewSummary"), "按强度、刚度、稳定性归类汇总 pass/warning/fail 和控制指标。"],
            ["空间杆系内核", latest.design_iteration_summary.get("p11SpatialFrameKernel"), "墙体/围檩转角自由度、支撑空间方向刚度、立柱竖向自由度、节点刚域和楼板换撑刚度。"],
            ["可审查稳定专项", latest.design_iteration_summary.get("p12ReviewableStabilityPackage"), "控制剖面、圆弧滑动候选、渗流路径、降水过程、井点和加固方案。"],
            ["施工图表达", latest.design_iteration_summary.get("p13ConstructionDrawingOutput"), "支撑平面、围檩节点、钢筋笼、立柱桩详图 SVG 输出接口。"],
            ["详细 IFC", latest.design_iteration_summary.get("p14DetailedIfcOutput"), "详细钢筋、承压板、预埋件、施工阶段和标准属性集扩展。"],
            ["支撑布置评分", latest.design_iteration_summary.get("p15SupportLayoutQualityGate"), "自动检查支撑间距、跨长、角撑、立柱、障碍物、出土口和换撑路径。"],
            ["候选 A/B/C 完整计算", latest.design_iteration_summary.get("p21CandidateAbcFullCalculationComparison"), "对前 3 个候选方案分别运行施工阶段、轴力、墙体位移、围檩内力、稳定性、IFC 和正式化闸门。"],
            ["IFC 兼容性", latest.design_iteration_summary.get("p16IfcCompatibilityPrecheck"), "导出前检查 raw unicode、零尺寸、placement、材料关联和空间归属。"],
            ["正式化闸门", latest.design_iteration_summary.get("p17FormalReportGate"), "将 fail/warning/manual_review、支撑布置、稳定专项和 IFC 风险放到计算书首页。"],
            ["边界", "-", latest.design_iteration_summary.get("remainingBoundary")],
        ])
        if latest.design_review_summary:
            r = latest.design_review_summary
            _add_table(doc, ["复核类别", "状态", "Fail", "Warning", "控制指标"], [
                ["强度", r.strength_status, r.strength_fail_count, r.strength_warning_count, r.max_strength_utilization],
                ["刚度", r.stiffness_status, r.stiffness_fail_count, r.stiffness_warning_count, r.max_stiffness_utilization],
                ["稳定性", r.stability_status, r.stability_fail_count, r.stability_warning_count, r.min_stability_safety_factor],
            ])

    doc.add_heading("1 工程概况", level=1)
    doc.add_paragraph(f"项目 ID：{project.id}")
    doc.add_paragraph(
        f"坐标系：{project.coordinate_system.type}，原点=({project.coordinate_system.origin_x}, "
        f"{project.coordinate_system.origin_y}, {project.coordinate_system.origin_z})。"
    )
    doc.add_paragraph(
        f"安全等级：{project.design_settings.safety_grade}；环境控制：{project.design_settings.environment_grade}；"
        f"地下水位：{project.design_settings.groundwater_level} m；地面超载：{project.design_settings.surcharge} kPa。"
    )

    doc.add_heading("2 设计依据与规则库", level=1)
    rows = [
        ["JGJ120-2012 建筑基坑支护技术规程", "水平荷载、土/水压力、弹性地基梁、嵌固、抗隆起、抗渗流筛查子集", "部分实现；适用条件需复核"],
        ["GB/T50010-2010（2024局部修订）混凝土结构设计标准", "矩形截面受弯、受剪、轴压和最小配筋率筛查子集", "部分实现；裂缝、锚固、节点、构造需复核"],
        ["GB55008-2021 混凝土结构通用规范", "强制性工程建设规范约束提示、混凝土构件承载能力/正常使用复核入口", "通用规范优先；未覆盖条款需专业复核"],
        ["GB55003-2021 建筑与市政地基基础通用规范", "地基基础、基坑支护、环境安全与地下水控制通用要求提示", "场地与监测参数不足时需专业复核"],
        ["GB50009-2012 建筑结构荷载规范", "永久/可变作用组合参数记录", "软件默认组合，不替代正式组合"],
        ["GB50007-2011 建筑地基基础设计规范", "基坑工程要求提示、立柱基础承载力接口", "立柱基础按输入参数筛查，基础详勘参数需复核"],
        ["GB50017-2017 钢结构设计标准", "钢管支撑轴压强度/稳定筛查接口", "长细比和节点需复核"],
    ]
    _add_table(doc, ["标准/规则", "本软件实现范围", "结论边界"], rows)
    doc.add_paragraph("说明：本计算书明确区分“规则库子集筛查”和“正式规范验算”。缺少适用条件、项目参数或未覆盖公式的项目均进入专业复核范围。")

    doc.add_heading("3 地质资料", level=1)
    doc.add_paragraph(f"输入钻孔数量：{len(project.boreholes)}；地层参数数量：{len(project.strata)}。")
    _add_table(doc, ["地层编号", "名称", "重度(kN/m3)", "饱和重度", "c(kPa)", "phi(deg)", "E(MPa)", "m(kN/m3)"], [
        [
            s.code,
            s.name,
            s.parameters.unit_weight,
            s.parameters.saturated_unit_weight,
            s.parameters.cohesion,
            s.parameters.friction_angle,
            s.parameters.elastic_modulus,
            s.parameters.horizontal_subgrade_modulus,
        ]
        for s in project.strata
    ])
    if project.geological_model:
        doc.add_paragraph(f"地质模型界面数量：{len(project.geological_model.surfaces)}；VTU 网格：{'已导入' if project.geological_model.vtu_mesh else '未导入'}。")
        for warning in project.geological_model.warnings[:8]:
            doc.add_paragraph(f"地质 warning：{warning}")

    doc.add_heading("4 基坑轮廓和开挖深度", level=1)
    if project.excavation:
        doc.add_paragraph(
            f"坑顶标高：{project.excavation.top_elevation} m；坑底标高：{project.excavation.bottom_elevation} m；"
            f"开挖深度：{project.excavation.depth} m。"
        )
        doc.add_paragraph(f"面积：{project.excavation.area} m2；周长：{project.excavation.perimeter} m；边段数：{len(project.excavation.segments)}。")
        _add_table(doc, ["边段", "长度(m)", "起点", "终点", "外法向"], [
            [s.name, s.length, f"({s.start.x},{s.start.y})", f"({s.end.x},{s.end.y})", f"({s.outward_normal.x},{s.outward_normal.y})"]
            for s in project.excavation.segments
        ])
    else:
        doc.add_paragraph("未定义基坑。")

    doc.add_heading("5 围护结构设计结果", level=1)
    retaining = project.retaining_system
    if retaining and retaining.diaphragm_walls:
        _add_table(doc, ["墙编号", "边段", "厚度(m)", "墙顶", "墙底", "混凝土", "钢筋", "状态"], [
            [w.panel_code, w.segment_id, w.thickness, w.top_elevation, w.bottom_elevation, w.concrete_grade, w.rebar_grade, _status_label(w.design_results.check_status) if w.design_results else "review"]
            for w in retaining.diaphragm_walls
        ])
        doc.add_paragraph("地下连续墙初选结果已按 JGJ120 构造筛查；最终墙深应通过嵌固、整体稳定、坑底隆起、抗渗流和变形控制综合确定。")
    else:
        doc.add_paragraph("未生成地连墙。")

    doc.add_heading("6 支撑体系设计结果", level=1)
    supports = retaining.supports if retaining else []
    _add_table(doc, ["编号", "层号", "标高(m)", "截面", "材料", "设计轴力(kN)", "配筋组"], [
        [s.code, s.level_index, s.elevation, s.section.name, s.material.grade, s.design_axial_force, len(s.reinforcement)] for s in supports
    ])
    doc.add_paragraph("表中支撑轴力为包络设计值；标准值、影响范围和分项/重要性系数见第9节。节点、长细比、偏心、温度和施工误差需复核。")

    doc.add_heading("7 计算模型和公式子集", level=1)
    formula_rows = [
        ["主动/被动土压力", "Ka=tan^2(45-phi/2), Kp=tan^2(45+phi/2); pa=(sigma-u)Ka-2c sqrt(Ka)+u; pp=(sigma-u)Kp+2c sqrt(Kp)+u", "JGJ120 水土分算/朗肯子集"],
        ["水压力", "u=gamma_w*(z_w-z)", "静止地下水压力子集"],
        ["墙体内力", "EI*y'''' + k_s*y + sum(k_i*y_i)=q(z)", "一维弹性地基梁有限差分"],
        ["正截面受弯", "M <= alpha1*fc*b*x*(h0-x/2), alpha1*fc*b*x=fy*As", "GB/T50010 单筋矩形截面子集"],
        ["受剪筛查", "V <= 0.7*ft*b*h0（箍筋贡献另需详设）", "GB/T50010 斜截面简化筛查"],
        ["围檩连续梁", "EI*w\'\'\'\' + k_i*w_i=q；R_i=k_i*w_i；N_i=R_i/cos(theta)", "围檩弯矩/剪力/挠度和支撑节点反力子集"],
        ["支撑施工效应", "N_eff=N_wale+0.5N_preload+N_temperature+N_gap；M_e=N*e0", "预加轴力、温度、间隙和偏心筛查"],
        ["抗隆起", "K=(c*Nc+gamma_eff*D*Nq)/(gamma*H+q); phi=0 -> Nc=5.14,Nq=1", "JGJ120 抗隆起概念筛查"],
        ["抗渗流", "K=gamma_eff*D/(gamma_w*Delta h)", "地下水稳定简化筛查"],
    ]
    _add_table(doc, ["项目", "软件公式", "实现边界"], formula_rows)

    doc.add_heading("7.1 计算书图表", level=2)
    charts = [c for c in report_charts if c.get("title") not in {"支撑布置评分平面图", "支撑优化候选方案评分图", "支撑优化候选方案平面比选图"}]
    for chart in charts:
        doc.add_paragraph(chart.get("title", "图表"))
        try:
            doc.add_picture(chart["path"], width=Inches(5.8))
        except Exception as exc:
            doc.add_paragraph(f"无法插入图表 {chart.get('path')}: {exc}")

    doc.add_heading("8 土压力、水压力与内力结果", level=1)
    if latest:
        gv = latest.governing_values
        doc.add_paragraph(f"最大合成侧向压力：{gv.max_total_pressure} kPa。")
        doc.add_paragraph(f"最大墙弯矩：{gv.max_wall_moment} kN*m/m；最大墙剪力：{gv.max_wall_shear} kN/m；最大位移：{gv.max_displacement} mm。")
        doc.add_paragraph(
            f"嵌固安全系数最小值：{_text(gv.embedment_safety_factor_min)}；"
            f"抗隆起安全系数最小值：{_text(gv.heave_safety_factor_min)}；"
            f"抗渗安全系数最小值：{_text(gv.seepage_safety_factor_min)}。"
        )
        _add_table(doc, ["阶段", "边段", "支撑数", "最大压力(kPa)", "墙最大弯矩", "墙位移(mm)", "校核项数"], [
            [
                _stage_label(sr.stage_id),
                sr.segment_id,
                len(sr.support_forces),
                max((p.total_pressure for p in sr.pressure_profile.points), default=0.0),
                sr.wall_internal_force.max_moment if sr.wall_internal_force else sr.wall_internal_force_placeholder.get("maxMoment"),
                sr.wall_internal_force.max_displacement if sr.wall_internal_force else sr.wall_internal_force_placeholder.get("maxDisplacement"),
                len(sr.checks),
            ]
            for sr in latest.stage_results[:30]
        ])
    else:
        doc.add_paragraph("尚未运行计算。")

    doc.add_heading("9 支撑轴力估算", level=1)
    if latest:
        doc.add_paragraph(f"最大支撑轴力设计值估算：{latest.governing_values.max_support_axial_force} kN；标准值、设计值、gamma0 和 factor 见下表。")
        rows2: list[list[Any]] = []
        for sr in latest.stage_results:
            for f in sr.support_forces:
                rows2.append([_stage_label(sr.stage_id), sr.segment_id, f.level_index, f.elevation, f.tributary_top, f.tributary_bottom, f.axial_force, f.axial_force_design, f.importance_factor, f.partial_factor])
        _add_table(doc, ["阶段", "边段", "层号", "标高", "影响上界", "影响下界", "标准轴力(kN)", "设计轴力(kN)", "gamma0", "factor"], rows2[:28])
        if len(rows2) > 28:
            doc.add_paragraph(f"其余 {len(rows2) - 28} 条支撑轴力记录已写入 JSON 导出文件，计算书仅列出前 28 条代表性记录。")
        rows2b: list[list[Any]] = []
        for sr in latest.stage_results:
            for f in sr.support_forces:
                rows2b.append([_stage_label(sr.stage_id), f.face_code, f.wale_beam_code, f.wale_chainage, f.continuous_beam_reaction, f.elastic_support_stiffness, f.preload_effect, f.thermal_effect, f.gap_effect, f.effective_axial_force])
        _add_table(doc, ["阶段", "墙面", "围檩", "里程", "节点反力", "弹簧刚度", "预加轴力", "温度效应", "间隙效应", "有效标准轴力"], rows2b[:28])
    else:
        doc.add_paragraph("尚未运行计算。")

    doc.add_heading("10 围檩连续梁内力、截面设计和配筋", level=1)
    if retaining and retaining.wale_beams:
        doc.add_paragraph("围檩按连续梁计算本体弯矩、剪力和挠度；支撑节点作为弹性支座。下表为围檩包络和 GB/T50010 子集配筋结果。")
        _add_table(doc, ["围檩", "层号", "墙面", "Mmax", "Vmax", "挠度", "Md", "Vd", "As_req", "As_prov", "配筋", "状态"], [
            [
                beam.code,
                beam.support_level,
                beam.design_result.face_code if beam.design_result else "-",
                beam.design_result.max_moment if beam.design_result else "-",
                beam.design_result.max_shear if beam.design_result else "-",
                beam.design_result.max_deflection if beam.design_result else "-",
                beam.design_result.max_moment_design if beam.design_result else "-",
                beam.design_result.max_shear_design if beam.design_result else "-",
                beam.design_result.required_reinforcement_area if beam.design_result else "-",
                beam.design_result.provided_reinforcement_area if beam.design_result else "-",
                f"D{beam.design_result.main_bar_diameter}@{beam.design_result.main_bar_spacing}" if beam.design_result else "-",
                _status_label(beam.design_result.check_status) if beam.design_result else "review",
            ]
            for beam in retaining.wale_beams if beam.design_result
        ][:80])
        _add_table(doc, ["围檩", "附加筋协调说明"], [
            [beam.code, beam.design_result.node_additional_reinforcement_note if beam.design_result else "-"]
            for beam in retaining.wale_beams if beam.design_result
        ][:40])
    else:
        doc.add_paragraph("尚未形成围檩连续梁设计结果。")

    doc.add_heading("11 配筋建议", level=1)
    if retaining:
        rows3: list[list[Any]] = []
        for wall in retaining.diaphragm_walls:
            for r in wall.reinforcement:
                rows3.append([wall.panel_code, r.name, r.grade, r.diameter, r.spacing or r.count, r.required_area_per_meter, r.area_per_meter, _status_label(r.check_status)])
        _add_table(doc, ["构件", "配筋组", "等级", "直径", "间距/根数", "As_req", "As_prov", "状态"], rows3[:80])
    doc.add_paragraph("配筋建议仅为方案阶段钢筋组参数化输出，未自动完成裂缝宽度、锚固、搭接、钢筋笼吊装、接头、节点和施工图构造详图。")

    doc.add_heading("12 规范筛查结果", level=1)
    doc.add_paragraph(
        f"检查统计：pass={summary.get('pass', 0)}；fail={summary.get('fail', 0)}；warning={summary.get('warning', 0)}；manual_review={summary.get('manual_review', 0)}。"
    )
    doc.add_paragraph(_conclusion(checks))
    _add_table(doc, ["规则ID", "对象", "状态", "计算/限值", "单位", "说明"], [
        [
            _short(c.get("ruleId"), 34),
            _short(c.get("objectId"), 16),
            _status_label(c.get("status")),
            f"{_text(c.get('calculatedValue'))} / {_text(c.get('limitValue'))}",
            c.get("unit"),
            _short(c.get("message"), 70),
        ]
        for c in checks[:48]
    ])
    if len(checks) > 48:
        doc.add_paragraph(f"其余 {len(checks) - 48} 条校核结果已写入 JSON 导出文件，计算书仅列出前 48 条代表性记录。")

    doc.add_heading("13 墙-围檩-支撑全局联立刚度模型", level=1)
    if latest:
        rows_global = []
        for sr in latest.stage_results[:36]:
            g = sr.global_coupled_result
            if g:
                rows_global.append([_stage_label(sr.stage_id), sr.segment_id, g.face_code, g.matrix_size, g.dof_summary.get("wallHorizontal"), g.dof_summary.get("waleHorizontal"), g.max_wall_displacement, g.max_support_axial_force, g.fallback])
        _add_table(doc, ["阶段", "边段", "墙面", "矩阵阶数", "墙DOF", "围檩DOF", "最大墙位移(m)", "最大支撑轴力(kN)", "退化"], rows_global[:36])
        reaction_rows = []
        for sr in latest.stage_results[:20]:
            g = sr.global_coupled_result
            if not g:
                continue
            for r in g.support_reactions[:8]:
                reaction_rows.append([_stage_label(sr.stage_id), r.support_code, r.endpoint, r.face_code, r.chainage, r.node_reaction, r.axial_force, r.axial_deformation])
        _add_table(doc, ["阶段", "支撑", "端点", "墙面", "里程", "节点反力", "轴力", "轴向变形"], reaction_rows[:48])
    doc.add_paragraph("说明：V2.0 已由平动凝聚矩阵升级为空间杆系代理内核：墙体梁和围檩梁具有转角自由度，支撑按空间方向刚度进入，立柱竖向自由度、节点刚域和楼板换撑刚度均进入全局矩阵。该模型仍为设计辅助内核，正式工程应通过完整三维杆系/FEM和审查意见复核。")

    doc.add_heading("14 墙-围檩-支撑耦合摘要与包络图表数据", level=1)
    if latest:
        coupled_rows = []
        for sr in latest.stage_results[:36]:
            data = sr.coupled_system_result or {}
            if data:
                coupled_rows.append([_stage_label(sr.stage_id), sr.segment_id, data.get("segmentSupportCount"), data.get("waleResultCount"), data.get("wallMaxMoment"), data.get("wallMaxDisplacement"), data.get("maxWaleMoment"), _short(data.get("note"), 60)])
        _add_table(doc, ["阶段", "边段", "支撑数", "围檩结果数", "墙弯矩", "墙位移", "围檩弯矩", "说明"], coupled_rows[:36])
        envelopes = (latest.report_diagram_data or {}).get("waleEnvelopes", [])
        _add_table(doc, ["围檩", "墙面", "阶段数", "M+", "M-", "|V|max", "|δ|max", "采样点"], [
            [env.get("waleBeamCode"), env.get("faceCode"), len(env.get("governingStageIds", [])), env.get("maxPositiveMoment"), env.get("maxNegativeMoment"), env.get("maxAbsShear"), env.get("maxAbsDeflection"), len(env.get("points", []))]
            for env in envelopes[:30]
        ])
    doc.add_paragraph("说明：本节以表格形式写入可绘图数据。后续正式报告可由这些数据生成弯矩图、剪力图、挠度图和轴力云图。")

    doc.add_heading("15 可审查稳定专项与施工图表达", level=1)
    if latest and latest.stability_detailed_result:
        stab = latest.stability_detailed_result
        doc.add_paragraph(f"控制剖面：{stab.controlling_section_name or stab.controlling_section_id or '-'}；控制模式：{stab.controlling_mode or '-'}；最小安全指标：{_text(stab.min_safety_factor)}。")
        _add_table(doc, ["项目", "数值"], [
            ["坑底隆起系数", stab.heave_factor],
            ["承压水突涌系数", stab.confined_uplift_factor],
            ["抗渗流系数", stab.seepage_factor],
            ["整体稳定系数", stab.overall_stability_factor],
            ["软弱下卧层指标", stab.weak_layer_index],
        ])
        _add_table(doc, ["候选圆弧", "中心X", "中心标高", "半径", "安全系数", "控制"], [
            [x.get("id"), x.get("centerX"), x.get("centerElevation"), x.get("radius"), x.get("safetyFactor"), x.get("governing")]
            for x in stab.circular_slip_surfaces[:8]
        ])
        _add_table(doc, ["渗流路径", "入口水位", "出口标高", "路径长", "水头差", "坡降"], [
            [x.get("id"), x.get("entryElevation"), x.get("exitElevation"), x.get("pathLength"), x.get("headLoss"), x.get("hydraulicGradient")]
            for x in stab.seepage_paths[:6]
        ])
        _add_table(doc, ["降水步", "目标水位", "降深", "持水时间(h)", "监测动作"], [
            [x.get("step"), x.get("targetWaterLevel"), x.get("drawdown"), x.get("recommendedHoldHours"), x.get("monitoringAction")]
            for x in stab.drawdown_process[:8]
        ])
        _add_table(doc, ["井号", "类型", "控制参数", "说明"], [
            [x.get("wellCode"), x.get("type"), x.get("designFlowIndex") or x.get("targetHeadElevation"), x.get("controlMode") or x.get("screenBottomElevation")]
            for x in (stab.dewatering_wells[:8] + stab.depressurization_wells[:6])
        ])
        _add_table(doc, ["加固/优化方案", "说明", "预期效果"], [
            [x.get("option"), x.get("description"), x.get("expectedEffect")] for x in stab.improvement_options
        ])
    if latest and latest.drawing_sheets:
        _add_table(doc, ["图号", "图名", "比例", "类型", "文件"], [
            [sht.sheet_id, sht.title, sht.scale, sht.sheet_type, sht.file_path] for sht in latest.drawing_sheets
        ])
        doc.add_paragraph("施工图表达当前为 SVG 图纸接口，包含支撑平面、围檩节点、钢筋笼和立柱桩详图；正式施工图仍需图框、比例、标注、构造尺寸和审签流程。")

    doc.add_heading("16 风险提示和人工复核要求", level=1)
    warning_list: list[str] = []
    if project.geological_model:
        warning_list += project.geological_model.warnings
    if retaining:
        warning_list += retaining.warnings
    if latest:
        warning_list += latest.warnings
        for sr in latest.stage_results:
            if sr.wall_internal_force:
                warning_list += sr.wall_internal_force.warnings
    for warning in list(dict.fromkeys(warning_list))[:40] or ["无额外 warning，但仍需专业复核。"]:
        doc.add_paragraph(warning)
    doc.add_paragraph(DISCLAIMER)

    doc.add_heading("16 附录：输入参数表", level=1)
    doc.add_paragraph(f"单位系统：length={project.unit_system.length}, force={project.unit_system.force}, stress={project.unit_system.stress}, angle={project.unit_system.angle}")
    doc.add_paragraph(f"规则库：{project.design_settings.rule_set}。")
    doc.add_paragraph("报告结束。")

    doc.save(path)
    return path
