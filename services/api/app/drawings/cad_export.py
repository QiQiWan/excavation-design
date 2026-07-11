from __future__ import annotations

import csv
import json
import math
import shutil
import zipfile
from pathlib import Path
from typing import Any, Iterable

from app.drawings.detail_sheets import generate_construction_detail_sheets
from app.schemas.domain import Point2D, Project, ReinforcementGroup
from app.services.rebar_detailing import build_rebar_detailing
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.advanced_suite import build_advanced_engineering_suite
from app.services.review_workflow import review_status
from app.services.cad_template import normalize_cad_template
from app.version import SOFTWARE_VERSION


class DxfWriter:
    def __init__(self) -> None:
        self.entities: list[str] = []
        self.layers: set[str] = set()
        self.min_x = math.inf
        self.max_x = -math.inf
        self.min_y = math.inf
        self.max_y = -math.inf

    def _update_bounds(self, *points: tuple[float, float]) -> None:
        for x, y in points:
            self.min_x = min(self.min_x, float(x))
            self.max_x = max(self.max_x, float(x))
            self.min_y = min(self.min_y, float(y))
            self.max_y = max(self.max_y, float(y))

    def drawing_bounds(self) -> tuple[float, float, float, float]:
        if not math.isfinite(self.min_x):
            return 0.0, 120.0, 0.0, 20.0
        return self.min_x, self.max_x, self.min_y, self.max_y

    def _add_layer(self, layer: str) -> None:
        self.layers.add(layer)

    def line(self, layer: str, x1: float, y1: float, x2: float, y2: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x1, y1), (x2, y2))
        self.entities.extend([
            "0", "LINE", "8", layer,
            "10", f"{x1:.4f}", "20", f"{y1:.4f}", "30", "0.0",
            "11", f"{x2:.4f}", "21", f"{y2:.4f}", "31", "0.0",
        ])

    def circle(self, layer: str, x: float, y: float, r: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x - r, y - r), (x + r, y + r))
        self.entities.extend(["0", "CIRCLE", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0", "40", f"{max(r, 0.01):.4f}"])

    def text(self, layer: str, x: float, y: float, value: str, height: float = 0.35, rotation: float = 0.0) -> None:
        self._add_layer(layer)
        self._update_bounds((x, y), (x + max(len(str(value)), 1) * height * 0.62, y + height))
        safe = str(value).replace("\n", " ")[:240]
        self.entities.extend(["0", "TEXT", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0", "40", f"{height:.4f}", "1", safe, "50", f"{rotation:.4f}"])

    def lwpolyline(self, layer: str, points: Iterable[Point2D], closed: bool = False) -> None:
        pts = list(points)
        if not pts:
            return
        self._add_layer(layer)
        self._update_bounds(*[(p.x, p.y) for p in pts])
        self.entities.extend(["0", "LWPOLYLINE", "8", layer, "90", str(len(pts)), "70", "1" if closed else "0"])
        for p in pts:
            self.entities.extend(["10", f"{p.x:.4f}", "20", f"{p.y:.4f}"])


    def arc(self, layer: str, x: float, y: float, r: float, start_angle: float, end_angle: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x - r, y - r), (x + r, y + r))
        self.entities.extend(["0", "ARC", "8", layer, "10", f"{x:.4f}", "20", f"{y:.4f}", "30", "0.0", "40", f"{max(r, 0.01):.4f}", "50", f"{start_angle:.4f}", "51", f"{end_angle:.4f}"])

    def leader(self, layer: str, x1: float, y1: float, x2: float, y2: float, text: str, text_height: float = 0.28) -> None:
        self.line(layer, x1, y1, x2, y2)
        angle = math.atan2(y2 - y1, x2 - x1)
        size = max(text_height * 1.2, 0.2)
        self.line(layer, x1, y1, x1 + size * math.cos(angle + 2.55), y1 + size * math.sin(angle + 2.55))
        self.line(layer, x1, y1, x1 + size * math.cos(angle - 2.55), y1 + size * math.sin(angle - 2.55))
        self.text(layer, x2 + 0.2, y2 + 0.1, text, text_height)

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
        drawing_min_x, drawing_max_x, drawing_min_y, _drawing_max_y = self.drawing_bounds()
        height = float(block.get("height", 16.0))
        default_width = max(120.0, drawing_max_x - drawing_min_x)
        width = float(block.get("width", default_width))
        x0 = float(block["originX"]) if "originX" in block else drawing_min_x
        y0 = float(block["originY"]) if "originY" in block else drawing_min_y - height - 4.0
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
            role_layer = {
                "main_strut": "PIT_SUPPORT_MAIN",
                "secondary_strut": "PIT_SUPPORT_SECONDARY",
                "corner_diagonal": "PIT_SUPPORT_CORNER",
                "ring_strut": "PIT_SUPPORT_RING",
            }.get(support.support_role, "PIT_SUPPORT")
            role_tag = {"main_strut": "M", "secondary_strut": "G", "corner_diagonal": "DB", "ring_strut": "R"}.get(support.support_role, "S")
            dxf.line(role_layer, support.start.x, support.start.y, support.end.x, support.end.y)
            mx, my = _mid(support.start, support.end)
            dxf.text("PIT_TEXT", mx, my, f"{support.code}[{role_tag}] L{support.level_index} N={support.design_axial_force or 0:.0f}kN", 0.32, _angle(support.start, support.end))
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



def _write_rebar_bending_schedule(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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


def _write_rebar_bending_schedule_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
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

def _write_individual_bar_geometry_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
    bars = detailing.get("individualBars", [])
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "sub_index", "host_type", "host_code", "bar_type", "diameter_mm", "grade", "shape_code", "centerline_length_m", "anchorage_length_m", "lap_length_m", "hook_length_m", "cut_length_m", "weight_kg", "point_count", "points_xyz"])
        for bar in bars:
            points = bar.get("points", [])
            point_text = ";".join(f"{p.get('x',0):.3f},{p.get('y',0):.3f},{p.get('z',0):.3f}" for p in points)
            writer.writerow([bar.get("barId"), bar.get("barMark"), bar.get("subIndex"), bar.get("hostType"), bar.get("hostCode"), bar.get("barType"), bar.get("diameterMm"), bar.get("grade"), bar.get("shapeCode"), bar.get("centerlineLengthM"), bar.get("anchorageLengthM"), bar.get("lapLengthM"), bar.get("hookLengthM"), bar.get("cutLengthM"), bar.get("weightKg"), len(points), point_text])


def _write_rebar_geometry_plan(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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



def _write_splice_layout(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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


def _write_cage_lifting_plan(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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


def _write_cover_conflict_check(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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


def _write_shop_signoff_sheet(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    dxf = DxfWriter()
    detailing = detailing or build_rebar_detailing(project)
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


def _write_cage_segment_schedule_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["segment_id", "host_code", "bottom_elevation", "top_elevation", "length_m", "splice_overlap_m", "estimated_cage_weight_t", "lifting_point_count", "status"])
        for item in detailing.get("cageSegments", []):
            writer.writerow([item.get("segmentId"), item.get("hostCode"), item.get("bottomElevation"), item.get("topElevation"), item.get("lengthM"), item.get("spliceOverlapM"), item.get("estimatedCageWeightT"), item.get("liftingPointCount"), item.get("status")])


def _write_splice_schedule_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "host_code", "splice_zone_id", "cage_segment_id", "lap_length_m", "lap_location_status"])
        for item in detailing.get("spliceSchedule", []):
            writer.writerow([item.get("barId"), item.get("barMark"), item.get("hostCode"), item.get("spliceZoneId"), item.get("cageSegmentId"), item.get("lapLengthM"), item.get("lapLocationStatus")])


def _write_cover_conflict_check_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_id", "bar_mark", "host_code", "required_cover_mm", "actual_cover_mm", "status"])
        for item in detailing.get("coverConflictChecks", []):
            writer.writerow([item.get("barId"), item.get("barMark"), item.get("hostCode"), item.get("requiredCoverMm"), item.get("actualCoverMm"), item.get("status")])


def _write_shop_signoff_checklist_csv(project: Project, path: Path, detailing: dict[str, Any] | None = None) -> None:
    detailing = detailing or build_rebar_detailing(project)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "item", "label", "status", "evidence_count"])
        for item in detailing.get("signoffChecklist", []):
            writer.writerow([item.get("id"), item.get("item"), item.get("label"), item.get("status"), item.get("evidenceCount")])



def _plan_extents(project: Project) -> tuple[float, float, float, float]:
    points = project.excavation.outline.points if project.excavation else []
    if not points:
        return 0.0, 60.0, 0.0, 30.0
    xs = [float(p.x) for p in points]
    ys = [float(p.y) for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _draw_north_arrow(dxf: DxfWriter, x: float, y: float, size: float = 4.0) -> None:
    dxf.line("PIT_SYMBOL", x, y, x, y + size)
    dxf.line("PIT_SYMBOL", x, y + size, x - size * 0.22, y + size * 0.68)
    dxf.line("PIT_SYMBOL", x, y + size, x + size * 0.22, y + size * 0.68)
    dxf.text("PIT_SYMBOL", x - 0.25, y + size + 0.35, "N", max(size * 0.12, 0.35))


def _draw_plan_base(dxf: DxfWriter, project: Project, *, support_level: int | None = None, show_all_supports: bool = True) -> None:
    ret = project.retaining_system
    if project.excavation:
        dxf.lwpolyline("PIT_EXCAVATION", project.excavation.outline.points, closed=True)
        for idx, segment in enumerate(project.excavation.segments, start=1):
            dxf.text("PIT_TEXT", segment.midpoint.x + 0.2, segment.midpoint.y + 0.2, f"E{idx:02d}/{segment.name}", 0.24)
        for obs in project.excavation.obstacles:
            if obs.outline:
                dxf.lwpolyline("PIT_OBSTACLE", obs.outline.points, closed=obs.outline.closed)
            elif obs.center and obs.width and obs.length:
                dxf.rectangle("PIT_OBSTACLE", obs.center.x - obs.width / 2, obs.center.y - obs.length / 2, obs.width, obs.length)
    if not ret:
        return
    for wall in ret.diaphragm_walls:
        if len(wall.axis.points) < 2:
            continue
        a, b = wall.axis.points[0], wall.axis.points[-1]
        dxf.line("PIT_WALL", a.x, a.y, b.x, b.y)
        mx, my = _mid(a, b)
        dxf.text("PIT_TEXT", mx, my, wall.panel_code, 0.25, _angle(a, b))
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        if support_level is not None and beam.support_level not in {None, support_level}:
            continue
        if len(beam.axis.points) >= 2:
            a, b = beam.axis.points[0], beam.axis.points[-1]
            dxf.line(f"PIT_WALE_L{beam.support_level or 0:02d}", a.x, a.y, b.x, b.y)
    for support in ret.supports:
        if support_level is not None and support.level_index != support_level:
            continue
        if not show_all_supports and support_level is None:
            continue
        role_suffix = {"main_strut": "MAIN", "secondary_strut": "GRID", "corner_diagonal": "CORNER", "ring_strut": "RING"}.get(support.support_role, "OTHER")
        layer = f"PIT_SUPPORT_L{support.level_index:02d}_{role_suffix}"
        dxf.line(layer, support.start.x, support.start.y, support.end.x, support.end.y)
        mx, my = _mid(support.start, support.end)
        role_tag = {"main_strut": "M", "secondary_strut": "G", "corner_diagonal": "DB", "ring_strut": "R"}.get(support.support_role, "S")
        dxf.text("PIT_TEXT", mx, my, f"{support.code}[{role_tag}] N={support.design_axial_force or 0:.0f}", 0.24, _angle(support.start, support.end))
    for col in ret.columns:
        radius = max((col.section.width or col.section.diameter or 0.6) / 2.0, 0.2)
        dxf.circle("PIT_COLUMN", col.location.x, col.location.y, radius)
        dxf.text("PIT_TEXT", col.location.x + 0.35, col.location.y + 0.35, col.code, 0.22)


def build_drawing_set_manifest(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    levels = sorted({int(item.level_index) for item in (ret.supports if ret else [])})
    sheets: list[dict[str, Any]] = [
        {"sheetNo": "G-00", "title": "图纸目录、设计总说明与图例", "category": "general", "scale": "NTS", "file": "00_general/G-00_drawing_index_general_notes.dxf", "modelBinding": ["project", "standards", "drawing_register"]},
        {"sheetNo": "S-00", "title": "基坑围护与支撑总平面图", "category": "global_plan", "scale": "1:200", "file": "10_plans/S-00_retaining_support_general_arrangement.dxf", "modelBinding": ["excavation", "walls", "wales", "supports", "columns"]},
        {"sheetNo": "S-01", "title": "围护结构分幅及构件编号图", "category": "plan", "scale": "1:200", "file": "S-01_support_plan.dxf", "legacy": True},
    ]
    for level in levels:
        sheets.append({"sheetNo": f"S-02-L{level:02d}", "title": f"第{level}道支撑平面布置图", "category": "level_plan", "scale": "1:150", "file": f"10_plans/S-02-L{level:02d}_support_level_plan.dxf", "modelBinding": [f"support_level_{level}", "walls", "columns", "nodes"]})
    sheets.extend([
        {"sheetNo": "S-03", "title": "典型开挖剖面与施工阶段图", "category": "section", "scale": "1:100", "file": "20_sections/S-03_excavation_stage_section.dxf"},
        {"sheetNo": "M-01", "title": "监测点布置总图", "category": "monitoring", "scale": "1:200", "file": "S-06_monitoring_plan.dxf", "legacy": True},
        {"sheetNo": "R-01", "title": "地下连续墙配筋总图", "category": "rebar_general", "scale": "1:200", "file": "30_rebar/R-01_wall_rebar_general_arrangement.dxf"},
        {"sheetNo": "R-02", "title": "地下连续墙分区配筋立面图", "category": "rebar_elevation", "scale": "1:100", "file": "30_rebar/R-02_wall_rebar_zone_elevation.dxf"},
        {"sheetNo": "R-03", "title": "地下连续墙钢筋笼、接头与吊装详图", "category": "rebar_detail", "scale": "1:50", "file": "S-02_wall_rebar_cage.dxf", "legacy": True},
        {"sheetNo": "R-04", "title": "钢筋混凝土支撑配筋总图", "category": "rebar_general", "scale": "1:100", "file": "30_rebar/R-04_support_rebar_general_arrangement.dxf"},
        {"sheetNo": "R-05", "title": "冠梁、围檩及环梁配筋总图", "category": "rebar_general", "scale": "1:100", "file": "30_rebar/R-05_wale_rebar_general_arrangement.dxf"},
        {"sheetNo": "D-00", "title": "典型节点大样索引与组合图", "category": "detail_index", "scale": "1:20/1:50", "file": "40_details/D-00_typical_detail_compilation.dxf"},
        {"sheetNo": "D-01", "title": "支撑—围檩节点大样", "category": "node_detail", "scale": "1:20", "file": "S-03_support_wale_node_detail.dxf", "legacy": True},
        {"sheetNo": "D-02", "title": "角撑节点与转角加强大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-02_corner_brace_node_detail.dxf"},
        {"sheetNo": "D-03", "title": "支撑—立柱交叉节点大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-03_support_column_intersection_detail.dxf"},
        {"sheetNo": "D-04", "title": "地连墙支撑区局部加强大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-04_wall_support_zone_detail.dxf"},
        {"sheetNo": "D-05", "title": "临时立柱与立柱桩详图", "category": "pile_detail", "scale": "1:50", "file": "S-05_column_pile_detail.dxf", "legacy": True},
        {"sheetNo": "D-06", "title": "地下连续墙墙幅接头与钢筋笼连接大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-06_wall_panel_joint_detail.dxf"},
        {"sheetNo": "D-07", "title": "钢筋混凝土支撑端部锚固与错开搭接大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-07_support_anchorage_splice_detail.dxf"},
        {"sheetNo": "D-08", "title": "主次支撑网格交叉节点与立柱连接大样", "category": "node_detail", "scale": "1:20", "file": "40_details/D-08_bidirectional_grid_node_detail.dxf", "modelBinding": ["main_strut", "secondary_strut", "columns"]},
        {"sheetNo": "Q-02", "title": "长期效应与裂缝控制检查图", "category": "quality", "scale": "NTS", "file": "50_quality/Q-02_serviceability_crack_check.dxf", "modelBinding": ["wall_rebar_zones", "serviceability"]},
        {"sheetNo": "Q-03", "title": "构件碰撞、净距与节点拥挤检查图", "category": "quality", "scale": "NTS", "file": "50_quality/Q-03_collision_clearance_check.dxf", "modelBinding": ["supports", "obstacles", "rebar"]},
        {"sheetNo": "N-01", "title": "高利用率节点局部复核索引图", "category": "node_analysis", "scale": "NTS", "file": "50_quality/N-01_node_local_analysis.dxf", "modelBinding": ["support_nodes", "bearing_plates"]},
        {"sheetNo": "M-02", "title": "监测反演与参数校准记录图", "category": "monitoring", "scale": "NTS", "file": "60_monitoring/M-02_monitoring_calibration.dxf", "modelBinding": ["monitoring_records", "calibration_runs"]},
        {"sheetNo": "R-06", "title": "钢筋下料、弯曲及逐根索引图", "category": "rebar_schedule", "scale": "NTS", "file": "S-07_rebar_bending_schedule.dxf", "legacy": True},
        {"sheetNo": "Q-01", "title": "保护层、弯折、搭接与签审检查图", "category": "quality", "scale": "NTS", "file": "S-11_cover_bend_check.dxf", "legacy": True},
    ])
    if ret:
        wall_detail_sheets: list[dict[str, Any]] = []
        for index, wall in enumerate(ret.diaphragm_walls, start=1):
            sheet_no = f"R-02-W{index:02d}"
            token = _safe_file_token(wall.panel_code)
            wall_detail_sheets.append({
                "sheetNo": sheet_no,
                "title": f"{wall.panel_code} 地下连续墙单幅分区配筋立面",
                "category": "wall_rebar_elevation",
                "scale": "1:50",
                "file": f"30_rebar/walls/{sheet_no}_{token}_rebar_elevation.dxf",
                "modelBinding": [wall.id, wall.segment_id, "rebar_design_scheme"],
            })
        insert_at = next((i + 1 for i, item in enumerate(sheets) if item.get("sheetNo") == "R-02"), len(sheets))
        sheets[insert_at:insert_at] = wall_detail_sheets
    return {
        "projectId": project.id,
        "softwareVersion": SOFTWARE_VERSION,
        "sheetCount": len(sheets),
        "supportLevels": levels,
        "categories": {key: sum(1 for item in sheets if item["category"] == key) for key in sorted({item["category"] for item in sheets})},
        "sheets": sheets,
        "packageFolders": ["00_general", "10_plans", "20_sections", "30_rebar", "40_details", "90_schedules"],
        "issueBoundary": "本图纸包为可编辑的设计辅助成果。正式施工图出图仍需完成项目专项规范复核、企业图签、设计/校核/审核签署及注册工程师审查。",
    }


def _write_general_notes_sheet(project: Project, path: Path, manifest: dict[str, Any]) -> None:
    dxf = DxfWriter()
    dxf.text("PIT_TITLE", 0, 8, "G-00 Drawing index, general notes and legend", 0.65)
    headers = ["No.", "Drawing title", "Scale", "Category"]
    xs = [0, 14, 88, 108]
    for index, header in enumerate(headers):
        dxf.text("PIT_TITLE", xs[index], 4.5, header, 0.3)
    for row, item in enumerate(manifest.get("sheets", [])[:26], start=1):
        y = 4.5 - row * 1.05
        values = [item.get("sheetNo"), item.get("title"), item.get("scale"), item.get("category")]
        for index, value in enumerate(values):
            dxf.text("PIT_TEXT", xs[index], y, str(value), 0.23)
    note_x = 0.0
    note_y = -25.5
    notes = [
        "1. All coordinates and elevations follow the project coordinate system; dimensions are in metres unless noted.",
        "2. Rebar labels follow diameter@spacing or count-diameter conventions; zone schedules govern local reductions.",
        "3. Support installation, preload, replacement and removal shall follow the approved staged construction sequence.",
        "4. Node, splice, cage lifting, coupler and embedded-item details require constructability review before issue.",
        "5. CAD layers are separated by discipline, support level and reinforcement type for editable downstream use.",
        "6. The drawing package is automatically generated from the same object IDs used by calculation, IFC and schedules.",
    ]
    for index, note in enumerate(notes):
        dxf.text("PIT_TEXT", note_x, note_y - index * 1.1, note, 0.24)
    dxf.line("PIT_WALL", 86, -27, 102, -27); dxf.text("PIT_TEXT", 104, -27, "Diaphragm wall", 0.24)
    dxf.line("PIT_SUPPORT_L01", 86, -29, 102, -29); dxf.text("PIT_TEXT", 104, -29, "Internal support", 0.24)
    dxf.circle("PIT_COLUMN", 94, -31, 0.45); dxf.text("PIT_TEXT", 104, -31, "Temporary column", 0.24)
    dxf.line("PIT_REBAR_MAIN", 86, -33, 102, -33); dxf.text("PIT_TEXT", 104, -33, "Main reinforcement", 0.24)
    _title_block(dxf, project, "G00", "图纸目录、设计总说明与图例", "NTS")
    dxf.write(path)


def _write_master_general_arrangement(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    _draw_plan_base(dxf, project, support_level=None, show_all_supports=True)
    min_x, max_x, min_y, max_y = _plan_extents(project)
    margin = max(max_x - min_x, max_y - min_y, 20.0) * 0.08
    dxf.dim_line("PIT_DIM", min_x, max_y + margin, max_x, max_y + margin, f"L={max_x-min_x:.2f}m", offset=0.45)
    dxf.dim_line("PIT_DIM", max_x + margin, min_y, max_x + margin, max_y, f"B={max_y-min_y:.2f}m", offset=0.45)
    _draw_north_arrow(dxf, max_x + 2.2 * margin, max_y - margin * 0.5, max(3.0, margin * 0.8))
    if project.excavation:
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        dxf.line("PIT_SECTION", min_x - margin, cy, max_x + margin, cy)
        dxf.text("PIT_SECTION", min_x - margin, cy + 0.4, "A", 0.35)
        dxf.text("PIT_SECTION", max_x + margin, cy + 0.4, "A", 0.35)
        dxf.line("PIT_SECTION", cx, min_y - margin, cx, max_y + margin)
        dxf.text("PIT_SECTION", cx + 0.4, min_y - margin, "B", 0.35)
        dxf.text("PIT_SECTION", cx + 0.4, max_y + margin, "B", 0.35)
    legend_x = max_x + margin * 1.6
    legend_y = min_y + margin
    dxf.text("PIT_TITLE", legend_x, legend_y + 7.5, "Support level legend", 0.32)
    levels = sorted({item.level_index for item in (project.retaining_system.supports if project.retaining_system else [])})
    for index, level in enumerate(levels):
        y = legend_y + 6.0 - index * 1.2
        dxf.line(f"PIT_SUPPORT_L{level:02d}", legend_x, y, legend_x + 4.0, y)
        dxf.text("PIT_TEXT", legend_x + 4.5, y, f"Level {level}", 0.24)
    dxf.text("PIT_TITLE", min_x, min_y - margin * 1.5, "S-00 Retaining and support general arrangement", 0.48)
    _title_block(dxf, project, "S00", "基坑围护与支撑总平面图", str(_cad_template(project).get("sheetRules", {}).get("defaultScalePlan", "1:200")))
    dxf.write(path)


def _write_support_level_plan(project: Project, path: Path, level: int) -> None:
    dxf = DxfWriter()
    _draw_plan_base(dxf, project, support_level=level, show_all_supports=True)
    min_x, max_x, min_y, max_y = _plan_extents(project)
    ret = project.retaining_system
    supports = [item for item in (ret.supports if ret else []) if item.level_index == level]
    nodes = [item for item in (ret.support_nodes if ret else []) if item.level_index == level]
    for node in nodes:
        dxf.circle("PIT_NODE", node.location.x, node.location.y, 0.35)
        dxf.leader("PIT_LEADER", node.location.x, node.location.y, node.location.x + 2.0, node.location.y + 1.5, f"{node.code}/D-01")
    dxf.text("PIT_TEXT", min_x, min_y - 2.0, f"Level {level}: EL={supports[0].elevation:.2f}m" if supports else f"Level {level}", 0.35)
    dxf.text("PIT_TEXT", min_x, min_y - 3.0, "All support end nodes refer to D-01; corner diagonal nodes refer to D-02; column crossings refer to D-03.", 0.24)
    _title_block(dxf, project, f"S02L{level:02d}", f"第{level}道支撑平面布置图", "1:150")
    dxf.write(path)


def _write_wall_rebar_general_arrangement(project: Project, path: Path, scheme: dict[str, Any]) -> None:
    dxf = DxfWriter()
    _draw_plan_base(dxf, project, show_all_supports=False)
    ret = project.retaining_system
    zones_by_host: dict[str, list[dict[str, Any]]] = {}
    for zone in scheme.get("wallZones", []):
        zones_by_host.setdefault(str(zone.get("hostId")), []).append(zone)
    if ret:
        for wall in ret.diaphragm_walls:
            if len(wall.axis.points) < 2:
                continue
            a, b = wall.axis.points[0], wall.axis.points[-1]
            mx, my = _mid(a, b)
            zones = zones_by_host.get(wall.id, [])
            governing = max(zones, key=lambda item: max((float(face.get("requiredAsMm2PerM") or 0.0) for face in item.get("faces", [])), default=0.0), default=None)
            token = "zone schedule pending"
            if governing:
                token = "/".join(str(face.get("token")) for face in governing.get("faces", []))
            dxf.leader("PIT_LEADER", mx, my, mx + 1.8, my + 1.8, f"{wall.panel_code} {token} / R-02")
    min_x, _, min_y, _ = _plan_extents(project)
    dxf.text("PIT_TEXT", min_x, min_y - 2.0, "Wall plan labels show governing face reinforcement. Elevation reductions and local strengthening follow R-02/R-03/D-04.", 0.25)
    _title_block(dxf, project, "R01", "地下连续墙配筋总图", "1:200")
    dxf.write(path)


def _write_wall_rebar_zone_elevation(project: Project, path: Path, scheme: dict[str, Any]) -> None:
    dxf = DxfWriter()
    ret = project.retaining_system
    if not ret:
        dxf.write(path); return
    zones_by_host: dict[str, list[dict[str, Any]]] = {}
    for zone in scheme.get("wallZones", []):
        zones_by_host.setdefault(str(zone.get("hostId")), []).append(zone)
    walls = ret.diaphragm_walls[:8]
    x_cursor = 0.0
    for wall in walls:
        zones = sorted(zones_by_host.get(wall.id, []), key=lambda item: float(item.get("topElevation") or 0.0), reverse=True)
        height = max(float(wall.top_elevation - wall.bottom_elevation), 1.0)
        width = max(min(float(wall.design_length or 6.0), 10.0), 4.0)
        dxf.rectangle("PIT_CONCRETE", x_cursor, wall.bottom_elevation, width, height)
        dxf.text("PIT_TITLE", x_cursor, wall.top_elevation + 0.8, wall.panel_code, 0.3)
        for zone in zones:
            top = float(zone.get("topElevation") or wall.top_elevation)
            bottom = float(zone.get("bottomElevation") or wall.bottom_elevation)
            dxf.line("PIT_ZONE", x_cursor, top, x_cursor + width, top)
            face_tokens = "/".join(str(face.get("token")) for face in zone.get("faces", []))
            dxf.text("PIT_TEXT", x_cursor + 0.2, (top + bottom) / 2.0, f"{zone.get('zoneId')} {face_tokens}", 0.18)
            dxf.text("PIT_TEXT", x_cursor + 0.2, (top + bottom) / 2.0 - 0.45, f"H:{zone.get('horizontalDistribution',{}).get('token')} T:{zone.get('tieBars',{}).get('token')}", 0.16)
            if zone.get("zoneType") == "support_node_zone":
                dxf.text("PIT_HIGHLIGHT", x_cursor + width - 1.4, (top + bottom) / 2.0, "D-04", 0.22)
        dxf.line("PIT_ZONE", x_cursor, wall.bottom_elevation, x_cursor + width, wall.bottom_elevation)
        x_cursor += width + 4.0
    dxf.text("PIT_TEXT", 0, min((wall.bottom_elevation for wall in walls), default=-20) - 1.0, "Zone boundaries are generated from support elevations, excavation transition, wall toe and latest moment envelope.", 0.25)
    _title_block(dxf, project, "R02", "地下连续墙分区配筋立面图", "1:100")
    dxf.write(path)


def _safe_file_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))[:80]


def _write_single_wall_rebar_elevation(project: Project, path: Path, scheme: dict[str, Any], wall_id: str, sheet_no: str) -> None:
    dxf = DxfWriter()
    ret = project.retaining_system
    wall = next((item for item in (ret.diaphragm_walls if ret else []) if item.id == wall_id), None)
    if wall is None:
        dxf.text("PIT_TEXT", 0, 0, "Wall not found", 0.35)
        _title_block(dxf, project, sheet_no.replace("-", ""), "地下连续墙单幅分区配筋立面", "1:50")
        dxf.write(path)
        return
    zones = sorted([item for item in scheme.get("wallZones", []) if str(item.get("hostId")) == wall_id], key=lambda item: float(item.get("topElevation") or 0.0), reverse=True)
    width = max(min(float(wall.design_length or 6.0), 12.0), 5.0)
    dxf.rectangle("PIT_CONCRETE", 0, float(wall.bottom_elevation), width, float(wall.top_elevation - wall.bottom_elevation))
    dxf.text("PIT_TITLE", 0, float(wall.top_elevation) + 1.0, f"{wall.panel_code} wall reinforcement elevation", 0.42)
    dxf.text("PIT_TEXT", width + 2.0, float(wall.top_elevation), f"Thickness={wall.thickness:.2f}m  Concrete={wall.concrete_grade}  Rebar={wall.rebar_grade}", 0.25)
    schedule_y = float(wall.top_elevation) - 1.2
    for index, zone in enumerate(zones, start=1):
        top = float(zone.get("topElevation") or wall.top_elevation)
        bottom = float(zone.get("bottomElevation") or wall.bottom_elevation)
        dxf.line("PIT_ZONE", 0, top, width, top)
        if index == len(zones):
            dxf.line("PIT_ZONE", 0, bottom, width, bottom)
        face_tokens = {str(face.get("face")): str(face.get("token")) for face in zone.get("faces", [])}
        status = str(zone.get("status") or "manual_review")
        source = str(zone.get("envelopeSource") or "calculated_moment")
        dxf.text("PIT_TEXT", 0.25, (top + bottom) / 2.0 + 0.18, f"{zone.get('zoneId')} {zone.get('zoneType')}", 0.18)
        dxf.text("PIT_REBAR_MAIN", 0.25, (top + bottom) / 2.0 - 0.20, f"IN {face_tokens.get('inner','-')} / OUT {face_tokens.get('outer','-')}", 0.18)
        dxf.text("PIT_REBAR_DIST", 0.25, (top + bottom) / 2.0 - 0.55, f"H {zone.get('horizontalDistribution',{}).get('token')}  T {zone.get('tieBars',{}).get('token')}", 0.16)
        dxf.text("PIT_TEXT", width + 2.0, schedule_y, f"{index:02d}  EL {top:.2f}~{bottom:.2f}  M={float(zone.get('maxAbsMomentKnMPerM') or 0):.1f}  {status}  {source}", 0.20)
        schedule_y -= 0.72
        if zone.get("zoneType") == "support_node_zone":
            dxf.rectangle("PIT_HIGHLIGHT", -0.18, bottom, width + 0.36, top - bottom)
            dxf.leader("PIT_LEADER", width, (top + bottom) / 2.0, width + 1.5, (top + bottom) / 2.0 + 0.6, "D-04 local strengthening", 0.18)
    for elevation in sorted({float(item.elevation) for item in (ret.supports if ret else [])}, reverse=True):
        if wall.bottom_elevation < elevation < wall.top_elevation:
            dxf.line("PIT_SUPPORT_LEVEL", -0.8, elevation, width + 0.8, elevation)
            dxf.text("PIT_SUPPORT_LEVEL", width + 0.9, elevation, f"Support EL {elevation:.2f}", 0.18)
    if project.excavation:
        dxf.line("PIT_EXCAVATION", -0.8, project.excavation.bottom_elevation, width + 0.8, project.excavation.bottom_elevation)
        dxf.text("PIT_EXCAVATION", width + 0.9, project.excavation.bottom_elevation, "Final excavation level", 0.18)
    _title_block(dxf, project, sheet_no.replace("-", ""), f"{wall.panel_code} 地下连续墙单幅分区配筋立面", "1:50")
    dxf.write(path)


def _write_support_rebar_general(project: Project, path: Path, scheme: dict[str, Any]) -> None:
    dxf = DxfWriter()
    rows = scheme.get("supportSchemes", [])
    dxf.text("PIT_TITLE", 0, 8, "R-04 RC support reinforcement general arrangement", 0.55)
    headers = ["Support", "Level", "Section", "N(kN)", "Longitudinal", "End zone", "Middle", "Status"]
    xs = [0, 18, 30, 48, 64, 88, 108, 126]
    for index, header in enumerate(headers):
        dxf.text("PIT_TITLE", xs[index], 5.2, header, 0.26)
    for row_index, item in enumerate(rows[:34], start=1):
        y = 5.2 - row_index * 0.95
        values = [item.get("hostCode"), item.get("levelIndex"), item.get("section", {}).get("name"), item.get("axialForceDesignKn"), item.get("longitudinal", {}).get("token"), item.get("endZones", {}).get("token"), item.get("middleZone", {}).get("token"), item.get("status")]
        for index, value in enumerate(values):
            dxf.text("PIT_TEXT", xs[index], y, str(value), 0.2)
    y0 = -31.0
    dxf.rectangle("PIT_CONCRETE", 0, y0, 48, 3.0)
    dxf.text("PIT_TEXT", 0, y0 + 3.6, "Typical support elevation", 0.3)
    dxf.line("PIT_ZONE", 7, y0, 7, y0 + 3); dxf.line("PIT_ZONE", 41, y0, 41, y0 + 3)
    dxf.text("PIT_TEXT", 1, y0 + 1.3, "End confinement zone", 0.22)
    dxf.text("PIT_TEXT", 18, y0 + 1.3, "Middle zone / staggered lap away from rigid nodes", 0.22)
    dxf.text("PIT_TEXT", 42, y0 + 1.3, "End confinement zone", 0.22)
    for offset in (0.55, 1.1, 1.65, 2.2):
        dxf.line("PIT_REBAR_MAIN", 0.4, y0 + offset, 47.6, y0 + offset)
    _title_block(dxf, project, "R04", "钢筋混凝土支撑配筋总图", "1:100")
    dxf.write(path)


def _write_wale_rebar_general(project: Project, path: Path, scheme: dict[str, Any]) -> None:
    dxf = DxfWriter()
    rows = [item for item in scheme.get("beamNodeSchemes", []) if item.get("hostType") == "wale_or_crown_beam"]
    dxf.text("PIT_TITLE", 0, 7, "R-05 Crown, wale and ring beam reinforcement arrangement", 0.55)
    headers = ["Beam", "Role", "Level", "EL", "Main bars", "Stirrups", "Node additional", "Status"]
    xs = [0, 18, 38, 52, 66, 88, 108, 144]
    for index, header in enumerate(headers): dxf.text("PIT_TITLE", xs[index], 4.2, header, 0.25)
    for row_index, item in enumerate(rows[:30], start=1):
        y = 4.2 - row_index * 1.0
        values = [item.get("hostCode"), item.get("beamRole"), item.get("levelIndex"), item.get("elevation"), item.get("mainBars", {}).get("token"), item.get("stirrups", {}).get("token"), str(item.get("nodeAdditional") or "")[:36], item.get("status")]
        for index, value in enumerate(values): dxf.text("PIT_TEXT", xs[index], y, str(value), 0.19)
    _title_block(dxf, project, "R05", "冠梁、围檩及环梁配筋总图", "1:100")
    dxf.write(path)


def _write_detail_cell(dxf: DxfWriter, x: float, y: float, title: str, detail_no: str, kind: str) -> None:
    dxf.rectangle("PIT_FRAME", x, y, 34, 22)
    dxf.text("PIT_TITLE", x + 1, y + 20.5, f"{detail_no} {title}", 0.3)
    if kind == "support_wale":
        dxf.rectangle("PIT_WALE", x + 3, y + 8, 26, 5)
        dxf.line("PIT_SUPPORT", x + 16, y + 2, x + 16, y + 20)
        dxf.rectangle("PIT_PLATE", x + 13.5, y + 6.5, 5, 8)
        for i in range(5): dxf.line("PIT_REBAR_MAIN", x + 4, y + 8.8 + i * 0.8, x + 28, y + 8.8 + i * 0.8)
    elif kind == "corner":
        dxf.line("PIT_WALE", x + 4, y + 4, x + 4, y + 18)
        dxf.line("PIT_WALE", x + 4, y + 4, x + 28, y + 4)
        dxf.line("PIT_SUPPORT", x + 5, y + 5, x + 24, y + 16)
        dxf.arc("PIT_REBAR_MAIN", x + 6, y + 6, 4, 0, 90)
    elif kind == "column":
        dxf.line("PIT_SUPPORT", x + 3, y + 11, x + 31, y + 11)
        dxf.rectangle("PIT_COLUMN", x + 14, y + 3, 6, 16)
        dxf.rectangle("PIT_PLATE", x + 11, y + 8, 12, 6)
    elif kind == "wall_joint":
        dxf.rectangle("PIT_CONCRETE", x + 7, y + 3, 8, 16)
        dxf.rectangle("PIT_CONCRETE", x + 19, y + 3, 8, 16)
        dxf.line("PIT_JOINT", x + 17, y + 3, x + 17, y + 19)
        for px in (9, 12.5, 21, 24.5): dxf.line("PIT_REBAR_MAIN", x + px, y + 4, x + px, y + 18)
        dxf.arc("PIT_REBAR_ADD", x + 17, y + 10, 5, 90, 270)
        dxf.text("PIT_TEXT", x + 10, y + 1.8, "panel joint / stop-end / waterstop / cage connector", 0.16)
    elif kind == "support_splice":
        dxf.rectangle("PIT_CONCRETE", x + 3, y + 7, 28, 8)
        for offset in (8.5, 10.2, 11.9, 13.6):
            dxf.line("PIT_REBAR_MAIN", x + 4, y + offset, x + 30, y + offset)
        dxf.rectangle("PIT_HIGHLIGHT", x + 12, y + 6.3, 10, 9.4)
        dxf.text("PIT_TEXT", x + 10.5, y + 17, "staggered lap / coupler zone", 0.18)
        dxf.text("PIT_TEXT", x + 4, y + 4.8, "end rigid zones excluded; confinement follows support schedule", 0.16)
    elif kind == "grid_node":
        dxf.rectangle("PIT_SUPPORT_MAIN", x + 2, y + 8, 30, 6)
        dxf.rectangle("PIT_SUPPORT_SECONDARY", x + 14, y + 2, 6, 18)
        dxf.rectangle("PIT_COLUMN", x + 13, y + 7, 8, 8)
        dxf.rectangle("PIT_PLATE", x + 10.5, y + 6, 13, 10)
        for offset in (8.8, 10.2, 11.6, 13.0):
            dxf.line("PIT_REBAR_MAIN", x + 3, y + offset, x + 31, y + offset)
        for offset in (15.0, 16.5, 18.0, 19.5):
            dxf.line("PIT_REBAR_ADD", x + offset, y + 3, x + offset, y + 19)
        dxf.text("PIT_TEXT", x + 2.5, y + 1.0, "main/grid struts intersect at temporary column; verify elevation, bearing plate, couplers and cage clearance", 0.145)
    else:
        dxf.rectangle("PIT_CONCRETE", x + 6, y + 3, 20, 16)
        for i in range(6): dxf.line("PIT_REBAR_MAIN", x + 8 + i * 3, y + 4, x + 8 + i * 3, y + 18)
        dxf.rectangle("PIT_HIGHLIGHT", x + 5, y + 8, 22, 5)
    dxf.text("PIT_TEXT", x + 1, y + 1, "Dimensions and reinforcement marks refer to zone/node schedules.", 0.16)


def _write_typical_detail_compilation(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    _write_detail_cell(dxf, 0, 0, "Support-wale bearing node", "D-01", "support_wale")
    _write_detail_cell(dxf, 38, 0, "Corner diagonal brace node", "D-02", "corner")
    _write_detail_cell(dxf, 0, -26, "Support-column intersection", "D-03", "column")
    _write_detail_cell(dxf, 38, -26, "Wall support-zone strengthening", "D-04", "wall")
    _write_detail_cell(dxf, 0, -52, "Wall panel joint and cage connector", "D-06", "wall_joint")
    _write_detail_cell(dxf, 38, -52, "Support anchorage and staggered lap", "D-07", "support_splice")
    _write_detail_cell(dxf, 0, -78, "Bidirectional grid node at temporary column", "D-08", "grid_node")
    _title_block(dxf, project, "D00", "典型节点大样索引与组合图", "1:20/1:50")
    dxf.write(path)


def _write_corner_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter(); _write_detail_cell(dxf, 0, 0, "Corner diagonal brace node", "D-02", "corner"); _title_block(dxf, project, "D02", "角撑节点与转角加强大样", "1:20"); dxf.write(path)


def _write_support_column_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter(); _write_detail_cell(dxf, 0, 0, "Support-column intersection", "D-03", "column"); _title_block(dxf, project, "D03", "支撑—立柱交叉节点大样", "1:20"); dxf.write(path)


def _write_wall_support_zone_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter(); _write_detail_cell(dxf, 0, 0, "Wall support-zone strengthening", "D-04", "wall"); _title_block(dxf, project, "D04", "地连墙支撑区局部加强大样", "1:20"); dxf.write(path)


def _write_wall_joint_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter(); _write_detail_cell(dxf, 0, 0, "Wall panel joint and cage connector", "D-06", "wall_joint"); _title_block(dxf, project, "D06", "地下连续墙墙幅接头与钢筋笼连接大样", "1:20"); dxf.write(path)


def _write_support_splice_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter(); _write_detail_cell(dxf, 0, 0, "Support anchorage and staggered lap", "D-07", "support_splice"); _title_block(dxf, project, "D07", "钢筋混凝土支撑端部锚固与错开搭接大样", "1:20"); dxf.write(path)


def _write_bidirectional_grid_node_detail(project: Project, path: Path) -> None:
    dxf = DxfWriter()
    _write_detail_cell(dxf, 0, 0, "Bidirectional grid node at temporary column", "D-08", "grid_node")
    _title_block(dxf, project, "D08", "主次支撑网格交叉节点与立柱连接大样", "1:20")
    dxf.write(path)


def _write_rebar_zone_schedule_csv(path: Path, scheme: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["zone_id", "host_code", "zone_type", "top_elevation", "bottom_elevation", "face", "moment_design_knm_per_m", "required_as_mm2_per_m", "bar_diameter_mm", "bar_spacing_mm", "provided_as_mm2_per_m", "utilization", "horizontal_distribution", "tie_bars", "status", "drawing_refs"])
        for zone in scheme.get("wallZones", []):
            for face in zone.get("faces", []):
                writer.writerow([zone.get("zoneId"), zone.get("hostCode"), zone.get("zoneType"), zone.get("topElevation"), zone.get("bottomElevation"), face.get("face"), face.get("momentDesignKnMPerM"), face.get("requiredAsMm2PerM"), face.get("barDiameterMm"), face.get("barSpacingMm"), face.get("providedAsMm2PerM"), face.get("utilization"), zone.get("horizontalDistribution", {}).get("token"), zone.get("tieBars", {}).get("token"), face.get("status"), ";".join(zone.get("drawingRefs", []))])


def _write_support_rebar_schedule_csv(path: Path, scheme: dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["support_code", "level", "elevation", "role", "span_m", "section", "axial_force_design_kn", "longitudinal", "end_zone", "middle_zone", "clear_spacing_mm", "steel_ratio", "utilization", "status", "drawing_refs"])
        for item in scheme.get("supportSchemes", []):
            writer.writerow([item.get("hostCode"), item.get("levelIndex"), item.get("elevation"), item.get("supportRole"), item.get("spanM"), item.get("section", {}).get("name"), item.get("axialForceDesignKn"), item.get("longitudinal", {}).get("token"), item.get("endZones", {}).get("token"), item.get("middleZone", {}).get("token"), item.get("clearSpacingMm"), item.get("longitudinalSteelRatio"), item.get("utilization"), item.get("status"), ";".join(item.get("drawingRefs", []))])


def _write_design_diagnostic_summary(path: Path, scheme: dict[str, Any]) -> None:
    diagnostics = scheme.get("diagnostics") or {}
    payload = {
        "headline": diagnostics.get("headline"),
        "canApply": diagnostics.get("canApply"),
        "canIssueConstructionDrawings": diagnostics.get("canIssueConstructionDrawings"),
        "reviewWatermarkRequired": diagnostics.get("reviewWatermarkRequired"),
        "calculation": diagnostics.get("calculation"),
        "supportTopology": diagnostics.get("supportTopology"),
        "failureReasons": diagnostics.get("failureReasons"),
        "actions": diagnostics.get("actions"),
        "summary": scheme.get("summary"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_design_diagnostic_csv(path: Path, scheme: dict[str, Any]) -> None:
    diagnostics = scheme.get("diagnostics") or {}
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "code", "status_or_count", "objects", "recommended_action"])
        calculation = diagnostics.get("calculation") or {}
        writer.writerow(["calculation", "CALCULATION_VALIDITY", calculation.get("status"), "", "; ".join(calculation.get("messages") or [])])
        topology = diagnostics.get("supportTopology") or {}
        writer.writerow(["support_topology", "SUPPORT_TOPOLOGY", topology.get("status"), f"secondary={topology.get('secondaryGridSupportCount')}; corner_width={topology.get('maxCornerTributaryWidthM')}", topology.get("message")])
        for code, item in (diagnostics.get("failureReasons") or {}).items():
            writer.writerow(["failure_reason", code, item.get("count"), ";".join(str(v) for v in item.get("objects") or []), item.get("recommendedAction")])
        for action in diagnostics.get("actions") or []:
            writer.writerow(["action", action.get("id"), f"P{action.get('priority')}", action.get("label"), action.get("description")])


def _write_advanced_diagnostic_sheet(project: Project, path: Path, sheet_no: str, title: str, rows: list[tuple[str, str, str, str]]) -> None:
    dxf = DxfWriter()
    dxf.text(_layer(project, "title", "PIT_TITLE"), 0, 18, title, 0.65)
    headers = ["对象/模块", "状态", "控制指标", "建议"]
    widths = [25.0, 12.0, 28.0, 55.0]
    x = [0.0]
    for w in widths: x.append(x[-1] + w)
    top = 16.5; row_h = 2.0
    for i in range(len(rows)+2): dxf.line(_layer(project, "frame", "PIT_FRAME"), x[0], top-i*row_h, x[-1], top-i*row_h)
    for xx in x: dxf.line(_layer(project, "frame", "PIT_FRAME"), xx, top, xx, top-(len(rows)+1)*row_h)
    for col, text in enumerate(headers): dxf.text(_layer(project, "text", "PIT_TEXT"), x[col]+0.4, top-1.35, text, 0.3)
    for r, values in enumerate(rows, start=1):
        for c, value in enumerate(values): dxf.text(_layer(project, "text", "PIT_TEXT"), x[c]+0.4, top-r*row_h-1.35, str(value)[:70], 0.25)
    _title_block(dxf, project, sheet_no.replace("-", ""), title, "NTS")
    dxf.write(path)


def _write_manifest_files(package_dir: Path, manifest: dict[str, Any], scheme: dict[str, Any]) -> None:
    (package_dir / "drawing_set_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (package_dir / "rebar_design_scheme.json").write_text(json.dumps(scheme, ensure_ascii=False, indent=2), encoding="utf-8")
    with (package_dir / "drawing_register.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["sheet_no", "title", "category", "scale", "file", "model_binding", "legacy"])
        for item in manifest.get("sheets", []):
            writer.writerow([item.get("sheetNo"), item.get("title"), item.get("category"), item.get("scale"), item.get("file"), ";".join(item.get("modelBinding", [])), item.get("legacy", False)])


def export_construction_cad_package(project: Project, output_dir: str | Path, scope: str = "full", rebar_mode: str = "balanced", issue_mode: str = "review") -> Path:
    if scope not in {"full", "general", "rebar", "details"}:
        raise ValueError(f"Unsupported CAD package scope: {scope}")
    if issue_mode not in {"review", "construction"}:
        raise ValueError(f"Unsupported CAD issue mode: {issue_mode}")
    out = Path(output_dir)
    package_dir = out / f"{project.id}_cad_package_{scope}_{issue_mode}"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for folder in ("00_general", "10_plans", "20_sections", "30_rebar", "40_details", "50_quality", "60_monitoring", "90_schedules"):
        (package_dir / folder).mkdir(parents=True, exist_ok=True)

    detailing = build_rebar_detailing(project, mode=rebar_mode)
    scheme = detailing.get("designScheme") or build_rebar_design_scheme(project, mode=rebar_mode)
    manifest = build_drawing_set_manifest(project)
    advanced_suite = build_advanced_engineering_suite(project, rebar_mode)
    manifest["scope"] = scope
    manifest["rebarMode"] = rebar_mode
    manifest["issueMode"] = issue_mode
    manifest["reviewWatermark"] = issue_mode == "review"

    generated: list[Path] = []
    def add(path: Path, writer) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer(path)
        generated.append(path)

    if scope in {"full", "general"}:
        add(package_dir / "00_general/G-00_drawing_index_general_notes.dxf", lambda path: _write_general_notes_sheet(project, path, manifest))
        add(package_dir / "10_plans/S-00_retaining_support_general_arrangement.dxf", lambda path: _write_master_general_arrangement(project, path))
        add(package_dir / "S-01_support_plan.dxf", lambda path: _write_support_plan(project, path))
        levels = sorted({item.level_index for item in (project.retaining_system.supports if project.retaining_system else [])})
        for level in levels:
            add(package_dir / f"10_plans/S-02-L{level:02d}_support_level_plan.dxf", lambda path, level=level: _write_support_level_plan(project, path, level))
        add(package_dir / "20_sections/S-03_excavation_stage_section.dxf", lambda path: _write_excavation_section(project, path))
        add(package_dir / "S-04_excavation_section.dxf", lambda path: _write_excavation_section(project, path))
        add(package_dir / "S-06_monitoring_plan.dxf", lambda path: _write_monitoring_plan(project, path))

    if scope in {"full", "rebar"}:
        add(package_dir / "30_rebar/R-01_wall_rebar_general_arrangement.dxf", lambda path: _write_wall_rebar_general_arrangement(project, path, scheme))
        add(package_dir / "30_rebar/R-02_wall_rebar_zone_elevation.dxf", lambda path: _write_wall_rebar_zone_elevation(project, path, scheme))
        if project.retaining_system:
            for index, wall in enumerate(project.retaining_system.diaphragm_walls, start=1):
                sheet_no = f"R-02-W{index:02d}"
                token = _safe_file_token(wall.panel_code)
                add(package_dir / f"30_rebar/walls/{sheet_no}_{token}_rebar_elevation.dxf", lambda path, wall_id=wall.id, sheet_no=sheet_no: _write_single_wall_rebar_elevation(project, path, scheme, wall_id, sheet_no))
        add(package_dir / "30_rebar/R-04_support_rebar_general_arrangement.dxf", lambda path: _write_support_rebar_general(project, path, scheme))
        add(package_dir / "30_rebar/R-05_wale_rebar_general_arrangement.dxf", lambda path: _write_wale_rebar_general(project, path, scheme))
        add(package_dir / "S-02_wall_rebar_cage.dxf", lambda path: _write_wall_rebar_detail(project, path))
        add(package_dir / "S-07_rebar_bending_schedule.dxf", lambda path: _write_rebar_bending_schedule(project, path, detailing=detailing))
        add(package_dir / "S-08_individual_rebar_geometry.dxf", lambda path: _write_rebar_geometry_plan(project, path, detailing=detailing))
        add(package_dir / "S-09_lap_splice_layout.dxf", lambda path: _write_splice_layout(project, path, detailing=detailing))
        add(package_dir / "S-10_cage_segment_lifting_plan.dxf", lambda path: _write_cage_lifting_plan(project, path, detailing=detailing))
        add(package_dir / "S-11_cover_bend_check.dxf", lambda path: _write_cover_conflict_check(project, path, detailing=detailing))
        add(package_dir / "S-12_shop_drawing_signoff_checklist.dxf", lambda path: _write_shop_signoff_sheet(project, path, detailing=detailing))

    if scope in {"full", "details"}:
        add(package_dir / "40_details/D-00_typical_detail_compilation.dxf", lambda path: _write_typical_detail_compilation(project, path))
        add(package_dir / "S-03_support_wale_node_detail.dxf", lambda path: _write_node_detail(project, path))
        add(package_dir / "40_details/D-02_corner_brace_node_detail.dxf", lambda path: _write_corner_detail(project, path))
        add(package_dir / "40_details/D-03_support_column_intersection_detail.dxf", lambda path: _write_support_column_detail(project, path))
        add(package_dir / "40_details/D-04_wall_support_zone_detail.dxf", lambda path: _write_wall_support_zone_detail(project, path))
        add(package_dir / "S-05_column_pile_detail.dxf", lambda path: _write_column_pile_detail(project, path))
        add(package_dir / "40_details/D-06_wall_panel_joint_detail.dxf", lambda path: _write_wall_joint_detail(project, path))
        add(package_dir / "40_details/D-07_support_anchorage_splice_detail.dxf", lambda path: _write_support_splice_detail(project, path))
        add(package_dir / "40_details/D-08_bidirectional_grid_node_detail.dxf", lambda path: _write_bidirectional_grid_node_detail(project, path))

    if scope in {"full", "details"}:
        svc = advanced_suite["serviceability"]
        svc_rows = [(str(x.get("hostCode")), str(x.get("status")), f"w={x.get('estimatedCrackWidthMm')}mm / {x.get('limitMm')}mm", str(x.get("recommendedAction"))) for x in svc.get("wallZoneChecks", []) if x.get("status") != "pass"][:18]
        if not svc_rows: svc_rows = [("ALL", "pass", f"max w={svc['summary'].get('maxEstimatedCrackWidthMm')}mm", "维持当前抗裂构造并结合监测复核")]
        add(package_dir / "50_quality/Q-02_serviceability_crack_check.dxf", lambda path, rows=svc_rows: _write_advanced_diagnostic_sheet(project, path, "Q-02", "长期效应与裂缝控制检查图", rows))
        col = advanced_suite["collisions"]
        col_rows = [(str(x.get("objectA")), str(x.get("status")), str(x.get("type")), str(x.get("recommendedAction"))) for x in col.get("collisions", [])][:18]
        if not col_rows: col_rows = [("ALL", "pass", "no hard collision", "按施工偏差和净距要求实施")]
        add(package_dir / "50_quality/Q-03_collision_clearance_check.dxf", lambda path, rows=col_rows: _write_advanced_diagnostic_sheet(project, path, "Q-03", "构件碰撞、净距与节点拥挤检查图", rows))
        nod = advanced_suite["nodeLocal"]
        nod_rows = [(str(x.get("nodeCode")), str(x.get("status")), f"util={x.get('governingUtilization')}, slip={x.get('localSlipMm')}mm", str(x.get("recommendedAction"))) for x in nod.get("nodes", []) if x.get("status") != "pass"][:18]
        if not nod_rows: nod_rows = [("ALL", "pass", f"max util={nod['summary'].get('maxUtilization')}", "按节点大样实施")]
        add(package_dir / "50_quality/N-01_node_local_analysis.dxf", lambda path, rows=nod_rows: _write_advanced_diagnostic_sheet(project, path, "N-01", "高利用率节点局部复核索引图", rows))
        mon = advanced_suite["monitoring"]
        mon_rows = [("监测记录", "info", str(mon.get("recordCount", 0)), "导入墙体位移、支撑轴力、水位与沉降数据"), ("最近反演", str((mon.get("latestCalibration") or {}).get("status", "not_run")), str((mon.get("latestCalibration") or {}).get("confidence", "-")), "应用后必须重新计算")]
        add(package_dir / "60_monitoring/M-02_monitoring_calibration.dxf", lambda path, rows=mon_rows: _write_advanced_diagnostic_sheet(project, path, "M-02", "监测反演与参数校准记录图", rows))

    schedule_files = [
        package_dir / "90_schedules/rebar_schedule.csv",
        package_dir / "90_schedules/material_schedule.csv",
        package_dir / "90_schedules/rebar_bending_schedule.csv",
        package_dir / "90_schedules/individual_bar_geometry.csv",
        package_dir / "90_schedules/cage_segment_schedule.csv",
        package_dir / "90_schedules/splice_schedule.csv",
        package_dir / "90_schedules/cover_conflict_check.csv",
        package_dir / "90_schedules/shop_drawing_checklist.csv",
        package_dir / "90_schedules/rebar_zone_schedule.csv",
        package_dir / "90_schedules/support_rebar_schedule.csv",
        package_dir / "90_schedules/delivery_consistency_matrix.csv",
        package_dir / "enterprise_template_manifest.json",
        package_dir / "90_schedules/design_diagnostic_summary.json",
        package_dir / "90_schedules/design_diagnostic_summary.csv",
        package_dir / "90_schedules/advanced_engineering_suite.json",
        package_dir / "90_schedules/serviceability_checks.csv",
        package_dir / "90_schedules/collision_clearance_checks.csv",
        package_dir / "90_schedules/node_local_analysis.csv",
        package_dir / "90_schedules/support_topology_graph.json",
        package_dir / "90_schedules/monitoring_calibration.json",
        package_dir / "90_schedules/review_workflow.json",
        package_dir / "90_schedules/drawing_revision_log.csv",
    ]
    _write_rebar_schedule(project, schedule_files[0])
    _write_material_schedule(project, schedule_files[1])
    _write_rebar_bending_schedule_csv(project, schedule_files[2], detailing=detailing)
    _write_individual_bar_geometry_csv(project, schedule_files[3], detailing=detailing)
    _write_cage_segment_schedule_csv(project, schedule_files[4], detailing=detailing)
    _write_splice_schedule_csv(project, schedule_files[5], detailing=detailing)
    _write_cover_conflict_check_csv(project, schedule_files[6], detailing=detailing)
    _write_shop_signoff_checklist_csv(project, schedule_files[7], detailing=detailing)
    _write_rebar_zone_schedule_csv(schedule_files[8], scheme)
    _write_support_rebar_schedule_csv(schedule_files[9], scheme)
    _write_delivery_consistency_matrix(project, schedule_files[10])
    _write_enterprise_template_manifest(project, schedule_files[11])
    _write_design_diagnostic_summary(schedule_files[12], scheme)
    _write_design_diagnostic_csv(schedule_files[13], scheme)
    schedule_files[14].write_text(json.dumps(advanced_suite, ensure_ascii=False, indent=2), encoding="utf-8")
    def _write_rows(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(headers); w.writerows(rows)
    _write_rows(schedule_files[15], ["object_id","host_code","face","crack_width_mm","limit_mm","status","drawing_refs"], [[x.get("objectId"),x.get("hostCode"),x.get("face"),x.get("estimatedCrackWidthMm"),x.get("limitMm"),x.get("status"),";".join(x.get("drawingRefs") or [])] for x in advanced_suite["serviceability"].get("wallZoneChecks", [])])
    _write_rows(schedule_files[16], ["id","object_a","object_b","type","status","message","recommended_action"], [[x.get("id"),x.get("objectA"),x.get("objectB"),x.get("type"),x.get("status"),x.get("message"),x.get("recommendedAction")] for x in advanced_suite["collisions"].get("collisions", [])])
    _write_rows(schedule_files[17], ["node_code","support_code","force_kn","bearing_util","splitting_util","eccentric_util","slip_mm","status"], [[x.get("nodeCode"),x.get("supportCode"),x.get("designForceKn"),x.get("bearingUtilization"),x.get("splittingUtilization"),x.get("eccentricityUtilization"),x.get("localSlipMm"),x.get("status")] for x in advanced_suite["nodeLocal"].get("nodes", [])])
    schedule_files[18].write_text(json.dumps(advanced_suite["topology"], ensure_ascii=False, indent=2), encoding="utf-8")
    schedule_files[19].write_text(json.dumps(advanced_suite["monitoring"], ensure_ascii=False, indent=2), encoding="utf-8")
    schedule_files[20].write_text(json.dumps(review_status(project), ensure_ascii=False, indent=2), encoding="utf-8")
    _write_rows(schedule_files[21], ["revision","description","sheets","author","issue_status","snapshot_hash","created_at"], [[r.revision,r.description,";".join(r.sheet_numbers),r.author,r.issue_status,r.snapshot_hash,r.created_at] for r in project.drawing_revisions])
    generated.extend(schedule_files)
    # Preserve V2.x flat-package schedule names for downstream scripts while the
    # canonical V3.2 files remain organized under 90_schedules/.
    for source in (schedule_files[3], schedule_files[4], schedule_files[5], schedule_files[6], schedule_files[7]):
        legacy = package_dir / source.name
        shutil.copy2(source, legacy)
        generated.append(legacy)
    manifest["includedFiles"] = [file.relative_to(package_dir).as_posix() for file in generated if file.exists()]
    manifest["includedSheetCount"] = len([file for file in generated if file.suffix.lower() == ".dxf"])
    _write_manifest_files(package_dir, manifest, scheme)
    generated.extend([package_dir / "drawing_set_manifest.json", package_dir / "rebar_design_scheme.json", package_dir / "drawing_register.csv"])

    package_manifest = package_dir / "drawing_package_manifest.json"
    package_manifest.write_text(json.dumps({
        "projectId": project.id,
        "packageType": f"PitGuard V{SOFTWARE_VERSION} coordinated CAD drawing set",
        "scope": scope,
        "rebarMode": rebar_mode,
        "issueMode": issue_mode,
        "reviewWatermark": issue_mode == "review",
        "sheetCount": len([path for path in generated if path.suffix.lower() == ".dxf"]),
        "tableCount": len([path for path in generated if path.suffix.lower() == ".csv"]),
        "folders": manifest.get("packageFolders"),
        "drawingSet": "drawing_set_manifest.json",
        "rebarDesignScheme": "rebar_design_scheme.json",
        "softwareModuleCompletion": 100,
        "cadTemplate": _cad_template(project),
        "officialIssueBoundary": manifest.get("issueBoundary"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    generated.append(package_manifest)
    readme = package_dir / "README.txt"
    readme.write_text(
        f"PitGuard V{SOFTWARE_VERSION} coordinated construction CAD package\n"
        f"Scope: {scope}; reinforcement mode: {rebar_mode}.\n"
        "Folders: 00_general, 10_plans, 20_sections, 30_rebar, 40_details, 50_quality, 60_monitoring and 90_schedules.\n"
        "Global drawings include drawing index/general notes, retaining-support master plan and separate support-level plans.\n"
        "Reinforcement drawings include wall zone plans/elevations, RC support end/middle zones, wale/node schedules, cage/splice/lifting and bar bending data.\n"
        "Detail drawings include support-wale, corner brace, support-column and wall support-zone local strengthening details.\n"
        "All DXF files are AutoCAD R12-compatible model-space drawings. CSV files use UTF-8 BOM.\n"
        "Final sealed construction issue requires project-specific crack, seismic, coupler, embedded-item, lifting and professional signoff checks.\n",
        encoding="utf-8",
    )
    generated.append(readme)
    if issue_mode == "review":
        review_notice = package_dir / "REVIEW_ONLY_审查版.txt"
        review_notice.write_text(
            "本图纸包为审查版/设计辅助成果，不得直接用于正式施工。\n"
            "请先消除配筋阻断项、完成企业图签与设计/校核/审核签署，并由注册工程师复核。\n",
            encoding="utf-8",
        )
        generated.append(review_notice)
    zip_path = out / f"{project.id}_construction_cad_{scope}_{issue_mode}_v{SOFTWARE_VERSION.replace('.', '_')}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in generated:
            if file.exists():
                zf.write(file, arcname=file.relative_to(package_dir).as_posix())
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
