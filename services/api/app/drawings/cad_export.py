from __future__ import annotations

import csv
import math
import zipfile
from pathlib import Path
from typing import Iterable

from app.drawings.detail_sheets import generate_construction_detail_sheets
from app.schemas.domain import Point2D, Project, ReinforcementGroup
from app.services.rebar_detailing import build_rebar_detailing
from app.services.cad_template import normalize_cad_template


class DxfWriter:
    def __init__(self) -> None:
        self.entities: list[str] = []
        self.layers: set[str] = set()

    def _add_layer(self, layer: str) -> None:
        self.layers.add(layer)

    def line(self, layer: str, x1: float, y1: float, x2: float, y2: float) -> None:
        self._add_layer(layer)
        self.entities.extend([
            "0", "LINE", "8", layer,
            "10", f"{x1:.4f}", "20", f"{y1:.4f}", "30", "0.0",
            "11", f"{x2:.4f}", "21", f"{y2:.4f}", "31", "0.0",
        ])

    def circle(self, layer: str, x: float, y: float, r: float) -> None:
        self._add_layer(layer)
        self.entities.extend(["0", "CIRCLE", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0", "40", f"{max(r, 0.01):.4f}"])

    def text(self, layer: str, x: float, y: float, value: str, height: float = 0.35, rotation: float = 0.0) -> None:
        self._add_layer(layer)
        safe = str(value).replace("\n", " ")[:240]
        self.entities.extend(["0", "TEXT", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0", "40", f"{height:.4f}", "1", safe, "50", f"{rotation:.4f}"])

    def lwpolyline(self, layer: str, points: Iterable[Point2D], closed: bool = False) -> None:
        pts = list(points)
        if not pts:
            return
        self._add_layer(layer)
        self.entities.extend(["0", "LWPOLYLINE", "8", layer, "90", str(len(pts)), "70", "1" if closed else "0"])
        for p in pts:
            self.entities.extend(["10", f"{p.x:.4f}", "20", f"{p.y:.4f}"])

    def rectangle(self, layer: str, x: float, y: float, w: float, h: float) -> None:
        class P:
            def __init__(self, x: float, y: float) -> None:
                self.x = x; self.y = y
        self.lwpolyline(layer, [P(x, y), P(x + w, y), P(x + w, y + h), P(x, y + h)], closed=True)  # type: ignore[arg-type]

    def dim_line(self, layer: str, x1: float, y1: float, x2: float, y2: float, label: str, offset: float = 2.0) -> None:
        self.line(layer, x1, y1, x2, y2)
        self.line(layer, x1, y1 - 0.25, x1, y1 + 0.25)
        self.line(layer, x2, y2 - 0.25, x2, y2 + 0.25)
        self.text(layer, (x1 + x2) / 2.0, (y1 + y2) / 2.0 + offset, label, 0.28)

    def title_block(self, sheet_no: str, title: str, scale: str = "1:100", project_name: str = "PitGuard project", stage: str = "施工图深化接口", designer: str = "AI", checker: str = "REVIEW", approver: str = "CHIEF", template: dict | None = None) -> None:
        # Standardized editable CAD title block in model coordinates. V2.4.0 allows
        # project-specific enterprise template fields while keeping R12 DXF compatibility.
        template = template or {}
        block = template.get("titleBlock", {}) if isinstance(template, dict) else {}
        layers = template.get("layerStandard", {}) if isinstance(template, dict) else {}
        frame_layer = layers.get("frame", "PIT_FRAME")
        title_layer = layers.get("title", "PIT_TITLE")
        text_layer = layers.get("text", "PIT_TEXT")
        x0, y0 = float(block.get("originX", 0.0)), float(block.get("originY", -18.0))
        width, height = float(block.get("width", 120.0)), float(block.get("height", 16.0))
        self.rectangle(frame_layer, x0, y0, width, height)
        for ratio in (0.15, 0.66, 0.79, 0.90):
            self.line(frame_layer, x0 + width * ratio, y0, x0 + width * ratio, y0 + height)
        for ratio in (0.25, 0.5, 0.75):
            self.line(frame_layer, x0, y0 + height * ratio, x0 + width, y0 + height * ratio)
        labels = {
            "project": block.get("projectNameLabel", "工程名称"), "title": block.get("sheetTitleLabel", "图名"),
            "stage": block.get("stageLabel", "阶段"), "sheet": block.get("sheetNoLabel", "图号"),
            "scale": block.get("scaleLabel", "比例"), "designer": block.get("designerLabel", "设计"),
            "checker": block.get("checkerLabel", "校核"), "approver": block.get("approverLabel", "审定"),
        }
        th = float((template.get("dimensionRules", {}) if isinstance(template, dict) else {}).get("textHeight", 0.35))
        self.text(title_layer, x0 + 2.0, y0 + height * 0.82, labels["project"], th)
        self.text(text_layer, x0 + width * 0.17, y0 + height * 0.82, project_name[:42], th)
        self.text(title_layer, x0 + 2.0, y0 + height * 0.57, labels["title"], th)
        self.text(text_layer, x0 + width * 0.17, y0 + height * 0.57, title[:44], th)
        self.text(title_layer, x0 + 2.0, y0 + height * 0.32, labels["stage"], th)
        self.text(text_layer, x0 + width * 0.17, y0 + height * 0.32, stage, th)
        self.text(title_layer, x0 + width * 0.68, y0 + height * 0.82, labels["sheet"], th)
        self.text(text_layer, x0 + width * 0.81, y0 + height * 0.82, sheet_no, th)
        self.text(title_layer, x0 + width * 0.68, y0 + height * 0.57, labels["scale"], th)
        self.text(text_layer, x0 + width * 0.81, y0 + height * 0.57, scale, th)
        self.text(title_layer, x0 + width * 0.68, y0 + height * 0.32, labels["designer"], th)
        self.text(text_layer, x0 + width * 0.81, y0 + height * 0.32, designer, th)
        self.text(title_layer, x0 + width * 0.68, y0 + height * 0.07, labels["checker"], th)
        self.text(text_layer, x0 + width * 0.81, y0 + height * 0.07, checker, th)
        self.text(title_layer, x0 + width * 0.91, y0 + height * 0.07, labels["approver"], th)
        self.text(text_layer, x0 + width * 0.96, y0 + height * 0.07, approver, th)

    def body(self) -> str:
        layer_records: list[str] = []
        color = 1
        for layer in sorted(self.layers | {"0"}):
            layer_records.extend(["0", "LAYER", "2", layer, "70", "0", "62", str(color), "6", "CONTINUOUS"])
            color = color + 1 if color < 7 else 1
        parts = [
            "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "0", "ENDSEC",
            "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(layer_records) // 14 + 1),
            *layer_records,
            "0", "ENDTAB", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            *self.entities,
            "0", "ENDSEC", "0", "EOF",
        ]
        return "\n".join(parts) + "\n"

    def write(self, path: Path) -> None:
        path.write_text(self.body(), encoding="utf-8")



def _cad_template(project: Project) -> dict:
    return normalize_cad_template(project)

def _sheet_no(project: Project, number: str) -> str:
    template = _cad_template(project)
    prefix = str(template.get("sheetPrefix") or "S")
    return f"{prefix}-{number}"

def _title_block(dxf: DxfWriter, project: Project, number: str, title: str, scale: str) -> None:
    template = _cad_template(project)
    dxf.title_block(_sheet_no(project, number), title, scale, project.name, str(template.get("stage", "施工图深化接口")), str(template.get("designer", "AI-DRAFT")), str(template.get("checker", "ENGINEER-REVIEW")), str(template.get("approver", "CHIEF-REVIEW")), template=template)

def _layer(project: Project, logical: str, fallback: str) -> str:
    return str(_cad_template(project).get("layerStandard", {}).get(logical, fallback))

def _angle(a: Point2D, b: Point2D) -> float:
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x))


def _mid(a: Point2D, b: Point2D) -> tuple[float, float]:
    return (a.x + b.x) / 2.0, (a.y + b.y) / 2.0


def _group_token(group: ReinforcementGroup) -> str:
    token = f"{group.name} D{group.diameter:g}"
    if group.spacing:
        token += f"@{group.spacing:g}"
    if group.count:
        token += f"x{group.count}"
    return token


def _write_support_plan(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    ret = project.retaining_system
    if project.excavation:
        dxf.lwpolyline("PIT_EXCAVATION", project.excavation.outline.points, closed=True)
        for obs in project.excavation.obstacles:
            if obs.outline:
                dxf.lwpolyline("PIT_OBSTACLE", obs.outline.points, closed=obs.outline.closed)
                if obs.outline.points:
                    dxf.text("PIT_TEXT", obs.outline.points[0].x, obs.outline.points[0].y, f"{obs.name}/{obs.obstacle_type}", 0.4)
            elif obs.center and obs.width and obs.length:
                dxf.rectangle("PIT_OBSTACLE", obs.center.x - obs.width / 2, obs.center.y - obs.length / 2, obs.width, obs.length)
                dxf.text("PIT_TEXT", obs.center.x, obs.center.y, obs.name, 0.4)
    if ret:
        for wall in ret.diaphragm_walls:
            if len(wall.axis.points) >= 2:
                a, b = wall.axis.points[0], wall.axis.points[-1]
                dxf.line("PIT_WALL", a.x, a.y, b.x, b.y)
                mx, my = _mid(a, b)
                dxf.text("PIT_TEXT", mx, my, f"{wall.panel_code} t={wall.thickness:g}m", 0.35, _angle(a, b))
        for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
            if len(beam.axis.points) >= 2:
                a, b = beam.axis.points[0], beam.axis.points[-1]
                dxf.line("PIT_WALE", a.x, a.y, b.x, b.y)
        for support in ret.supports:
            dxf.line("PIT_SUPPORT", support.start.x, support.start.y, support.end.x, support.end.y)
            mx, my = _mid(support.start, support.end)
            dxf.text("PIT_TEXT", mx, my, f"{support.code} L{support.level_index} N={support.design_axial_force or 0:.0f}kN", 0.32, _angle(support.start, support.end))
        for col in ret.columns:
            dxf.circle("PIT_COLUMN", col.location.x, col.location.y, max((col.section.width or col.section.diameter or 0.6) / 2, 0.2))
            dxf.text("PIT_TEXT", col.location.x + 0.4, col.location.y + 0.4, col.code, 0.3)
    dxf.text("PIT_TITLE", 0, -5, "S-01 Foundation pit support plan - PitGuard generated CAD exchange drawing", 0.55)
    if project.excavation and project.excavation.outline.points:
        pts = project.excavation.outline.points
        xs = [p.x for p in pts]; ys = [p.y for p in pts]
        dxf.dim_line("PIT_DIM", min(xs), max(ys) + 3.0, max(xs), max(ys) + 3.0, f"{max(xs)-min(xs):.1f} m")
        dxf.dim_line("PIT_DIM", max(xs) + 3.0, min(ys), max(xs) + 3.0, max(ys), f"{max(ys)-min(ys):.1f} m", offset=0.8)
    _title_block(dxf, project, "01", "基坑围护与支撑平面布置图", str(_cad_template(project).get("sheetRules", {}).get("defaultScalePlan", "1:200")))
    dxf.write(path)


def _write_wall_rebar_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    wall = project.retaining_system.diaphragm_walls[0] if project.retaining_system and project.retaining_system.diaphragm_walls else None
    length = float(wall.design_length or 6.0) if wall else 6.0
    height = float((wall.top_elevation - wall.bottom_elevation) if wall else 18.0)
    x0, y0 = 0.0, 0.0
    dxf.rectangle("PIT_CONCRETE", x0, y0, length, height)
    groups = wall.reinforcement if wall else []
    vertical = [g for g in groups if g.bar_type == "longitudinal"][:2]
    horiz = [g for g in groups if g.bar_type == "distribution"][:1]
    v_count = 10
    for face, offset in enumerate((0.18, length - 0.18), start=1):
        for i in range(v_count):
            x = x0 + 0.35 + (length - 0.7) * i / max(v_count - 1, 1)
            dxf.line("PIT_REBAR_MAIN", x, y0 + 0.25, x, y0 + height - 0.25)
    for j in range(12):
        y = y0 + 0.4 + (height - 0.8) * j / 11
        dxf.line("PIT_REBAR_DISTRIBUTION", x0 + 0.2, y, x0 + length - 0.2, y)
    note = "; ".join(_group_token(g) for g in groups) or "rebar groups pending calculation"
    dxf.text("PIT_TEXT", x0, y0 + height + 1.0, f"Typical diaphragm wall cage: {wall.panel_code if wall else '-'}", 0.45)
    dxf.text("PIT_TEXT", x0, y0 - 1.0, note, 0.28)
    _title_block(dxf, project, "02", "地下连续墙钢筋笼配筋图", str(_cad_template(project).get("sheetRules", {}).get("defaultScaleDetail", "1:50")))
    dxf.write(path)


def _write_node_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    dxf.rectangle("PIT_WALE", 0, 0, 8.0, 1.2)
    dxf.rectangle("PIT_PLATE", 3.2, -0.6, 1.6, 2.4)
    dxf.line("PIT_SUPPORT", 4.0, -3.0, 4.0, 4.0)
    for i in range(6):
        y = 0.18 + i * 0.16
        dxf.line("PIT_REBAR_MAIN", 0.3, y, 7.7, y)
    for i in range(6):
        x = 3.4 + i * 0.22
        dxf.line("PIT_REBAR_STIRRUP", x, -0.5, x, 1.7)
    node = project.retaining_system.support_nodes[0] if project.retaining_system and project.retaining_system.support_nodes else None
    note = "; ".join(_group_token(g) for g in (node.reinforcement if node else [])) or "node reinforcement pending"
    dxf.text("PIT_TEXT", 0, 2.2, f"D-02 Support-wale node detail {node.code if node else ''}", 0.4)
    dxf.text("PIT_TEXT", 0, -1.6, note, 0.28)
    _title_block(dxf, project, "03", "支撑-围檩节点详图", str(_cad_template(project).get("sheetRules", {}).get("defaultScaleDetail", "1:50")))
    dxf.write(path)


def _write_rebar_schedule(project: Project, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["host_type", "host_code", "group_name", "bar_type", "diameter_mm", "spacing_mm", "count", "grade", "status", "location_description"])
        ret = project.retaining_system
        if not ret:
            return
        for wall in ret.diaphragm_walls:
            for g in wall.reinforcement:
                writer.writerow(["diaphragm_wall", wall.panel_code, g.name, g.bar_type, g.diameter, g.spacing, g.count, g.grade, g.check_status, g.location_description])
        for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
            for g in beam.reinforcement or []:
                writer.writerow(["beam", beam.code, g.name, g.bar_type, g.diameter, g.spacing, g.count, g.grade, g.check_status, g.location_description])
            if beam.design_result and beam.design_result.main_bar_diameter:
                writer.writerow(["beam", beam.code, "design_result_main_bar", "longitudinal", beam.design_result.main_bar_diameter, beam.design_result.main_bar_spacing, "", "HRB400", beam.design_result.check_status, "from wale beam design result"])
            if beam.design_result and beam.design_result.stirrup_diameter:
                writer.writerow(["beam", beam.code, "design_result_stirrup", "stirrup", beam.design_result.stirrup_diameter, beam.design_result.stirrup_spacing, "", "HRB400", beam.design_result.check_status, "from wale beam design result"])
        for support in ret.supports:
            for g in support.reinforcement:
                writer.writerow(["internal_support", support.code, g.name, g.bar_type, g.diameter, g.spacing, g.count, g.grade, g.check_status, g.location_description])
        for node in ret.support_nodes or []:
            for g in node.reinforcement:
                writer.writerow(["support_wale_node", node.code, g.name, g.bar_type, g.diameter, g.spacing, g.count, g.grade, g.check_status, g.location_description])



def _write_excavation_section(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    depth = abs(project.excavation.bottom_elevation - project.excavation.top_elevation) if project.excavation else 12.0
    ret = project.retaining_system
    wall = ret.diaphragm_walls[0] if ret and ret.diaphragm_walls else None
    wall_bottom = wall.bottom_elevation if wall else -depth - 8.0
    top = project.excavation.top_elevation if project.excavation else 0.0
    bottom = project.excavation.bottom_elevation if project.excavation else -depth
    x_wall = 0.0
    dxf.line("PIT_WALL", x_wall, wall_bottom, x_wall, top)
    dxf.line("PIT_EXCAVATION", -2.0, bottom, 12.0, bottom)
    dxf.line("PIT_GROUND", -2.0, top, 12.0, top)
    dxf.text("PIT_TEXT", -1.8, top + 0.6, f"Ground EL {top:.2f}m", 0.35)
    dxf.text("PIT_TEXT", -1.8, bottom - 0.8, f"Excavation bottom EL {bottom:.2f}m", 0.35)
    if ret:
        for support in sorted(ret.supports, key=lambda item: (item.level_index, item.elevation))[:8]:
            dxf.line("PIT_SUPPORT", x_wall, support.elevation, 9.0, support.elevation)
            dxf.text("PIT_TEXT", 9.3, support.elevation, f"L{support.level_index} {support.code} EL {support.elevation:.2f}", 0.3)
        for beam in ret.wale_beams[:8]:
            dxf.rectangle("PIT_WALE", -0.5, beam.elevation - 0.25, 1.0, 0.5)
    dxf.text("PIT_TITLE", -2.0, wall_bottom - 1.5, "S-04 Typical excavation support section with staged bracing elevations", 0.45)
    _title_block(dxf, project, "04", "典型开挖剖面与支撑标高图", str(_cad_template(project).get("sheetRules", {}).get("defaultScaleSection", "1:100")))
    dxf.write(path)


def _write_column_pile_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    ret = project.retaining_system
    column = ret.columns[0] if ret and ret.columns else None
    fdn = column.foundation_design if column and column.foundation_design else None
    pile_d = fdn.pile_diameter or 0.8 if fdn else 0.8
    pile_l = fdn.pile_length or 18.0 if fdn else 18.0
    dxf.line("PIT_COLUMN", 0.0, 1.2, 0.0, 8.0)
    dxf.rectangle("PIT_CONCRETE", -1.0, 0.0, 2.0, 1.2)
    dxf.line("PIT_PILE", 0.0, 0.0, 0.0, -pile_l)
    dxf.circle("PIT_PILE", 0.0, -pile_l, max(pile_d / 2.0, 0.2))
    for i in range(6):
        x = -0.35 + i * 0.14
        dxf.line("PIT_REBAR_MAIN", x, -pile_l + 0.5, x, 0.8)
    dxf.text("PIT_TEXT", 1.2, 0.8, f"Column: {column.code if column else '-'}", 0.35)
    dxf.text("PIT_TEXT", 1.2, -1.0, f"Pile D={pile_d:.2f}m L={pile_l:.1f}m util={fdn.pile_utilization if fdn else '-'}", 0.3)
    dxf.text("PIT_TITLE", -2.0, -pile_l - 1.0, "S-05 Temporary column and pile detail", 0.45)
    _title_block(dxf, project, "05", "临时立柱与立柱桩详图", str(_cad_template(project).get("sheetRules", {}).get("defaultScaleDetail", "1:50")))
    dxf.write(path)


def _write_monitoring_plan(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    if project.excavation:
        dxf.lwpolyline("PIT_EXCAVATION", project.excavation.outline.points, closed=True)
        pts = project.excavation.outline.points
        for i, p in enumerate(pts[:60], start=1):
            dxf.circle("PIT_MONITOR", p.x, p.y, 0.35)
            dxf.text("PIT_TEXT", p.x + 0.45, p.y + 0.45, f"CX{i:02d}", 0.25)
        for i, seg in enumerate(project.excavation.segments[:60], start=1):
            dxf.circle("PIT_MONITOR", seg.midpoint.x, seg.midpoint.y, 0.25)
            dxf.text("PIT_TEXT", seg.midpoint.x + 0.35, seg.midpoint.y - 0.35, f"WY{i:02d}", 0.25)
    ret = project.retaining_system
    if ret:
        for i, support in enumerate(ret.supports[:80], start=1):
            mx, my = _mid(support.start, support.end)
            if i % max(len(ret.supports)//12, 1) == 0:
                dxf.circle("PIT_MONITOR_FORCE", mx, my, 0.28)
                dxf.text("PIT_TEXT", mx + 0.35, my, f"ZL{i:02d}", 0.25)
    dxf.text("PIT_TITLE", 0, -6, "S-06 Monitoring layout: wall displacement, settlement and support axial-force points", 0.45)
    _title_block(dxf, project, "06", "监测点布置图", str(_cad_template(project).get("sheetRules", {}).get("defaultScalePlan", "1:200")))
    dxf.write(path)


def _write_drawing_register(project: Project, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["sheet_no", "title", "scale", "file", "status", "model_binding"])
        rows = [
            ("S-01", "基坑围护与支撑平面布置图", "1:200", "S-01_support_plan.dxf", "generated", "excavation, walls, wales, supports, columns"),
            ("S-02", "地下连续墙钢筋笼示意图", "1:50", "S-02_wall_rebar_cage.dxf", "generated", "diaphragm walls and wall rebar groups"),
            ("S-03", "支撑-围檩节点详图", "1:50", "S-03_support_wale_node_detail.dxf", "generated", "support-wale nodes and node reinforcement"),
            ("S-04", "典型开挖剖面与支撑标高图", "1:100", "S-04_excavation_section.dxf", "generated", "construction stages and support elevations"),
            ("S-05", "临时立柱与立柱桩详图", "1:50", "S-05_column_pile_detail.dxf", "generated", "columns and foundation design"),
            ("S-06", "监测点布置图", "1:200", "S-06_monitoring_plan.dxf", "generated", "monitoring points from pit geometry"),
            ("S-07", "钢筋大样与钢筋表", "NTS", "S-07_rebar_bending_schedule.dxf", "generated", "rebar detailing and bar bending schedule"),
            ("S-08", "逐根钢筋几何索引图", "NTS", "S-08_individual_rebar_geometry.dxf", "generated", "individual rebar centerline geometry index"),
        ]
        writer.writerows(rows)


def _write_material_schedule(project: Project, path: Path) -> None:
    ret = project.retaining_system
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["item", "quantity", "unit", "basis", "note"])
        if not ret:
            return
        wall_len = sum(float(w.design_length or 0.0) for w in ret.diaphragm_walls)
        wall_depth = max((abs(w.top_elevation - w.bottom_elevation) for w in ret.diaphragm_walls), default=0.0)
        wall_volume = sum(float(w.design_length or 0.0) * float(w.thickness or 0.0) * abs(w.top_elevation - w.bottom_elevation) for w in ret.diaphragm_walls)
        support_len = sum(_support_len(s) for s in ret.supports)
        writer.writerow(["diaphragm_wall_concrete", round(wall_volume, 3), "m3", f"length {wall_len:.2f}m depth {wall_depth:.2f}m", "preliminary quantity"])
        writer.writerow(["internal_support_total_length", round(support_len, 3), "m", f"{len(ret.supports)} supports", "preliminary quantity"])
        writer.writerow(["temporary_columns", len(ret.columns), "each", "column layout", "preliminary quantity"])
        writer.writerow(["wale_beams", len(ret.wale_beams), "segments", "wale beam model", "preliminary quantity"])


def _support_len(support) -> float:
    return ((support.end.x - support.start.x) ** 2 + (support.end.y - support.start.y) ** 2) ** 0.5


def _write_delivery_consistency_matrix(project: Project, path: Path) -> None:
    latest = project.calculation_results[-1] if project.calculation_results else None
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["data_item", "source", "IFC", "CAD", "DOCX", "JSON", "status", "note"])
        writer.writerow(["excavation_outline", "project.excavation.outline", "yes", "yes", "yes", "yes", "pass", "same model object"])
        writer.writerow(["retaining_members", "project.retaining_system", "yes", "yes", "yes", "yes", "pass", "walls/wales/supports/columns share ids"])
        writer.writerow(["calculation_results", "latest.calculation_results", "property sets", "tables/charts", "yes", "yes", "pass" if latest else "missing", "run calculation before formal issue"])
        writer.writerow(["rebar_groups", "retaining_system.*.reinforcement", "yes", "yes", "yes", "yes", "pass", "parameterized groups; full bar bending remains review item"])
        writer.writerow(["issue_center", "/api/projects/{id}/issues", "sidecar", "README", "checklist", "yes", "pass", "project-specific readiness may still contain warnings"])



def _write_rebar_bending_schedule(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    dxf.text("PIT_TITLE", 0, 4, "S-07 Rebar bending schedule and bar-mark table", 0.55)
    headers = ["Mark", "Host", "Type", "D", "Shape", "Qty", "L(m)", "W(kg)"]
    xs = [0, 18, 48, 68, 78, 92, 104, 116]
    y0 = 0.0
    for i, h in enumerate(headers):
        dxf.text("PIT_TITLE", xs[i], y0, h, 0.28)
    for row, item in enumerate(detailing.get("entries", [])[:36], start=1):
        y = y0 - row * 1.0
        values = [item.get("barMark"), item.get("hostCode"), item.get("barType"), item.get("diameterMm"), item.get("shapeCode"), item.get("quantity"), item.get("singleLengthM"), item.get("totalWeightKg")]
        for i, value in enumerate(values):
            dxf.text("PIT_TEXT", xs[i], y, str(value), 0.22)
    # Typical shape legend.
    dxf.rectangle("PIT_REBAR_MAIN", 0, -42, 12, 2)
    dxf.line("PIT_REBAR_MAIN", 3, -46, 15, -46)
    dxf.line("PIT_REBAR_STIRRUP", 25, -48, 35, -48)
    dxf.line("PIT_REBAR_STIRRUP", 35, -48, 35, -42)
    dxf.line("PIT_REBAR_STIRRUP", 35, -42, 25, -42)
    dxf.line("PIT_REBAR_STIRRUP", 25, -42, 25, -48)
    dxf.text("PIT_TEXT", 0, -50, "Shape 00: straight bar; Shape 21: closed stirrup with hooks; Shape 31/99: tie/additional bars, review required.", 0.28)
    _title_block(dxf, project, "07", "钢筋大样与钢筋表", "NTS")
    dxf.write(path)


def _write_rebar_bending_schedule_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_mark", "host_type", "host_code", "group_name", "bar_type", "diameter_mm", "spacing_mm", "shape_code", "shape_description", "quantity", "single_length_m", "total_length_m", "total_weight_kg", "anchorage_status", "lap_status", "hook_status", "note"])
        for item in detailing.get("entries", []):
            writer.writerow([item.get("barMark"), item.get("hostType"), item.get("hostCode"), item.get("groupName"), item.get("barType"), item.get("diameterMm"), item.get("spacingMm"), item.get("shapeCode"), item.get("shapeDescription"), item.get("quantity"), item.get("singleLengthM"), item.get("totalLengthM"), item.get("totalWeightKg"), item.get("anchorageStatus"), item.get("lapStatus"), item.get("hookStatus"), item.get("note")])


def _write_enterprise_template_manifest(project: Project, path: Path) -> None:
    import json
    path.write_text(json.dumps({
        **_cad_template(project),
        "projectId": project.id,
        "titleBlockFields": ["project_name", "sheet_title", "sheet_no", "scale", "stage", "designer", "checker"],
        "defaultLayerNames": ["PIT_FRAME", "PIT_TITLE", "PIT_TEXT", "PIT_DIM", "PIT_EXCAVATION", "PIT_WALL", "PIT_WALE", "PIT_SUPPORT", "PIT_COLUMN", "PIT_REBAR_MAIN", "PIT_REBAR_STIRRUP", "PIT_MONITOR"],
        "reviewBoundary": "Company title block, signature workflow and registered engineer approval remain project-specific.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

def _write_individual_bar_geometry_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    bars = detailing.get("individualBars", [])
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "sub_index", "host_type", "host_code", "bar_type", "diameter_mm", "grade", "shape_code", "centerline_length_m", "anchorage_length_m", "lap_length_m", "hook_length_m", "cut_length_m", "weight_kg", "point_count", "points_xyz"])
        for bar in bars:
            points = bar.get("points", [])
            point_text = ";".join(f"{p.get('x',0):.3f},{p.get('y',0):.3f},{p.get('z',0):.3f}" for p in points)
            writer.writerow([bar.get("barId"), bar.get("barMark"), bar.get("subIndex"), bar.get("hostType"), bar.get("hostCode"), bar.get("barType"), bar.get("diameterMm"), bar.get("grade"), bar.get("shapeCode"), bar.get("centerlineLengthM"), bar.get("anchorageLengthM"), bar.get("lapLengthM"), bar.get("hookLengthM"), bar.get("cutLengthM"), bar.get("weightKg"), len(points), point_text])


def _write_rebar_geometry_plan(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    bars = detailing.get("individualBars", [])[:300]
    for bar in bars:
        points = bar.get("points", [])
        if len(points) < 2:
            continue
        layer = "PIT_REBAR_STIRRUP" if bar.get("barType") == "stirrup" else "PIT_REBAR_MAIN"
        for p, q in zip(points[:-1], points[1:]):
            dxf.line(layer, float(p.get("x", 0)), float(p.get("y", 0)), float(q.get("x", 0)), float(q.get("y", 0)))
        first = points[0]
        if int(bar.get("subIndex") or 0) <= 3:
            dxf.text("PIT_TEXT", float(first.get("x", 0)) + 0.15, float(first.get("y", 0)) + 0.15, str(bar.get("barId")), 0.22)
    dxf.text("PIT_TITLE", 0, 6, "S-08 Individual rebar geometry projection: first 300 bars for CAD review", 0.45)
    dxf.text("PIT_TEXT", 0, 4.8, "Full centerline geometry is exported in individual_bar_geometry.csv; this DXF sheet is a lightweight visual index.", 0.28)
    _title_block(dxf, project, "08", "逐根钢筋几何索引图", "NTS")
    dxf.write(path)



def _write_splice_layout(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    dxf.text("PIT_TITLE", 0, 4, "S-09 Lap splice and construction-joint layout", 0.55)
    headers = ["Splice zone", "Segment", "Bar mark", "Host", "Lap(m)", "Status"]
    xs = [0, 26, 54, 82, 104, 118]
    for i, h in enumerate(headers):
        dxf.text("PIT_TITLE", xs[i], 0, h, 0.28)
    for row, item in enumerate(detailing.get("spliceSchedule", [])[:40], start=1):
        y = -row * 0.9
        vals = [item.get("spliceZoneId"), item.get("cageSegmentId"), item.get("barMark"), item.get("hostCode"), item.get("lapLengthM"), item.get("lapLocationStatus")]
        for i, value in enumerate(vals):
            dxf.text("PIT_TEXT", xs[i], y, str(value), 0.22)
    for idx, joint in enumerate(detailing.get("constructionJointPlan", [])[:10]):
        x = 5 + idx * 10
        dxf.line("PIT_HIGHLIGHT", x, -46, x, -36)
        dxf.text("PIT_TEXT", x + 0.3, -35.5, str(joint.get("jointId")), 0.18, 90)
    _title_block(dxf, project, "09", "搭接区与施工缝布置图", "NTS")
    dxf.write(path)


def _write_cage_lifting_plan(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    dxf.text("PIT_TITLE", 0, 4, "S-10 Reinforcement cage segmentation and lifting plan", 0.55)
    x0 = 0.0
    for idx, seg in enumerate(detailing.get("cageSegments", [])[:16]):
        x = x0 + (idx % 4) * 30
        y = -5 - (idx // 4) * 16
        h = max(4, min(12, float(seg.get("lengthM", 6))))
        dxf.rectangle("PIT_REBAR_MAIN", x, y-h, 8, h)
        dxf.text("PIT_TEXT", x, y+0.6, str(seg.get("segmentId")), 0.22)
        dxf.text("PIT_TEXT", x, y-h-0.8, f"W={seg.get('estimatedCageWeightT')}t  LP={seg.get('liftingPointCount')}", 0.18)
        for k in range(int(seg.get("liftingPointCount", 4))):
            dxf.circle("PIT_HIGHLIGHT", x + 1.0 + k * 1.6, y - 0.8, 0.25)
    _title_block(dxf, project, "10", "钢筋笼分节与吊装布置图", "NTS")
    dxf.write(path)


def _write_cover_conflict_check(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    dxf.text("PIT_TITLE", 0, 4, "S-11 Cover and bend-radius check sheet", 0.55)
    headers = ["Bar", "Host", "Cover req", "Cover act", "Bend R", "Status"]
    xs = [0, 22, 48, 70, 94, 116]
    for i, h in enumerate(headers):
        dxf.text("PIT_TITLE", xs[i], 0, h, 0.28)
    covers = detailing.get("coverConflictChecks", [])[:24]
    bends = {b.get("barId"): b for b in detailing.get("bendRadiusChecks", [])}
    for row, item in enumerate(covers, start=1):
        y = -row * 0.9
        bend = bends.get(item.get("barId"), {})
        vals = [item.get("barMark"), item.get("hostCode"), item.get("requiredCoverMm"), item.get("actualCoverMm"), bend.get("minimumBendRadiusMm"), item.get("status")]
        for i, value in enumerate(vals):
            dxf.text("PIT_TEXT", xs[i], y, str(value), 0.22)
    _title_block(dxf, project, "11", "保护层与弯折半径检查图", "NTS")
    dxf.write(path)


def _write_shop_signoff_sheet(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    detailing = build_rebar_detailing(project)
    dxf.text("PIT_TITLE", 0, 4, "S-12 Shop drawing signoff checklist", 0.55)
    headers = ["ID", "Item", "Status", "Evidence"]
    xs = [0, 16, 82, 112]
    for i, h in enumerate(headers):
        dxf.text("PIT_TITLE", xs[i], 0, h, 0.28)
    for row, item in enumerate(detailing.get("signoffChecklist", []), start=1):
        y = -row * 1.2
        vals = [item.get("id"), item.get("label"), item.get("status"), item.get("evidenceCount")]
        for i, value in enumerate(vals):
            dxf.text("PIT_TEXT", xs[i], y, str(value), 0.24)
    readiness = detailing.get("shopDrawingReadiness", {})
    dxf.text("PIT_TEXT", 0, -12, f"Readiness: {readiness.get('status')} / softwareCompletion={readiness.get('softwareCompletion')}", 0.28)
    dxf.text("PIT_TEXT", 0, -14, str(readiness.get("remainingHumanAction", "Professional signoff required.")), 0.24)
    _title_block(dxf, project, "12", "钢筋施工详图签审清单", "NTS")
    dxf.write(path)


def _write_cage_segment_schedule_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["segment_id", "host_code", "bottom_elevation", "top_elevation", "length_m", "splice_overlap_m", "estimated_cage_weight_t", "lifting_point_count", "status"])
        for item in detailing.get("cageSegments", []):
            writer.writerow([item.get("segmentId"), item.get("hostCode"), item.get("bottomElevation"), item.get("topElevation"), item.get("lengthM"), item.get("spliceOverlapM"), item.get("estimatedCageWeightT"), item.get("liftingPointCount"), item.get("status")])


def _write_splice_schedule_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "host_code", "splice_zone_id", "cage_segment_id", "lap_length_m", "lap_location_status"])
        for item in detailing.get("spliceSchedule", []):
            writer.writerow([item.get("barId"), item.get("barMark"), item.get("hostCode"), item.get("spliceZoneId"), item.get("cageSegmentId"), item.get("lapLengthM"), item.get("lapLocationStatus")])


def _write_cover_conflict_check_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "host_code", "required_cover_mm", "actual_cover_mm", "status"])
        for item in detailing.get("coverConflictChecks", []):
            writer.writerow([item.get("barId"), item.get("barMark"), item.get("hostCode"), item.get("requiredCoverMm"), item.get("actualCoverMm"), item.get("status")])


def _write_shop_signoff_checklist_csv(project: Project, path: Path) -> None:
    detailing = build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "item", "label", "status", "evidence_count"])
        for item in detailing.get("signoffChecklist", []):
            writer.writerow([item.get("id"), item.get("item"), item.get("label"), item.get("status"), item.get("evidenceCount")])

def export_construction_cad_package(project: Project, output_dir: str | Path) -> Path:
    out = Path(output_dir)
    package_dir = out / f"{project.id}_cad_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    files = [
        package_dir / "S-01_support_plan.dxf",
        package_dir / "S-02_wall_rebar_cage.dxf",
        package_dir / "S-03_support_wale_node_detail.dxf",
        package_dir / "S-04_excavation_section.dxf",
        package_dir / "S-05_column_pile_detail.dxf",
        package_dir / "S-06_monitoring_plan.dxf",
        package_dir / "S-07_rebar_bending_schedule.dxf",
        package_dir / "S-08_individual_rebar_geometry.dxf",
        package_dir / "S-09_lap_splice_layout.dxf",
        package_dir / "S-10_cage_segment_lifting_plan.dxf",
        package_dir / "S-11_cover_bend_check.dxf",
        package_dir / "S-12_shop_drawing_signoff_checklist.dxf",
        package_dir / "drawing_register.csv",
        package_dir / "rebar_schedule.csv",
        package_dir / "material_schedule.csv",
        package_dir / "rebar_bending_schedule.csv",
        package_dir / "individual_bar_geometry.csv",
        package_dir / "cage_segment_schedule.csv",
        package_dir / "splice_schedule.csv",
        package_dir / "cover_conflict_check.csv",
        package_dir / "shop_drawing_checklist.csv",
        package_dir / "delivery_consistency_matrix.csv",
        package_dir / "enterprise_template_manifest.json",
    ]
    _write_support_plan(project, files[0])
    _write_wall_rebar_detail(project, files[1])
    _write_node_detail(project, files[2])
    _write_excavation_section(project, files[3])
    _write_column_pile_detail(project, files[4])
    _write_monitoring_plan(project, files[5])
    _write_rebar_bending_schedule(project, files[6])
    _write_rebar_geometry_plan(project, files[7])
    _write_splice_layout(project, files[8])
    _write_cage_lifting_plan(project, files[9])
    _write_cover_conflict_check(project, files[10])
    _write_shop_signoff_sheet(project, files[11])
    _write_drawing_register(project, files[12])
    _write_rebar_schedule(project, files[13])
    _write_material_schedule(project, files[14])
    _write_rebar_bending_schedule_csv(project, files[15])
    _write_individual_bar_geometry_csv(project, files[16])
    _write_cage_segment_schedule_csv(project, files[17])
    _write_splice_schedule_csv(project, files[18])
    _write_cover_conflict_check_csv(project, files[19])
    _write_shop_signoff_checklist_csv(project, files[20])
    _write_delivery_consistency_matrix(project, files[21])
    _write_enterprise_template_manifest(project, files[22])
    manifest = package_dir / "drawing_package_manifest.json"
    import json
    manifest.write_text(json.dumps({
        "projectId": project.id,
        "packageType": "V2.5.0 formal CAD drawing-set interface with shop-detailing sheets",
        "sheetCount": 12,
        "tables": ["drawing_register.csv", "rebar_schedule.csv", "material_schedule.csv", "rebar_bending_schedule.csv", "individual_bar_geometry.csv", "cage_segment_schedule.csv", "splice_schedule.csv", "cover_conflict_check.csv", "shop_drawing_checklist.csv", "delivery_consistency_matrix.csv"],
        "template": "enterprise_template_manifest.json",
        "softwareModuleCompletion": 100,
        "cadTemplate": _cad_template(project),
        "officialIssueBoundary": "Generated drawings are editable CAD deliverables. Company title blocks, signature workflow and registered engineer review are still required before sealed issue.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = package_dir / "README.txt"
    readme.write_text(
        "PitGuard V2.5.0 construction CAD drawing-set package\n"
        "Format: AutoCAD R12 DXF + UTF-8 BOM CSV schedules + JSON manifest.\n"
        "Sheets: S-01 support plan, S-02 diaphragm-wall rebar cage, S-03 support-wale node, S-04 excavation section, S-05 column pile, S-06 monitoring plan, S-07 rebar bending, S-08 individual rebar geometry, S-09 splice layout, S-10 cage lifting, S-11 cover/bend check, S-12 signoff checklist.\n"
        "Schedules: drawing register, rebar schedule, material schedule, rebar bending, individual geometry, cage segment, splice, cover conflict, shop checklist and delivery consistency matrix.\n"
        "Layers: PIT_EXCAVATION, PIT_WALL, PIT_WALE, PIT_SUPPORT, PIT_COLUMN, PIT_PILE, PIT_REBAR_MAIN, PIT_REBAR_STIRRUP, PIT_MONITOR, PIT_TEXT, PIT_DIM.\n"
        "Status: formal CAD drawing-set interface. Final sealed construction drawings require company title block, designer/checker signatures, anchorage/lap/hook review and registered engineer approval.\n",
        encoding="utf-8",
    )
    zip_path = out / f"{project.id}_construction_cad_formal_set.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in [*files, manifest, readme]:
            zf.write(file, arcname=file.name)
    return zip_path

def export_construction_svg_package(project: Project, output_dir: str | Path) -> Path:
    out = Path(output_dir)
    svg_dir = out / f"{project.id}_svg_sheets"
    sheets = generate_construction_detail_sheets(project, svg_dir)
    zip_path = out / f"{project.id}_construction_svg_sheets.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for sheet in sheets:
            if sheet.file_path:
                path = Path(sheet.file_path)
                if path.exists():
                    zf.write(path, arcname=path.name)
    return zip_path
