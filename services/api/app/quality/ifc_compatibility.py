from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from app.schemas.domain import IfcCompatibilityCheckResult, IfcViewerProfileRisk, Project, QualityGateIssue

PRODUCT_PATTERNS = (
    "IFCWALL", "IFCBEAM", "IFCCOLUMN", "IFCPLATE", "IFCREINFORCINGBAR", "IFCBUILDINGELEMENTPROXY",
)


def _issue(category: str, severity: str, message: str, object_id: str | None = None, object_type: str | None = None, recommendation: str | None = None) -> QualityGateIssue:
    return QualityGateIssue(category=category, severity=severity, object_id=object_id, object_type=object_type, message=message, recommendation=recommendation)


def _status_from_issues(issues: Iterable[QualityGateIssue]) -> str:
    severities = {i.severity for i in issues}
    if "fail" in severities:
        return "fail"
    if "warning" in severities:
        return "warning"
    if "manual_review" in severities:
        return "manual_review"
    return "pass"



def _profile_status(score: float) -> tuple[str, str]:
    if score < 60:
        return "fail", "high"
    if score < 86:
        return "warning", "medium"
    return "pass", "low"


def _viewer_profiles(counts: dict[str, int], issues: list[QualityGateIssue], *, export_mode: str = "design_detailed", raw_unicode: bool = False, missing_refs: list[str] | None = None, zero_dims: int = 0, invalid_placement: int = 0, missing_material: int = 0, missing_spatial: int = 0) -> list[IfcViewerProfileRisk]:
    """Heuristic viewer risk profiles.

    These profiles are not vendor certifications. They translate common IFC failure
    modes into practical risk levels for typical downstream viewers.
    """
    fail_count = sum(1 for i in issues if i.severity == "fail")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    rebar_count = int(counts.get("IFCREINFORCINGBAR", counts.get("rebarGroups", 0)) or 0)
    plate_count = int(counts.get("IFCPLATE", 0) or 0)
    proxy_count = int(counts.get("IFCBUILDINGELEMENTPROXY", 0) or 0)
    product_count = sum(int(counts.get(k, 0) or 0) for k in PRODUCT_PATTERNS)
    export_mode = str(export_mode or "design_detailed")
    is_light = export_mode == "coordination_light"
    is_analysis = export_mode == "analysis_model"
    is_construction_visual = export_mode == "construction_visual"
    base_items: list[str] = []
    if raw_unicode:
        base_items.append("STEP 文件含原始非 ASCII 文本")
    if missing_refs:
        base_items.append("存在未定义 STEP 引用")
    if zero_dims:
        base_items.append("存在零尺寸实体/截面")
    if invalid_placement:
        base_items.append("缺少或存在无效 placement")
    profiles: list[IfcViewerProfileRisk] = []

    def add(viewer: str, base_score: float, extra_items: list[str], recommendation: str):
        score = max(0.0, min(100.0, base_score - fail_count * 18 - warning_count * 5 - len(extra_items) * 7))
        status, risk = _profile_status(score)
        profiles.append(IfcViewerProfileRisk(viewer=viewer, status=status, risk_level=risk, score=round(score, 1), risk_items=base_items + extra_items, recommendation=recommendation))

    add("BlenderBIM / Bonsai", 96.0, (["详细钢筋较多，打开大模型可能较慢"] if rebar_count > 500 else []), "适合作为开源 IFC 语义核查；优先检查空间归属、属性集和钢筋实体。")
    add("BIMVision", 94.0, (["产品空间归属不完整"] if missing_spatial else []) + (["详细钢筋/承压板实体较多"] if rebar_count + plate_count > 800 else []), "适合轻量可视化；若加载慢，可导出参数化钢筋简化版本。")
    add("Solibri", 90.0, (["材料关联不完整"] if missing_material else []) + (["空间结构归属不完整"] if missing_spatial else []), "适合规则审查；建议保证材料、空间结构和属性集完整。")
    if is_light:
        revit_items = []
        navis_items = []
        revit_base = 90.0
        navis_base = 92.0
        rec = "轻量协调版适合进入 Revit/Navisworks 做协调浏览。"
    elif is_construction_visual:
        revit_items = (["钢筋以可视化代理构件表达，语义钢筋属性保存在 Pset_ReinforcementVisualProxy"] if proxy_count else [])
        navis_items = ["施工图可视化版优先保证几何可见性，钢筋语义需查看属性集"]
        revit_base = 88.0
        navis_base = 90.0
        rec = "施工图可视化版适合解决详细 IFC 在轻量 Viewer 中不可见的问题；正式 BIM 语义审查仍使用 design_detailed。"
    elif is_analysis:
        revit_items = ["包含分析弹簧/荷载/工况代理构件，进入 Revit 时可能需要分类映射"] if proxy_count else []
        navis_items = ["分析模型属性集可见性依赖导入配置"]
        revit_base = 84.0
        navis_base = 88.0
        rec = "分析模型版用于计算模型交换；建议在 BIM Viewer 中主要查看轴线、节点、弹簧、荷载和工况属性。"
    else:
        revit_items = (["IFC4 详细钢筋/承压板可能导入较慢或被简化"] if rebar_count + plate_count > 200 else []) + (["代理构件可能需要族映射"] if proxy_count else [])
        navis_items = (["属性集可见性依赖导入配置"] if product_count > 0 else [])
        if rebar_count > 300:
            navis_items.append("详细钢筋数量较多会影响漫游性能")
        revit_base = 82.0
        navis_base = 86.0
        rec = "施工图版保留钢筋/承压板用于深化审查；如需协调浏览，请改用轻量版。"
    add("Autodesk Revit", revit_base, revit_items, rec)
    add("Navisworks", navis_base, navis_items, rec)
    return profiles

def evaluate_ifc_model_compatibility(project: Project) -> IfcCompatibilityCheckResult:
    """Pre-export model-level IFC readiness check.

    This gate runs before writing STEP.  File-level syntax checks are added by
    validate_ifc_file after export.
    """
    issues: list[QualityGateIssue] = []
    counts: dict[str, int] = {}
    ret = project.retaining_system
    if not ret:
        issues.append(_issue("ifc_model", "fail", "缺少围护结构模型，无法导出有效 IFC。", recommendation="先生成地连墙和支撑体系。"))
        return IfcCompatibilityCheckResult(score=0, status="fail", summary="IFC 导出前检查未通过：缺少围护结构。", entity_counts=counts, issues=issues)
    counts = {
        "diaphragmWalls": len(ret.diaphragm_walls),
        "beams": len(ret.crown_beams) + len(ret.wale_beams) + len(getattr(ret, "ring_beams", [])),
        "supports": len(ret.supports),
        "columns": len(ret.columns),
        "supportNodes": len(getattr(ret, "support_nodes", [])),
        "rebarGroups": sum(len(w.reinforcement) for w in ret.diaphragm_walls) + sum(len(s.reinforcement) for s in ret.supports),
    }
    if not ret.diaphragm_walls:
        issues.append(_issue("ifc_model", "fail", "IFC 中没有地连墙构件。", object_type="DiaphragmWallPanel", recommendation="先执行自动地连墙设计。"))
    if not ret.supports:
        issues.append(_issue("ifc_model", "warning", "IFC 中没有水平支撑构件。", object_type="SupportElement", recommendation="深基坑项目应生成支撑体系或说明采用其他支护形式。"))
    for wall in ret.diaphragm_walls:
        height = wall.top_elevation - wall.bottom_elevation
        if wall.design_length is not None:
            length = wall.design_length
        elif wall.axis.points and len(wall.axis.points) >= 2:
            length = ((wall.axis.points[-1].x - wall.axis.points[0].x) ** 2 + (wall.axis.points[-1].y - wall.axis.points[0].y) ** 2) ** 0.5
        else:
            length = 0.0
        if wall.thickness <= 0 or height <= 0 or length <= 0:
            issues.append(_issue("ifc_geometry", "fail", f"地连墙 {wall.panel_code} 存在零尺寸或负尺寸。", wall.id, "DiaphragmWallPanel", "检查墙厚、墙顶/墙底标高和轴线长度。"))
        if not wall.concrete_grade:
            issues.append(_issue("ifc_material", "warning", f"地连墙 {wall.panel_code} 未设置混凝土等级。", wall.id, "DiaphragmWallPanel"))
    for support in ret.supports:
        span = support.span_length or ((support.end.x-support.start.x)**2+(support.end.y-support.start.y)**2)**0.5
        if span <= 0 or (support.section.width or support.section.diameter or 0) <= 0:
            issues.append(_issue("ifc_geometry", "fail", f"支撑 {support.code} 存在零长度或零截面。", support.id, "SupportElement", "检查支撑端点和截面尺寸。"))
        if not support.material or not support.material.grade:
            issues.append(_issue("ifc_material", "warning", f"支撑 {support.code} 缺少材料等级。", support.id, "SupportElement"))
    if counts["rebarGroups"] == 0:
        issues.append(_issue("ifc_reinforcement", "warning", "未检测到参数化钢筋组，IFC 只能表达混凝土/钢构件几何。", recommendation="完成墙体、围檩、支撑配筋后再导出正式 IFC。"))
    score = max(0.0, 100.0 - sum(25 if i.severity == "fail" else 8 if i.severity == "warning" else 12 for i in issues))
    status = _status_from_issues(issues)
    return IfcCompatibilityCheckResult(score=round(score, 1), status=status, summary=f"IFC 导出前模型检查：{status}，实体准备度评分 {score:.1f}。", export_mode="model_precheck", entity_counts=counts, viewer_profiles=_viewer_profiles(counts, issues, export_mode="model_precheck"), issues=issues)


def validate_ifc_file(path: str | Path, base: IfcCompatibilityCheckResult | None = None) -> IfcCompatibilityCheckResult:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    issues = list(base.issues if base else [])
    counts = dict(base.entity_counts if base else {})
    if not path.exists() or not text.strip():
        issues.append(_issue("ifc_file", "fail", "IFC 文件不存在或为空。", recommendation="重新导出 IFC。"))
        return IfcCompatibilityCheckResult(score=0, status="fail", summary="IFC 文件级自检失败。", file_path=str(path), entity_counts=counts, issues=issues)

    raw_unicode_found = any(ord(ch) > 127 for ch in text)
    if raw_unicode_found:
        issues.append(_issue("ifc_text", "fail", "IFC STEP 文件中仍存在原始非 ASCII 字符，部分 Viewer 可能无法打开。", recommendation="使用 IFC STEP \\X2\\...\\X0\\ 编码输出中文。"))

    defined = set(re.findall(r"#(\d+)=", text))
    referenced = set(re.findall(r"#(\d+)", text))
    missing = sorted(ref for ref in referenced if ref not in defined)[:20]
    if missing:
        issues.append(_issue("ifc_reference", "fail", f"IFC 存在未定义引用：{', '.join(missing[:8])}。", recommendation="检查实体写入顺序和引用关系。"))

    for pattern in PRODUCT_PATTERNS:
        counts[pattern] = len(re.findall(pattern + r"\(", text))
    export_mode = "coordination_light" if "coordination_light" in path.name else "analysis_model" if "analysis_model" in path.name else "construction_visual" if "construction_visual" in path.name else "design_detailed"
    zero_dimensions = len(re.findall(r"IFCRECTANGLEPROFILEDEF\([^\)]*,\s*(?:0\.?|0\.0+)\s*,", text))
    zero_dimensions += len(re.findall(r"IFCEXTRUDEDAREASOLID\([^\)]*,\s*(?:0\.?|0\.0+)\s*\)", text))
    if zero_dimensions:
        issues.append(_issue("ifc_geometry", "fail", f"IFC 检测到 {zero_dimensions} 个零尺寸截面/实体。", recommendation="导出前校核构件长度、截面、拉伸高度。"))
    product_ids = set()
    for pattern in PRODUCT_PATTERNS:
        product_ids.update(re.findall(r"#(\d+)=" + pattern + r"\(", text))
    contained: set[str] = set()
    for rel in re.findall(r"IFCRELCONTAINEDINSPATIALSTRUCTURE\([^;]+;", text, flags=re.S):
        contained.update(re.findall(r"#(\d+)", rel))
    # The relationship itself includes owner/storey references; count only product ids.
    missing_containment = product_ids - contained
    if missing_containment:
        issues.append(_issue("ifc_spatial", "warning", f"{len(missing_containment)} 个产品未明确包含到空间结构关系中。", recommendation="检查 IFCRELContainedInSpatialStructure 关联。"))
    materialized: set[str] = set()
    for rel in re.findall(r"IFCRELASSOCIATESMATERIAL\([^;]+;", text, flags=re.S):
        materialized.update(re.findall(r"#(\d+)", rel))
    missing_material = product_ids - materialized
    if missing_material:
        issues.append(_issue("ifc_material", "warning", f"{len(missing_material)} 个产品未明确关联材料。", recommendation="检查 IFCRELASSOCIATESMATERIAL 关联。"))
    invalid_placement = 0
    if "IFCLOCALPLACEMENT" not in text:
        invalid_placement += 1
        issues.append(_issue("ifc_placement", "fail", "IFC 文件缺少 IFCLOCALPLACEMENT。", recommendation="检查空间放置和构件 placement。"))
    score = max(0.0, 100.0 - sum(25 if i.severity == "fail" else 8 if i.severity == "warning" else 12 for i in issues))
    status = _status_from_issues(issues)
    return IfcCompatibilityCheckResult(
        score=round(score, 1), status=status, summary=f"IFC 兼容性自检：{status}，评分 {score:.1f}。",
        file_path=str(path), export_mode=export_mode, entity_counts=counts, raw_unicode_found=raw_unicode_found,
        missing_references=missing, zero_dimension_count=zero_dimensions, invalid_placement_count=invalid_placement,
        missing_material_association_count=len(missing_material), missing_spatial_containment_count=len(missing_containment),
        viewer_profiles=_viewer_profiles(counts, issues, export_mode=export_mode, raw_unicode=raw_unicode_found, missing_refs=missing, zero_dims=zero_dimensions, invalid_placement=invalid_placement, missing_material=len(missing_material), missing_spatial=len(missing_containment)),
        issues=issues,
    )
