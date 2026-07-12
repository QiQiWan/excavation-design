from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.domain import Project


def evaluate_drawing_completeness(
    project: Project,
    detailing: dict[str, Any],
    package_dir: Path,
    issue_mode: str,
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
    ]
    for filename in mandatory_schedules:
        add(
            f"SCHEDULE_{filename.upper().replace('.', '_')}",
            (package_dir / "90_schedules" / filename).exists(),
            f"{filename} 已生成",
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
        "boundary": "图纸完整性门禁验证图种、加工表和几何覆盖；项目级专业签章、现场条件和企业标准仍需人工校审。",
    }
