from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from app.drawings.cad_export import build_drawing_set_manifest, export_construction_cad_package
from app.drawings.professional_pdf import export_professional_batch_pdf
from app.services.rebar_detailing import build_rebar_detailing
from app.drawing_rules import get_effective_drawing_rule_set
from app.schemas.domain import DrawingRevision, Project
from app.services.advanced_suite import build_advanced_engineering_suite
from app.services.review_workflow import project_snapshot_hash, review_status
from app.version import SOFTWARE_VERSION


def _revision_code(index: int) -> str:
    value = index + 1
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def create_drawing_revision(project: Project, description: str, sheet_numbers: list[str], author: str, issue_status: str = "review") -> DrawingRevision:
    existing = {item.revision for item in project.drawing_revisions}
    index = 0
    revision = _revision_code(index)
    while revision in existing:
        index += 1
        revision = _revision_code(index)
    item = DrawingRevision(revision=revision, description=description, sheet_numbers=sheet_numbers, author=author, snapshot_hash=project_snapshot_hash(project), issue_status=issue_status)
    project.drawing_revisions.append(item)
    return item


def _page_title(ax, title: str, subtitle: str = "") -> None:
    ax.axis("off")
    ax.text(0.5, 0.72, title, ha="center", va="center", fontsize=20, fontweight="bold")
    if subtitle:
        ax.text(0.5, 0.60, subtitle, ha="center", va="center", fontsize=11)


def _table_page(pdf: PdfPages, title: str, headers: list[str], rows: list[list[Any]], footer: str = "") -> None:
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    ax.set_title(title, fontsize=15, pad=18)
    table = ax.table(cellText=[[str(x) for x in row] for row in rows], colLabels=headers, loc="center", cellLoc="left")
    table.auto_set_font_size(False); table.set_fontsize(7); table.scale(1, 1.35)
    if footer:
        ax.text(0.01, 0.02, footer, fontsize=7, transform=ax.transAxes)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)



def _issue_footer(fig, project: Project, sheet_no: str, title: str, review: dict[str, Any]) -> None:
    rules = get_effective_drawing_rule_set(project)
    fig.text(0.02, 0.015, f"{sheet_no}  {title}", fontsize=7)
    fig.text(0.50, 0.015, f"PitGuard V{SOFTWARE_VERSION} · {review.get('status')} · rule {rules.get('ruleSetHash')} · {review.get('currentSnapshotHash')}", ha="center", fontsize=6)
    fig.text(0.98, 0.015, "AI-DRAFT / PROFESSIONAL REVIEW REQUIRED", ha="right", fontsize=6)


def _plot_plan_page(pdf: PdfPages, project: Project, review: dict[str, Any], level_index: int | None = None) -> None:
    if not project.excavation or not project.retaining_system:
        return
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    pts = project.excavation.outline.points
    if pts:
        xs = [p.x for p in pts] + [pts[0].x]
        ys = [p.y for p in pts] + [pts[0].y]
        ax.plot(xs, ys, linewidth=2.2, label="excavation")
    supports = [x for x in project.retaining_system.supports if level_index is None or x.level_index == level_index]
    for support in supports:
        ax.plot([support.start.x, support.end.x], [support.start.y, support.end.y], linewidth=1.2)
        ax.text((support.start.x + support.end.x) / 2, (support.start.y + support.end.y) / 2, support.code, fontsize=4, ha="center")
    for column in project.retaining_system.columns:
        ax.scatter([column.location.x], [column.location.y], s=7)
    for obstacle in project.excavation.obstacles:
        if obstacle.outline and obstacle.outline.points:
            op = obstacle.outline.points
            ax.plot([p.x for p in op] + [op[0].x], [p.y for p in op] + [op[0].y], linestyle="--", linewidth=.8)
        elif obstacle.center:
            ax.scatter([obstacle.center.x], [obstacle.center.y], marker="x", s=18)
    title = "围护与支撑总平面" if level_index is None else f"第 {level_index} 层支撑平面"
    sheet = "S-00" if level_index is None else f"S-02-L{level_index:02d}"
    ax.set_title(title, fontsize=14)
    ax.set_aspect("equal", adjustable="box"); ax.grid(True, linewidth=.25, alpha=.35)
    ax.set_xlabel("X / m"); ax.set_ylabel("Y / m")
    _issue_footer(fig, project, sheet, title, review)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def _plot_wall_elevation_page(pdf: PdfPages, project: Project, review: dict[str, Any]) -> None:
    if not project.retaining_system or not project.retaining_system.diaphragm_walls:
        return
    walls = project.retaining_system.diaphragm_walls[:6]
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    offset = 0.0
    for wall in walls:
        length = float(wall.design_length or (wall.axis.points[-1].x - wall.axis.points[0].x if len(wall.axis.points) > 1 else 6.0) or 6.0)
        length = max(abs(length), 3.0)
        ax.plot([offset, offset + length, offset + length, offset, offset], [wall.top_elevation, wall.top_elevation, wall.bottom_elevation, wall.bottom_elevation, wall.top_elevation], linewidth=1.2)
        ax.text(offset + length / 2, wall.top_elevation + .5, wall.panel_code, ha="center", fontsize=6)
        for support in project.retaining_system.supports:
            if support.start_face_code == wall.design_face_code or support.end_face_code == wall.design_face_code:
                ax.plot([offset, offset + length], [support.elevation, support.elevation], linewidth=.55, linestyle="--")
        offset += length + 3.0
    if project.excavation:
        ax.axhline(project.excavation.bottom_elevation, linewidth=1.1, linestyle="-.", label="excavation bottom")
    ax.set_title("地下连续墙分区配筋立面总览", fontsize=14)
    ax.set_xlabel("展开墙长 / m"); ax.set_ylabel("高程 / m"); ax.grid(True, linewidth=.25, alpha=.35)
    _issue_footer(fig, project, "R-02", "地下连续墙分区配筋立面总览", review)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def _plot_advanced_summary_page(pdf: PdfPages, project: Project, review: dict[str, Any], suite: dict[str, Any]) -> None:
    fig, ax = plt.subplots(figsize=(11.69, 8.27)); ax.axis("off")
    ax.set_title("八项工程深化发行状态", fontsize=15, pad=20)
    cards = [
        ("长期效应与裂缝", suite["serviceability"]["status"], f"wmax={suite['serviceability']['summary'].get('maxEstimatedCrackWidthMm')} mm"),
        ("复杂平面拓扑", suite["topology"]["status"], f"levels={suite['topology']['summary'].get('levelCount')}"),
        ("碰撞与净距", suite["collisions"]["status"], f"hard={suite['collisions']['summary'].get('hardCollisionCount')}"),
        ("节点局部复核", suite["nodeLocal"]["status"], f"util={suite['nodeLocal']['summary'].get('maxUtilization')}"),
        ("监测反演", "ready" if suite["monitoring"].get("recordCount") else "pending", f"records={suite['monitoring'].get('recordCount')}"),
        ("四级审签", review.get("status"), f"valid={review.get('approvalValid')}"),
        ("正式图纸发行", suite["formalDrawings"].get("status"), "CAD/PDF/revision"),
        ("交互与可访问性", "ready", "compact/Ctrl+K/a11y"),
    ]
    for i, (name, status, detail) in enumerate(cards):
        col, row = i % 2, i // 2
        x, y = .06 + col * .47, .78 - row * .18
        ax.add_patch(plt.Rectangle((x, y), .41, .13, transform=ax.transAxes, fill=False, linewidth=1.0))
        ax.text(x + .02, y + .085, name, transform=ax.transAxes, fontsize=10, fontweight="bold")
        ax.text(x + .02, y + .045, f"{status} · {detail}", transform=ax.transAxes, fontsize=8)
    _issue_footer(fig, project, "Q-00", "八项工程深化发行状态", review)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)

def export_batch_pdf(project: Project, output_path: Path, mode: str = "balanced", issue_mode: str = "review") -> Path:
    suite = build_advanced_engineering_suite(project, mode)
    rules = get_effective_drawing_rule_set(project)
    manifest = build_drawing_set_manifest(project, scope="full", issue_mode=issue_mode, advanced_suite=suite, rule_set=rules)
    detailing = build_rebar_detailing(project, mode=mode)
    return export_professional_batch_pdf(project, output_path, manifest, detailing, suite, review_status(project))


def export_formal_drawing_package(project: Project, output_dir: str | Path, issue_mode: str = "review", rebar_mode: str = "balanced") -> Path:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    work = out / f"{project.id}_formal_issue_v{SOFTWARE_VERSION.replace('.', '_')}"
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    cad_zip = export_construction_cad_package(project, out, scope="full", rebar_mode=rebar_mode, issue_mode=issue_mode)
    with zipfile.ZipFile(cad_zip) as zf: zf.extractall(work / "CAD")
    pdf = export_batch_pdf(project, work / "PitGuard_drawing_issue_preview.pdf", rebar_mode, issue_mode)
    suite = build_advanced_engineering_suite(project, rebar_mode)
    rules = get_effective_drawing_rule_set(project)
    plan = build_drawing_set_manifest(project, scope="full", issue_mode=issue_mode, advanced_suite=suite, rule_set=rules)
    (work / "drawing_rule_set.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "drawing_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "advanced_engineering_suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "review_workflow.json").write_text(json.dumps(review_status(project), ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "drawing_revisions.json").write_text(json.dumps([x.model_dump(mode="json", by_alias=True) for x in project.drawing_revisions], ensure_ascii=False, indent=2), encoding="utf-8")
    with (work / "drawing_revisions.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w=csv.writer(f); w.writerow(["revision","description","sheets","author","issue_status","snapshot_hash","created_at"])
        for r in project.drawing_revisions: w.writerow([r.revision,r.description,";".join(r.sheet_numbers),r.author,r.issue_status,r.snapshot_hash,r.created_at])
    (work / "DWG_CONVERSION_README.txt").write_text(
        "DXF files are native AutoCAD R2018 with 1:1 mm model space, paper-space layouts, locked viewports, Unicode text and native dimensions.\n"
        "Batch DWG conversion still requires AutoCAD, BricsCAD or ODA File Converter; preserve layouts, plot styles and enterprise page setups.\n", encoding="utf-8")
    (work / "plot_publish_manifest.json").write_text(json.dumps({
        "source": "CAD/drawing_set_manifest.json", "batchPdf": pdf.name, "paperSizes": ["A1", "A2", "A3"],
        "pageSetupRequired": True, "ctbRequired": True, "issueMode": issue_mode, "review": review_status(project),
        "pdfContainsGeometryPreviews": True, "geometryPreviewTypes": ["overall_plan", "support_level_plans", "wall_elevation", "engineering_closure"],
        "revisionCount": len(project.drawing_revisions),
        "drawingRuleSetId": rules.get("id"), "drawingRuleSetVersion": rules.get("version"),
        "drawingRuleSetHash": rules.get("ruleSetHash"), "drawingPlanHash": plan.get("planHash"),
        "drawingRulePreset": rules.get("preset"), "drawingSheetCount": plan.get("sheetCount"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = out / f"{project.id}_formal_drawing_issue_{issue_mode}_v{SOFTWARE_VERSION.replace('.', '_')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in work.rglob("*"):
            if file.is_file(): zf.write(file, file.relative_to(work).as_posix())
    return zip_path
