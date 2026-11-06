from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.domain import Project


def evaluate_drawing_completeness(
    project: Project,
    detailing: dict[str, Any],
    package_dir: Path,
    issue_mode: str,
    sheet_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether the package contains the minimum coordinated content.

    The checks deliberately use stable output artefacts instead of renderer internals,
    so enterprise rule packs may add sheets without weakening the mandatory baseline.
    """
    dxf_files = sorted(package_dir.rglob("*.dxf"))
    names = {p.name.upper() for p in dxf_files}
    rels = {p.relative_to(package_dir).as_posix().upper() for p in dxf_files}
    checks: list[dict[str, Any]] = []

    def add(code: str, passed: bool, message: str, severity: str = "fail") -> None:
        checks.append({"code": code, "status": "pass" if passed else severity, "message": message})

    def has_token(token: str) -> bool:
        token = token.upper()
        return any(token in name for name in names) or any(token in rel for rel in rels)

    add("DRAWING_REGISTER", (package_dir / "drawing_register.csv").exists(), "图纸目录已生成")
    add("GENERAL_NOTES", has_token("G-00") or has_token("GENERAL"), "总说明与图例已生成")
    add("MASTER_PLAN", has_token("S-00") or has_token("MASTER"), "围护与支撑总平面已生成")
    add("CONTROL_SECTION", has_token("S-03") or has_token("SECTION"), "控制剖面已生成")
    add("WALL_REBAR", has_token("R-02") or has_token("WALL_REBAR"), "地下连续墙配筋立面已生成")
    if project.retaining_system and project.retaining_system.supports:
        add("SUPPORT_REBAR", has_token("R-04") or has_token("SUPPORT_REBAR"), "支撑配筋图已生成")
    if project.retaining_system and project.retaining_system.wale_beams:
        add("WALE_REBAR", has_token("R-05") or has_token("WALE"), "围檩/冠梁配筋图已生成")
    if project.retaining_system and project.retaining_system.support_nodes:
        add("NODE_DETAILS", has_token("D-01") or has_token("DETAIL"), "节点大样已生成")
        add("NODE_HARDWARE_DETAIL", has_token("D-10") or has_token("NODE_HARDWARE"), "节点承压板、加劲板、焊缝与锚筋详图已生成", "warning" if issue_mode == "review" else "fail")
    if project.retaining_system and len(project.retaining_system.diaphragm_walls) > 1:
        add("WALL_JOINT_DETAIL", has_token("D-06") or has_token("WALL_JOINT"), "墙幅接头、止水和钢筋笼连接详图已生成")
    if project.retaining_system and project.retaining_system.columns:
        add("SUPPORT_COLUMN_DETAIL", has_token("D-03") or has_token("SUPPORT_COLUMN"), "支撑—立柱节点和基础索引详图已生成")
    if project.retaining_system and project.retaining_system.supports:
        add("SUPPORT_SPLICE_DETAIL", has_token("D-07") or has_token("SUPPORT_SPLICE"), "支撑锚固、施工缝和错开搭接详图已生成")
        levels = sorted({int(item.level_index) for item in project.retaining_system.supports})
        level_plan_count = sum(1 for rel in rels if "SUPPORT_LEVEL" in rel or "S-02-L" in rel or "S02L" in rel)
        add("SUPPORT_LEVEL_PLANS", level_plan_count >= len(levels), f"支撑分层平面图 {level_plan_count}/{len(levels)} 张")

    def detail_content_check(token: str, required_layers: list[str], min_entities: int, message: str) -> None:
        candidates = [item for item in dxf_files if token.upper() in item.name.upper() or token.upper() in item.as_posix().upper()]
        if not candidates:
            add(f"CONTENT_{token}", False, message)
            return
        try:
            content = candidates[0].read_text(encoding="utf-8", errors="ignore").upper()
        except OSError:
            add(f"CONTENT_{token}", False, message)
            return
        entity_count = content.count("\n0\nLINE\n") + content.count("\n0\nLWPOLYLINE\n") + content.count("\n0\nCIRCLE\n") + content.count("\n0\nTEXT\n") + content.count("\n0\nMTEXT\n")
        layers_ok = all(layer.upper() in content for layer in required_layers)
        add(f"CONTENT_{token}", entity_count >= min_entities and layers_ok, f"{message}；实体数={entity_count}，关键图层={'完整' if layers_ok else '缺失'}", "warning" if issue_mode == "review" else "fail")

    detail_content_check("D-06", ["PIT_WATERSTOP", "PIT_TABLE", "PIT_DIM", "PIT_REBAR_MAIN"], 70, "D-06应包含平剖面、立面、止水、尺寸、钢筋和接头要求表")
    detail_content_check("D-03", ["PIT_COLUMN", "PIT_SUPPORT", "PIT_FOUNDATION", "PIT_DIM"], 35, "D-03应包含支撑—立柱平面、立面、尺寸和基础索引")

    mandatory_schedules = [
        "rebar_schedule.csv",
        "rebar_bending_schedule.csv",
        "fabrication_bbs.csv",
        "fabrication_segments.csv",
        "geometric_rebar_spacing_checks.csv",
        "shop_drawing_checklist.csv",
        "embedded_item_schedule.csv",
        "weld_schedule.csv",
        "stiffener_schedule.csv",
        "coupler_schedule.csv",
        "cage_hoisting_analysis.csv",
        "construction_sequence.csv",
        "embedded_item_collision_checks.csv",
        "support_junction_schedule.csv",
        "wall_panel_cage_traceability.csv",
        "cross_artifact_traceability.json",
    ]
    for filename in mandatory_schedules:
        add(
            f"SCHEDULE_{filename.upper().replace('.', '_')}",
            (package_dir / "90_schedules" / filename).exists(),
            f"{filename} 已生成",
        )


    if sheet_quality is not None:
        add(
            "SHEET_PUBLICATION_QUALITY",
            sheet_quality.get("status") != "fail",
            f"图纸表达质量评分={sheet_quality.get('score')}，失败图纸={sheet_quality.get('failCount', 0)}，警告图纸={sheet_quality.get('warningCount', 0)}",
            "warning" if issue_mode == "review" else "fail",
        )
        add(
            "SHEET_NUMBER_UNIQUENESS",
            not bool(sheet_quality.get("duplicateSheetNumbers")),
            f"重复图号={','.join(sheet_quality.get('duplicateSheetNumbers') or []) or '无'}",
        )

    fabrication = detailing.get("fabrication") or {}
    summary = fabrication.get("summary") or {}
    add(
        "FABRICATION_LENGTH",
        float(summary.get("maxPieceLengthM", 999.0) or 999.0) <= float(fabrication.get("transportLimitM", 12.0) or 12.0) + 1e-6,
        f"最大加工长度={summary.get('maxPieceLengthM', 0)} m",
    )
    add(
        "FABRICATION_IDENTIFIERS",
        int(summary.get("duplicateSourceBarIdCount", 0) or 0) == 0,
        f"重复钢筋ID数量={summary.get('duplicateSourceBarIdCount', 0)}",
    )
    omitted = int((detailing.get("geometrySummary") or {}).get("omittedBarCount", 0) or 0)
    add(
        "FULL_REBAR_GEOMETRY",
        omitted == 0,
        f"逐根几何省略数量={omitted}；加工表已覆盖配筋条目，但正式碰撞结论需完整几何",
        "warning" if issue_mode == "review" else "fail",
    )
    embedded_status = str(fabrication.get("embeddedItemCollisionStatus") or "")
    add(
        "EMBEDDED_ITEM_GEOMETRY",
        embedded_status in {"pass", "not_applicable"},
        f"预埋件碰撞状态={embedded_status or 'unknown'}",
        "warning" if issue_mode == "review" else "fail",
    )

    blockers = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warning"]
    status = "fail" if blockers else "warning" if warnings else "pass"
    return {
        "status": status,
        "checkCount": len(checks),
        "blockerCount": len(blockers),
        "warningCount": len(warnings),
        "checks": checks,
        "dxfSheetCount": len(dxf_files),
        "sheetQualitySummary": {k: v for k, v in (sheet_quality or {}).items() if k != "sheets"},
        "boundary": "V3.13图纸门禁同时检查图种、分层平面、典型节点内容深度、关键图层、原生尺寸、纸空间、图签、加工表和几何覆盖；项目级专业签章、现场条件和企业标准仍需人工校审。",
    }
