from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.schemas.domain import BeamElement, ColumnElement, DiaphragmWallPanel, Point2D, Project, SupportElement
from app.geometry.wall_path import normalize_construction_panels, polyline_length, resolve_wall_plan_path
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.version import SOFTWARE_VERSION


def _ifc_text(value: str) -> str:
    # STEP viewers are uneven with raw UTF-8.  Encode non-ASCII runs using
    # IFC's \X2\hhhh\X0\ escape so Chinese names and notes do not break
    # visualization/import in stricter BIM viewers.
    value = str(value).replace("'", "''")
    out: list[str] = []
    run: list[str] = []
    def flush_run() -> None:
        nonlocal run
        if run:
            out.append("\\X2\\" + "".join(f"{ord(ch):04X}" for ch in run) + "\\X0\\")
            run = []
    for ch in value:
        code = ord(ch)
        if 32 <= code <= 126:
            flush_run()
            out.append(ch)
        elif ch in "\n\r\t":
            flush_run()
            out.append(" ")
        else:
            run.append(ch)
    flush_run()
    return "".join(out)


def _step_string(value: str | None) -> str:
    if value is None:
        return "$"
    return "'" + _ifc_text(str(value)) + "'"


def _real(value: float | int | None) -> str:
    if value is None:
        return "$"
    if isinstance(value, int):
        return f"{value}."
    if abs(value) < 1e-12:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".") + ("." if abs(value - int(value)) < 1e-12 else "")


_IFC_GUID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _guid() -> str:
    """Return a 22-character IFC GlobalId-compatible token.

    The previous exporter used the first 22 hexadecimal UUID characters.  That is
    syntactically inside the allowed character set, but several BIM viewers are
    stricter and expect a compressed 128-bit token.  This base64-style encoding
    keeps the file lightweight while improving viewer compatibility.
    """
    value = uuid4().int
    chars: list[str] = []
    for _ in range(22):
        value, rem = divmod(value, 64)
        chars.append(_IFC_GUID_CHARS[rem])
    return "".join(reversed(chars))


class IfcWriter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.next_id = 1

    def entity(self, value: str) -> int:
        eid = self.next_id
        self.next_id += 1
        self.lines.append(f"#{eid}={value};")
        return eid

    def point3(self, x: float, y: float, z: float) -> int:
        return self.entity(f"IFCCARTESIANPOINT(({_real(x)},{_real(y)},{_real(z)}))")

    def point2(self, x: float, y: float) -> int:
        return self.entity(f"IFCCARTESIANPOINT(({_real(x)},{_real(y)}))")

    def direction(self, x: float, y: float, z: float) -> int:
        length = math.sqrt(x * x + y * y + z * z)
        if length <= 1e-12:
            x, y, z = 1.0, 0.0, 0.0
        else:
            x, y, z = x / length, y / length, z / length
        return self.entity(f"IFCDIRECTION(({_real(x)},{_real(y)},{_real(z)}))")

    @staticmethod
    def _safe_refdir(refdir: tuple[float, float, float], axis: tuple[float, float, float]) -> tuple[float, float, float]:
        rx, ry, rz = refdir
        ax, ay, az = axis
        r_len = math.sqrt(rx * rx + ry * ry + rz * rz)
        a_len = math.sqrt(ax * ax + ay * ay + az * az)
        if r_len <= 1e-12 or a_len <= 1e-12:
            return (1.0, 0.0, 0.0)
        dot = abs((rx * ax + ry * ay + rz * az) / (r_len * a_len))
        if dot > 0.98:
            # IFC axis and reference direction must not be parallel.
            return (1.0, 0.0, 0.0) if abs(az) > 0.5 else (0.0, 0.0, 1.0)
        return refdir

    def axis2_3d(self, x: float, y: float, z: float, refdir: tuple[float, float, float] = (1, 0, 0), axis: tuple[float, float, float] = (0, 0, 1)) -> int:
        p = self.point3(x, y, z)
        a = self.direction(*axis)
        r = self.direction(*self._safe_refdir(refdir, axis))
        return self.entity(f"IFCAXIS2PLACEMENT3D(#{p},#{a},#{r})")

    def axis2_2d(self, x: float = 0.0, y: float = 0.0) -> int:
        p = self.point2(x, y)
        return self.entity(f"IFCAXIS2PLACEMENT2D(#{p},$)")

    def local_placement(
        self,
        relative_to: int | None,
        x: float,
        y: float,
        z: float,
        refdir: tuple[float, float, float] = (1, 0, 0),
        axis: tuple[float, float, float] = (0, 0, 1),
    ) -> int:
        placement_axis = self.axis2_3d(x, y, z, refdir=refdir, axis=axis)
        rel = f"#{relative_to}" if relative_to else "$"
        return self.entity(f"IFCLOCALPLACEMENT({rel},#{placement_axis})")

    def rect_swept_shape(self, context_id: int, length: float, width: float, height: float,
                         style_id: int | None = None) -> int:
        length = max(float(length), 0.001)
        width = max(float(width), 0.001)
        height = max(float(height), 0.001)
        pos2d = self.axis2_2d(0, 0)
        profile = self.entity(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{pos2d},{_real(length)},{_real(width)})")
        swept_pos = self.axis2_3d(0, 0, 0)
        extrusion_dir = self.direction(0, 0, 1)
        solid = self.entity(f"IFCEXTRUDEDAREASOLID(#{profile},#{swept_pos},#{extrusion_dir},{_real(height)})")
        item = solid
        if style_id:
            item = self.entity(f"IFCSTYLEDITEM(#{solid},(#{style_id}),$)")
        rep = self.entity(f"IFCSHAPEREPRESENTATION(#{context_id},'Body','SweptSolid',(#{item}))")
        return self.entity(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    def circle_swept_shape(
        self,
        context_id: int,
        radius: float,
        length: float,
        style_id: int | None = None,
    ) -> int:
        """Round bar: circular profile extruded along local +Z.

        Reinforcement shown as rectangular prisms reads as square sticks in every
        viewer, which is the main reason exported cages looked wrong.  A circular
        profile extruded by the bar length keeps the same placement convention as
        the previous box geometry (extrusion along local Z) but renders as a bar.
        """
        radius = max(float(radius), 0.003)
        length = max(float(length), 0.001)
        pos2d = self.axis2_2d(0, 0)
        profile = self.entity(f"IFCCIRCLEPROFILEDEF(.AREA.,$,#{pos2d},{_real(radius)})")
        swept_pos = self.axis2_3d(0, 0, 0)
        extrusion_dir = self.direction(0, 0, 1)
        solid = self.entity(f"IFCEXTRUDEDAREASOLID(#{profile},#{swept_pos},#{extrusion_dir},{_real(length)})")
        item = solid
        if style_id:
            item = self.entity(f"IFCSTYLEDITEM(#{solid},(#{style_id}),$)")
        rep = self.entity(f"IFCSHAPEREPRESENTATION(#{context_id},'Body','SweptSolid',(#{item}))")
        return self.entity(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    def surface_style(
        self,
        name: str,
        red: float,
        green: float,
        blue: float,
        transparency: float = 0.0,
    ) -> int:
        """Simple coloured surface style so rebar is distinguishable in viewers."""
        colour = self.entity(f"IFCCOLOURRGB($,{_real(red)},{_real(green)},{_real(blue)})")
        rendering = self.entity(
            f"IFCSURFACESTYLERENDERING(#{colour},{_real(transparency)},$,$,$,$,$,$,.NOTDEFINED.)"
        )
        return self.entity(f"IFCSURFACESTYLE({_step_string(name)},.BOTH.,(#{rendering}))")

    def single_value(self, name: str, value) -> int:
        if value is None:
            nominal = "$"
        elif isinstance(value, bool):
            nominal = ".T." if value else ".F."
        elif isinstance(value, (int, float)):
            nominal = f"IFCREAL({_real(float(value))})"
        else:
            nominal = f"IFCLABEL({_step_string(str(value))})"
        return self.entity(f"IFCPROPERTYSINGLEVALUE({_step_string(name)},$, {nominal}, $)")

    def property_set(self, owner_id: int, element_id: int, name: str, props: dict) -> int:
        prop_ids = [self.single_value(k, v) for k, v in props.items()]
        pset = self.entity(f"IFCPROPERTYSET({_step_string(_guid())},#{owner_id},{_step_string(name)},$,(" + ",".join(f"#{pid}" for pid in prop_ids) + "))")
        self.entity(f"IFCRELDEFINESBYPROPERTIES({_step_string(_guid())},#{owner_id},$,$,(#{element_id}),#{pset})")
        return pset


def _line_direction(start: Point2D, end: Point2D) -> tuple[float, float, float, float]:
    dx = end.x - start.x
    dy = end.y - start.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return 1.0, 0.0, 0.0, 0.0
    return dx / length, dy / length, length, math.atan2(dy, dx)


def _midpoint(start: Point2D, end: Point2D) -> tuple[float, float]:
    return (start.x + end.x) / 2.0, (start.y + end.y) / 2.0


def _axis_from_polyline(beam: BeamElement) -> tuple[Point2D, Point2D] | None:
    if len(beam.axis.points) < 2:
        return None
    return beam.axis.points[0], beam.axis.points[-1]


def _rebar_summary(groups) -> str:
    parts: list[str] = []
    for group in groups:
        token = f"{group.name}:D{group.diameter}"
        if group.spacing:
            token += f"@{group.spacing}"
        if group.count:
            token += f"x{group.count}"
        parts.append(token)
    return "; ".join(parts)


def _bar_area_m2(diameter_mm: float | None) -> float:
    dia_m = max(float(diameter_mm or 0.0) / 1000.0, 0.001)
    return math.pi * dia_m * dia_m / 4.0


def _add_reinforcing_bar(
    w: IfcWriter,
    *,
    owner: int,
    context: int,
    storey_place: int,
    name: str,
    description: str,
    x: float,
    y: float,
    z: float,
    refdir: tuple[float, float, float],
    length: float,
    diameter_mm: float,
    grade: str,
    properties: dict,
    vertical: bool = False,
    as_proxy: bool = False,
) -> int:
    diameter_m = max(diameter_mm / 1000.0, 0.006)
    radius_m = diameter_m / 2.0
    style_id = getattr(w, "rebar_style_id", None)
    # Round bars: circular profile extruded along the bar axis.  Square prisms
    # made every cage read as a stack of sticks in the viewer.
    if vertical:
        placement = w.local_placement(storey_place, x, y, z, refdir=(1, 0, 0), axis=(0, 0, 1))
        shape = w.circle_swept_shape(context, radius_m, max(length, diameter_m), style_id)
    else:
        # Local Z must follow the bar, so the reference direction is a
        # perpendicular inside the horizontal plane.
        rx, ry, rz = (float(refdir[0]), float(refdir[1]), float(refdir[2]))
        px, py = -ry, rx
        norm = math.sqrt(px * px + py * py)
        if norm <= 1e-9:
            px, py, norm = 1.0, 0.0, 1.0
        perp = (px / norm, py / norm, 0.0)
        placement = w.local_placement(storey_place, x, y, z, refdir=perp, axis=(rx, ry, rz))
        shape = w.circle_swept_shape(context, radius_m, max(length, diameter_m), style_id)
    area = _bar_area_m2(diameter_mm)
    if as_proxy:
        # Several lightweight web/coordination viewers either skip
        # IfcReinforcingBar or spend excessive time tessellating it.  The
        # construction_visual profile keeps the same reinforcement property set
        # but exports the representative bar geometry as a generic proxy so the
        # model is visible in more viewers.  The design_detailed profile still
        # emits true IfcReinforcingBar entities for BIM semantic review.
        merged = dict(properties)
        merged.update({
            "IfcSemanticClass": "IfcReinforcingBar visual proxy",
            "VisualProxyReason": "viewer_safe_construction_visual_export",
            "NominalDiameter_m": round(diameter_m, 6),
            "BarLength_m": round(length, 3),
            "CrossSectionArea_m2": round(area, 8),
            "SteelGrade": grade,
        })
        element = w.entity(
            f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string(name)},{_step_string(description)},$,#{placement},#{shape},$,.USERDEFINED.)"
        )
        w.property_set(owner, element, "Pset_ReinforcementVisualProxy", merged)
        return element
    element = w.entity(
        f"IFCREINFORCINGBAR({_step_string(_guid())},#{owner},{_step_string(name)},{_step_string(description)},$,#{placement},#{shape},$,{_step_string(grade)},{_real(diameter_m)},{_real(area)},{_real(length)},.USERDEFINED.,.RIBBED.)"
    )
    w.property_set(owner, element, "Pset_ReinforcementGroup", properties)
    return element



def _emit_wall_cage_bars(
    w: IfcWriter,
    *,
    owner: int,
    context: int,
    storey_place: int,
    wall: DiaphragmWallPanel,
    panel: dict,
    cage: dict,
    max_per_panel: int = 320,
    as_proxy: bool = False,
) -> list[int]:
    """Emit one construction panel's rebar cage as individual round bars.

    The previous export wrote a single representative bar per reinforcement group,
    so a 6 m panel showed one or two rods and read as an empty cage.  The cage
    descriptor already carries face diameters/spacings, horizontal spacing and
    tie spacing; this turns those parameters into real bar geometry.
    """
    start = cage.get("start") or {}
    end = cage.get("end") or {}
    sx, sy = float(start.get("x", 0.0)), float(start.get("y", 0.0))
    ex, ey = float(end.get("x", 0.0)), float(end.get("y", 0.0))
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    bottom = float(cage.get("bottomElevation", wall.bottom_elevation))
    height = max(float(cage.get("heightM") or (wall.top_elevation - wall.bottom_elevation)), 0.3)
    thickness = max(float(cage.get("thicknessM") or wall.thickness), 0.2)
    cover = min(max(float(cage.get("coverM") or 0.05), 0.02), thickness / 2 - 0.02)
    usable = max(length - 2 * cover, 0.3)
    face_offset = max(thickness / 2 - cover, 0.02)
    panel_code = str(panel.get("panelCode") or cage.get("panelCode") or "PANEL")
    grade = wall.rebar_grade or "HRB400"
    bars: list[int] = []

    def emit(name: str, x: float, y: float, z: float, refdir: tuple[float, float, float],
             bar_length: float, diameter_mm: float, vertical: bool, bar_type: str) -> None:
        if len(bars) >= max_per_panel:
            return
        bars.append(_add_reinforcing_bar(
            w, owner=owner, context=context, storey_place=storey_place,
            name=name,
            description=f"{bar_type} bar of construction panel {panel_code} cage",
            x=x, y=y, z=z, refdir=refdir, length=max(bar_length, 0.05),
            diameter_mm=diameter_mm, grade=grade,
            properties={
                "HostElement": panel_code,
                "CalculationWallCode": wall.panel_code,
                "ConstructionPanelCode": panel_code,
                "BarType": bar_type,
                "Diameter_mm": diameter_mm,
                "RepresentationLevel": "full_cage_grid",
            },
            vertical=vertical, as_proxy=as_proxy,
        ))

    # Vertical cage bars, both faces, at the design spacing.
    for face in cage.get("faces") or []:
        diameter = float(face.get("diameterMm") or 20.0)
        spacing = max(float(face.get("spacingMm") or 200.0) / 1000.0, 0.05)
        count = int(face.get("estimatedVerticalBarCount") or 0)
        if count <= 0:
            count = int(usable / spacing) + 1
        count = max(2, min(count, 70))
        side = 1.0 if str(face.get("face")) == "outer" else -1.0
        for index in range(count):
            ratio = index / (count - 1) if count > 1 else 0.0
            along = cover + ratio * usable
            emit(
                f"RB-{panel_code}-V{index + 1}",
                sx + ux * along + nx * side * face_offset,
                sy + uy * along + ny * side * face_offset,
                bottom, (0, 0, 1), height, diameter, True,
                f"vertical_{face.get('face') or 'face'}",
            )

    # Horizontal cage bars, both faces.
    horizontal = cage.get("horizontal") or {}
    h_diameter = float(horizontal.get("diameterMm") or 16.0)
    h_count = int(horizontal.get("estimatedBarCountPerFace") or 0)
    if h_count <= 0:
        h_spacing = max(float(horizontal.get("spacingMm") or 200.0) / 1000.0, 0.05)
        h_count = int(height / h_spacing) + 1
    h_count = max(2, min(h_count, 80))
    for side in (1.0, -1.0):
        for index in range(h_count):
            z = bottom + (index + 0.5) * height / h_count
            emit(
                f"RB-{panel_code}-H{index + 1}{'O' if side > 0 else 'I'}",
                sx + ux * cover + nx * side * face_offset,
                sy + uy * cover + ny * side * face_offset,
                z, (ux, uy, 0.0), usable, h_diameter, False,
                "horizontal" if side > 0 else "horizontal_inner",
            )

    # Ties bridging the two faces.
    ties = cage.get("ties") or {}
    t_diameter = float(ties.get("diameterMm") or 12.0)
    tie_spacing = max(float(ties.get("spacingMm") or 400.0) / 1000.0, 0.1)
    tie_count = max(2, min(int(height / tie_spacing) + 1, 60))
    tie_length = max(thickness - 2 * cover, 0.05)
    for index in range(tie_count):
        z = bottom + (index + 0.5) * height / tie_count
        emit(
            f"RB-{panel_code}-T{index + 1}",
            sx + ux * (length / 2) - nx * (tie_length / 2),
            sy + uy * (length / 2) - ny * (tie_length / 2),
            z, (nx, ny, 0.0), tie_length, t_diameter, False, "tie",
        )
    return bars


def _emit_support_cage_bars(
    w: IfcWriter,
    *,
    owner: int,
    context: int,
    storey_place: int,
    support: SupportElement,
    max_per_support: int = 320,
    as_proxy: bool = False,
    stirrup_station_cap: int = 40,
) -> list[int]:
    """Emit a support strut cage: longitudinal bars, side bars and closed hoops.

    Previously only the first two reinforcement groups were written, one bar each,
    so a strut showed two rods and no stirrups at all.
    """
    from app.ifc.rebar_visualization import _support_longitudinal_offsets

    dx = support.end.x - support.start.x
    dy = support.end.y - support.start.y
    span = math.hypot(dx, dy)
    if span <= 1e-6:
        return []
    ux, uy = dx / span, dy / span
    nx, ny = -uy, ux
    width = max(float(support.section.width or 0.8), 0.3)
    height = max(float(support.section.height or 0.8), 0.3)
    z_center = float(support.elevation)
    cover = 0.05
    half_w = max(width / 2.0 - cover, 0.05)
    half_h = max(height / 2.0 - cover, 0.05)
    usable = max(span - 2 * cover, 0.2)
    code = support.code
    groups = list(support.reinforcement or [])
    bars: list[int] = []

    def emit(name: str, x: float, y: float, z: float, refdir: tuple[float, float, float],
             bar_length: float, diameter: float, vertical: bool, bar_type: str, grade: str) -> None:
        if len(bars) >= max_per_support:
            return
        bars.append(_add_reinforcing_bar(
            w, owner=owner, context=context, storey_place=storey_place,
            name=name,
            description=f"{bar_type} bar of support {code}",
            x=x, y=y, z=z, refdir=refdir, length=max(bar_length, 0.05),
            diameter_mm=diameter, grade=grade,
            properties={
                "HostElement": code,
                "BarType": bar_type,
                "Diameter_mm": diameter,
                "RepresentationLevel": "full_cage_grid",
            },
            vertical=vertical, as_proxy=as_proxy,
        ))

    longitudinal = next((g for g in groups if g.bar_type == "longitudinal"), None)
    if longitudinal:
        offsets = _support_longitudinal_offsets(int(longitudinal.count or 8), width, height)
        for index, (lateral, vertical) in enumerate(offsets, start=1):
            emit(
                f"RB-{code}-L{index}",
                support.start.x + ux * cover + nx * lateral,
                support.start.y + uy * cover + ny * lateral,
                z_center + vertical, (ux, uy, 0.0), usable,
                longitudinal.diameter, False, "longitudinal", longitudinal.grade,
            )

    for group in groups:
        if group.bar_type != "distribution":
            continue
        spacing = max(float(group.spacing or 200.0) / 1000.0, 0.05)
        rows = max(2, min(int(height / spacing) + 1, 10))
        for side in (1.0, -1.0):
            for index in range(rows):
                z = z_center - half_h + (index / (rows - 1)) * 2 * half_h
                emit(
                    f"RB-{code}-D{index + 1}{'A' if side > 0 else 'B'}",
                    support.start.x + ux * cover + nx * side * half_w,
                    support.start.y + uy * cover + ny * side * half_w,
                    z, (ux, uy, 0.0), usable,
                    group.diameter, False, "distribution", group.grade,
                )

    stirrups = [g for g in groups if g.bar_type == "stirrup"]
    if stirrups:
        group = stirrups[-1]  # 跨中较疏的一档，端部加密由设计文件表达
        spacing = max(float(group.spacing or 200.0) / 1000.0, 0.05)
        stations = max(2, min(int(usable / spacing) + 1, stirrup_station_cap))
        top_z = z_center + half_h
        bottom_z = z_center - half_h
        hoop_width = 2 * half_w
        hoop_height = 2 * half_h
        for index in range(stations):
            ratio = index / (stations - 1) if stations > 1 else 0.5
            chainage = cover + ratio * usable
            cx = support.start.x + ux * chainage
            cy = support.start.y + uy * chainage
            # 闭合箍筋拆成四段，查看器里就是一个矩形箍
            emit(f"RB-{code}-S{index + 1}T", cx - nx * half_w, cy - ny * half_w, top_z,
                 (nx, ny, 0.0), hoop_width, group.diameter, False, "stirrup_top", group.grade)
            emit(f"RB-{code}-S{index + 1}B", cx - nx * half_w, cy - ny * half_w, bottom_z,
                 (nx, ny, 0.0), hoop_width, group.diameter, False, "stirrup_bottom", group.grade)
            emit(f"RB-{code}-S{index + 1}L", cx - nx * half_w, cy - ny * half_w, bottom_z,
                 (0.0, 0.0, 1.0), hoop_height, group.diameter, True, "stirrup_left", group.grade)
            emit(f"RB-{code}-S{index + 1}R", cx + nx * half_w, cy + ny * half_w, bottom_z,
                 (0.0, 0.0, 1.0), hoop_height, group.diameter, True, "stirrup_right", group.grade)
    return bars


def _emit_beam_cage_bars(
    w: IfcWriter,
    *,
    owner: int,
    context: int,
    storey_place: int,
    beam,
    max_per_beam: int = 560,
    as_proxy: bool = False,
) -> list[int]:
    """Crown/wale beam cage: longitudinal bars plus closed stirrups.

    Beams previously carried no reinforcement geometry at all, so the top beams
    looked like plain concrete in the rebar model.
    """
    from types import SimpleNamespace

    points = list(getattr(getattr(beam, "axis", None), "points", []) or [])
    if len(points) < 2:
        return []
    start, end = points[0], points[-1]
    width = max(float(beam.section.width or 0.8), 0.3)
    height = max(float(beam.section.height or 0.8), 0.3)
    groups = []
    for group in beam.reinforcement or []:
        if group.bar_type == "longitudinal" and not group.count:
            spacing = max(float(group.spacing or 200.0) / 1000.0, 0.05)
            perimeter = 2 * (width + height)
            count = max(4, min(int(perimeter / spacing) + 1, 16))
            groups.append(group.model_copy(update={"count": count}))
        else:
            groups.append(group)
    adapter = SimpleNamespace(
        code=beam.code,
        start=SimpleNamespace(x=start.x, y=start.y),
        end=SimpleNamespace(x=end.x, y=end.y),
        section=SimpleNamespace(width=width, height=height),
        elevation=float(beam.elevation),
        reinforcement=groups,
    )
    return _emit_support_cage_bars(
        w, owner=owner, context=context, storey_place=storey_place,
        support=adapter, max_per_support=max_per_beam, as_proxy=as_proxy,
        stirrup_station_cap=120,
    )


def _pile_row_spec(project: Project) -> dict | None:
    """计算书给的是排桩（桩径/桩距/桩长）时，BIM 按排桩表达而不是等代墙。"""
    advanced = project.advanced_engineering or {}
    parsed = (advanced.get("calculationBookAutopilot") or {}).get("parsed") or {}
    pile = parsed.get("pile") or {}
    diameter = float(pile.get("diameter") or 0)
    spacing = float(pile.get("spacing") or 0)
    if diameter <= 0 or spacing <= 0:
        return None
    cage = parsed.get("pileCage") or {}
    return {
        "diameterM": diameter,
        "spacingM": spacing,
        "lengthM": float(pile.get("length") or 0) or None,
        "mainDiameterMm": float(cage.get("mainDiameterMm") or 25.0),
        "mainBarCount": int(cage.get("barCount") or 0) or None,
        "barSpacingMm": float(cage.get("barSpacingMm") or 120.0),
        "hoopDiameterMm": float(cage.get("hoopDiameterMm") or 8.0),
        "hoopSpacingMm": float(cage.get("hoopSpacingMm") or 150.0),
        "strongHoopDiameterMm": float(cage.get("strongHoopDiameterMm") or 14.0),
        "strongHoopSpacingMm": float(cage.get("strongHoopSpacingMm") or 2000.0),
        "coverM": float(cage.get("clearCoverMm") or 50.0) / 1000.0,
    }


def _emit_pile_row(
    w: IfcWriter,
    *,
    owner: int,
    context: int,
    storey_place: int,
    wall: DiaphragmWallPanel,
    spec: dict,
    as_proxy: bool = False,
    hoop_segments: int = 8,
    hoop_ring_cap: int = 6,
    strong_ring_cap: int = 5,
) -> tuple[list[int], list[int]]:
    """沿墙轴线布置排桩，并为每根桩生成圆形钢筋笼。"""
    path = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    if len(path) < 2:
        return [], []
    segments: list[tuple[float, float, float, float, float]] = []
    total = 0.0
    for left, right in zip(path[:-1], path[1:]):
        dx, dy = right.x - left.x, right.y - left.y
        seg = math.hypot(dx, dy)
        if seg <= 1e-9:
            continue
        segments.append((left.x, left.y, dx / seg, dy / seg, seg))
        total += seg
    if total <= 1e-9:
        return [], []

    diameter = float(spec["diameterM"])
    spacing = max(float(spec["spacingM"]), 0.3)
    count = max(2, int(round(total / spacing)) + 1)
    step = total / (count - 1)

    def point_at(chainage: float) -> tuple[float, float, float, float]:
        remaining = chainage
        for sx, sy, ux, uy, seg in segments:
            if remaining <= seg or seg is segments[-1][4]:
                return sx + ux * remaining, sy + uy * remaining, ux, uy
            remaining -= seg
        sx, sy, ux, uy, seg = segments[-1]
        return sx + ux * seg, sy + uy * seg, ux, uy

    top = float(wall.top_elevation)
    length = float(spec.get("lengthM") or (top - float(wall.bottom_elevation)))
    length = max(length, 1.0)
    bottom = top - length
    cover = min(max(float(spec["coverM"]), 0.03), diameter / 4)
    main_d = float(spec["mainDiameterMm"]) / 1000.0
    hoop_d = float(spec["hoopDiameterMm"]) / 1000.0
    main_count = int(spec.get("mainBarCount") or 0)
    if main_count <= 0:
        main_count = max(6, int(math.pi * max(diameter - 2 * cover, 0.1) / max(spec["barSpacingMm"] / 1000.0, 0.05)))
    main_count = max(6, min(main_count, 32))
    main_radius = max(diameter / 2 - cover - hoop_d - main_d / 2, 0.05)
    hoop_radius = max(diameter / 2 - cover - hoop_d / 2, 0.05)
    hoop_spacing = max(float(spec["hoopSpacingMm"]) / 1000.0, 0.1)
    rings = max(2, min(int(length / hoop_spacing) + 1, hoop_ring_cap))
    strong_spacing = max(float(spec.get("strongHoopSpacingMm") or 2000.0) / 1000.0, 0.5)
    strong_d = float(spec.get("strongHoopDiameterMm") or 14.0) / 1000.0
    strong_rings = max(1, min(int(length / strong_spacing) + 1, strong_ring_cap))
    chord = 2 * hoop_radius * math.sin(math.pi / hoop_segments)
    strong_chord = 2 * hoop_radius * math.sin(math.pi / hoop_segments)
    grade = "HRB400"

    pile_ids: list[int] = []
    bar_ids: list[int] = []
    for index in range(count):
        cx, cy, ux, uy = point_at(min(index * step, total))
        placement = w.local_placement(storey_place, cx, cy, bottom, refdir=(1, 0, 0), axis=(0, 0, 1))
        shape = w.circle_swept_shape(context, diameter / 2.0, length)
        pile = w.entity(
            f"IFCPILE({_step_string(_guid())},#{owner},{_step_string(f'{wall.panel_code}-P{index + 1:03d}')},"
            f"'Bored pile row generated from calculation book',$,#{placement},#{shape},$,.BORED.,.CAST_IN_SITU.)"
        )
        w.property_set(owner, pile, "Pset_PileRowDesign", {
            "HostWallCode": wall.panel_code,
            "PileDiameter_m": diameter,
            "PileSpacing_m": round(step, 3),
            "PileLength_m": round(length, 3),
            "PileTopElevation": top,
            "PileBottomElevation": round(bottom, 3),
            "MainBar": f"{main_count}D{int(main_d * 1000)}",
            "Hoop": f"D{int(hoop_d * 1000)}@{int(hoop_spacing * 1000)}",
            "ClearCover_mm": round(cover * 1000),
            "RepresentationNote": "计算按等代墙，BIM 按排桩；两者在 Pset 中分别标注",
            "Source": "calculation_book.pile",
        })
        pile_ids.append(pile)

        # 主筋：沿圆周均布
        for k in range(main_count):
            angle = 2 * math.pi * k / main_count
            bx = cx + main_radius * math.cos(angle)
            by = cy + main_radius * math.sin(angle)
            bar_ids.append(_add_reinforcing_bar(
                w, owner=owner, context=context, storey_place=storey_place,
                name=f"RB-{wall.panel_code}-P{index + 1:03d}-M{k + 1}",
                description="Pile main bar (circular cage)",
                x=bx, y=by, z=bottom + cover, refdir=(0, 0, 1),
                length=max(length - 2 * cover, 0.2),
                diameter_mm=main_d * 1000, grade=grade,
                properties={"HostElement": f"{wall.panel_code}-P{index + 1:03d}", "BarType": "pile_main",
                            "Diameter_mm": main_d * 1000, "RepresentationLevel": "circular_cage"},
                vertical=True, as_proxy=as_proxy,
            ))

        # 箍筋：每环拆成 hoop_segments 段，水平平面内闭合
        for r in range(rings):
            z = bottom + cover + (r + 0.5) * (length - 2 * cover) / rings
            for k in range(hoop_segments):
                angle = 2 * math.pi * k / hoop_segments
                bx = cx + hoop_radius * math.cos(angle)
                by = cy + hoop_radius * math.sin(angle)
                tangent = (-math.sin(angle), math.cos(angle), 0.0)
                bar_ids.append(_add_reinforcing_bar(
                    w, owner=owner, context=context, storey_place=storey_place,
                    name=f"RB-{wall.panel_code}-P{index + 1:03d}-H{r + 1}-{k + 1}",
                    description="Pile hoop bar (circular cage)",
                    x=bx, y=by, z=z, refdir=tangent,
                    length=chord, diameter_mm=hoop_d * 1000, grade="HPB300",
                    properties={"HostElement": f"{wall.panel_code}-P{index + 1:03d}", "BarType": "pile_hoop",
                                "Diameter_mm": hoop_d * 1000, "RepresentationLevel": "circular_cage"},
                    vertical=False, as_proxy=as_proxy,
                ))

        # 加强箍（吊装用），图中 φ14@2000
        for r in range(strong_rings):
            z = bottom + cover + (r + 0.5) * (length - 2 * cover) / strong_rings
            for k in range(hoop_segments):
                angle = 2 * math.pi * k / hoop_segments
                bx = cx + hoop_radius * math.cos(angle)
                by = cy + hoop_radius * math.sin(angle)
                tangent = (-math.sin(angle), math.cos(angle), 0.0)
                bar_ids.append(_add_reinforcing_bar(
                    w, owner=owner, context=context, storey_place=storey_place,
                    name=f"RB-{wall.panel_code}-P{index + 1:03d}-G{r + 1}-{k + 1}",
                    description="Pile strong hoop (lifting cage)",
                    x=bx, y=by, z=z, refdir=tangent,
                    length=strong_chord, diameter_mm=strong_d * 1000, grade="HRB400",
                    properties={"HostElement": f"{wall.panel_code}-P{index + 1:03d}", "BarType": "pile_strong_hoop",
                                "Diameter_mm": strong_d * 1000, "RepresentationLevel": "circular_cage"},
                    vertical=False, as_proxy=as_proxy,
                ))
    return pile_ids, bar_ids


def _wall_construction_spans(project: Project, wall: DiaphragmWallPanel) -> list[dict]:
    """Return construction panels rebased to the current canonical wall path."""
    resolution = resolve_wall_plan_path(project, wall)
    if len(resolution.points) < 2:
        return []
    settings = project.design_settings
    panels, _ = normalize_construction_panels(
        wall,
        resolution.points,
        target_length_m=float(getattr(settings, "wall_panel_target_length_m", 6.0) or 6.0),
        minimum_length_m=float(getattr(settings, "wall_panel_min_length_m", 3.0) or 3.0),
        maximum_length_m=float(getattr(settings, "wall_panel_max_length_m", 7.0) or 7.0),
    )
    rows: list[dict] = []
    for panel in panels:
        plan_path = [Point2D(x=float(item["x"]), y=float(item["y"])) for item in list(panel.get("planPath") or [])]
        if len(plan_path) < 2:
            continue
        rows.append({
            **panel,
            "start": plan_path[0],
            "end": plan_path[-1],
            "planPath": plan_path,
            "lengthM": round(polyline_length(plan_path), 4),
        })
    return rows

def _wall_design_properties(project: Project, wall: DiaphragmWallPanel, *, panel: dict | None = None) -> dict:
    result = wall.design_results
    embedment = (project.excavation.bottom_elevation - wall.bottom_elevation) if project.excavation else None
    properties = {
        "WallType": "diaphragm_wall",
        "CalculationWallId": wall.id,
        "CalculationWallCode": wall.panel_code,
        "DesignFaceCode": wall.design_face_code,
        "DesignLength_m": wall.design_length,
        "DesignLengthIsOptimizationVariable": True,
        "FaceSegmentIds": ";".join(wall.face_segment_ids),
        "Thickness": wall.thickness,
        "TopElevation": wall.top_elevation,
        "BottomElevation": wall.bottom_elevation,
        "BottomElevationSource": wall.bottom_elevation_source,
        "ToeZoneId": wall.toe_zone_id,
        "ToeProfileStatus": wall.toe_profile_status,
        "EmbedmentDepth": round(embedment, 3) if embedment is not None else None,
        "ConcreteGrade": wall.concrete_grade,
        "RebarGrade": wall.rebar_grade,
        "MaxMoment_kNm_per_m": result.max_moment if result else None,
        "MaxShear_kN_per_m": result.max_shear if result else None,
        "MaxDisplacement_mm": result.max_displacement if result else None,
        "MomentDesign_kNm_per_m": result.max_moment_design if result else None,
        "ShearDesign_kN_per_m": result.max_shear_design if result else None,
        "RequiredAs_mm2_per_m": result.required_reinforcement_area if result else None,
        "ProvidedAs_mm2_per_m": result.provided_reinforcement_area if result else None,
        "MomentCapacity_kNm_per_m": result.moment_capacity if result else None,
        "ShearCapacity_kN_per_m": result.shear_capacity if result else None,
        "RebarDiameter_mm": result.rebar_diameter if result else None,
        "RebarSpacing_mm": result.rebar_spacing if result else None,
        "GoverningRuleIds": ";".join(result.governing_rule_ids) if result else None,
        "FormulaTrace": "; ".join(result.formula_trace) if result else None,
        "CheckStatus": result.check_status if result else "manual_review",
        "ProfessionalReviewRequired": wall.professional_review_required,
    }
    if panel:
        properties.update({
            "ConstructionPanelIndex": panel["panelIndex"],
            "ConstructionPanelCode": panel["panelCode"],
            "StartChainage_m": panel["startChainageM"],
            "EndChainage_m": panel["endChainageM"],
            "ConstructionPanelLength_m": panel["lengthM"],
            "CageCount": panel["cageCount"],
            "JointType": panel["jointType"],
            "LiftingReviewRequired": panel["liftingReviewRequired"],
        })
    return properties

def export_simplified_ifc(project: Project, output_dir: str | Path, export_mode: str = "design_detailed") -> Path:
    """Write an IFC4 STEP model with geometry and property sets.

    IfcOpenShell is not required in the runtime; this writer creates a compact IFC4 model with swept
    solid geometry for diaphragm walls, crown/wale beams, internal supports and temporary columns.
    Reinforcement is exported both as parameterized property sets and representative IfcReinforcingBar group entities for downstream BIM review.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if export_mode in {"coordination_light", "light", "coordination"}:
        mode = "coordination_light"
    elif export_mode in {"analysis_model", "analysis", "calculation"}:
        mode = "analysis_model"
    elif export_mode in {"construction_visual", "viewer_detailed", "visual_detailed", "construction"}:
        mode = "construction_visual"
    else:
        mode = "design_detailed"
    detailed_mode = mode in {"design_detailed", "construction_visual"}
    construction_visual_mode = mode == "construction_visual"
    analysis_mode = mode == "analysis_model"
    path = out_dir / f"{project.id}_{mode}.ifc"
    timestamp = datetime.now(timezone.utc).isoformat()
    w = IfcWriter()

    person = w.entity("IFCPERSON($,$,'PitGuard',$,$,$,$,$)")
    org = w.entity("IFCORGANIZATION($,'PitGuard BIM Designer',$,$,$)")
    person_org = w.entity(f"IFCPERSONANDORGANIZATION(#{person},#{org},$)")
    app = w.entity(f"IFCAPPLICATION(#{org},{_step_string(SOFTWARE_VERSION)},'PitGuard BIM Designer','PITGUARD')")
    owner = w.entity(f"IFCOWNERHISTORY(#{person_org},#{app},$,.ADDED.,$,$,$,0)")

    unit_length = w.entity("IFCSIUNIT($,.LENGTHUNIT.,$,.METRE.)")
    unit_area = w.entity("IFCSIUNIT($,.AREAUNIT.,$,.SQUARE_METRE.)")
    unit_volume = w.entity("IFCSIUNIT($,.VOLUMEUNIT.,$,.CUBIC_METRE.)")
    unit_plane = w.entity("IFCSIUNIT($,.PLANEANGLEUNIT.,$,.RADIAN.)")
    units = w.entity(f"IFCUNITASSIGNMENT((#{unit_length},#{unit_area},#{unit_volume},#{unit_plane}))")
    origin_axis = w.axis2_3d(0, 0, 0)
    context = w.entity(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#{origin_axis},$)")

    project_entity = w.entity(f"IFCPROJECT({_step_string(_guid())},#{owner},{_step_string(project.name)},'PitGuard engineering IFC export; professional review required',$,$,$,(#{context}),#{units})")
    site_place = w.local_placement(None, 0, 0, 0)
    site = w.entity(f"IFCSITE({_step_string(_guid())},#{owner},{_step_string(project.location or 'Local Site')},'Generated from PitGuard project',$,#{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    building_place = w.local_placement(site_place, 0, 0, 0)
    building = w.entity(f"IFCBUILDING({_step_string(_guid())},#{owner},'Foundation Pit Support Model',$,$,#{building_place},$,$,.ELEMENT.,$,$,$)")
    storey_place = w.local_placement(building_place, 0, 0, 0)
    storey = w.entity(f"IFCBUILDINGSTOREY({_step_string(_guid())},#{owner},'Temporary support stage model',$,$,#{storey_place},$,$,.ELEMENT.,0.)")
    w.entity(f"IFCRELAGGREGATES({_step_string(_guid())},#{owner},$,$,#{project_entity},(#{site}))")
    w.entity(f"IFCRELAGGREGATES({_step_string(_guid())},#{owner},$,$,#{site},(#{building}))")
    w.entity(f"IFCRELAGGREGATES({_step_string(_guid())},#{owner},$,$,#{building},(#{storey}))")

    material_concrete = w.entity("IFCMATERIAL('Concrete',$,$)")
    material_rebar = w.entity("IFCMATERIAL('Reinforcement steel',$,$)")
    material_steel = w.entity("IFCMATERIAL('Structural steel',$,$)")
    # Warm red-brown steel so bars separate visually from concrete in viewers.
    w.rebar_style_id = w.surface_style("Rebar steel", 0.72, 0.26, 0.20)

    product_ids: list[int] = []
    concrete_product_ids: list[int] = []
    steel_product_ids: list[int] = []
    rebar_product_ids: list[int] = []

    support_quality = evaluate_support_layout_quality(project) if project.retaining_system else None
    support_metrics = dict(support_quality.metrics or {}) if support_quality else {}
    # Cage descriptors (face diameters, spacings, ties) come from the same
    # visualization builder used by the dedicated rebar export.
    cage_by_panel: dict[str, dict] = {}
    if detailed_mode and project.retaining_system:
        try:
            from app.ifc.rebar_visualization import build_rebar_ifc_visualization

            visualization = build_rebar_ifc_visualization(project, max_bars=400)
            for cage in visualization.get("cages") or []:
                code = str(cage.get("panelCode") or "")
                if code:
                    cage_by_panel[code] = cage
        except Exception:
            cage_by_panel = {}
    latest_calculation = project.calculation_results[-1] if project.calculation_results else None
    calculation_assurance = dict(getattr(latest_calculation, "calculation_assurance", {}) or {}) if latest_calculation else {}
    w.property_set(owner, project_entity, "Pset_PitGuardExportControl", {
        "SoftwareVersion": SOFTWARE_VERSION,
        "ExportMode": mode,
        "ProjectId": project.id,
        "WallPlanDesignLengthIsVariable": True,
        "WallVerticalLengthIsVariable": True,
        "WallEndpointJunctionIncludedInObjective": True,
        "IllegalSupportCrossingCount": support_metrics.get("supportCrossingCount"),
        "WallConnectionPointCount": support_metrics.get("wallConnectionPointCount"),
        "WallJunctionCount": support_metrics.get("wallJunctionCount"),
        "HighDegreeWallJunctionCount": support_metrics.get("highDegreeWallJunctionCount"),
        "InternalJunctionCount": support_metrics.get("internalJunctionCount"),
        "PlanIntersectionComplexity": support_metrics.get("planIntersectionComplexity"),
        "CalculationContractId": getattr(latest_calculation, "calculation_contract_id", None),
        "CalculationInputSnapshotHash": getattr(latest_calculation, "input_snapshot_hash", None),
        "CalculationAdoptedDesignHash": getattr(latest_calculation, "adopted_design_snapshot_hash", None),
        "CalculationResultHash": getattr(latest_calculation, "result_hash", None),
        "CalculationAssuranceStatus": calculation_assurance.get("status"),
        "ProfessionalReviewRequired": True,
    })

    object_manifest: dict = {
        "projectId": project.id,
        "projectName": project.name,
        "softwareVersion": SOFTWARE_VERSION,
        "exportMode": mode,
        "optimizationTrace": {
            "wallPlanDesignLengthIsVariable": True,
            "wallVerticalLengthIsVariable": True,
            "wallEndpointJunctionIncludedInObjective": True,
            "supportLayoutMetrics": support_metrics,
        },
        "calculationBaseline": {
            "calculationResultId": getattr(latest_calculation, "id", None),
            "calculationContractId": getattr(latest_calculation, "calculation_contract_id", None),
            "inputSnapshotHash": getattr(latest_calculation, "input_snapshot_hash", None),
            "adoptedDesignSnapshotHash": getattr(latest_calculation, "adopted_design_snapshot_hash", None),
            "resultHash": getattr(latest_calculation, "result_hash", None),
            "assuranceStatus": calculation_assurance.get("status"),
            "stageCoverage": calculation_assurance.get("stageCoverage"),
            "solverRuntime": dict((calculation_assurance.get("contract") or {}).get("solverRuntime") or {}),
        },
        "calculationWalls": [],
        "constructionPanels": [],
        "constructionJoints": [],
        "rebarCageGroups": [],
        "reinforcementGroups": [],
        "supports": [],
        "supportWallConnections": support_metrics.get("wallConnectionPoints", []),
        "supportWallJunctions": support_metrics.get("wallJunctionPoints", []),
    }

    if project.retaining_system:
        pile_row_spec = _pile_row_spec(project)
        for wall in project.retaining_system.diaphragm_walls:
            if len(wall.axis.points) < 2:
                continue
            spans = _wall_construction_spans(project, wall)
            object_manifest["calculationWalls"].append({
                "id": wall.id, "code": wall.panel_code, "designFaceCode": wall.design_face_code,
                "designLengthM": wall.design_length, "constructionPanelCount": len(spans),
                "bottomElevation": wall.bottom_elevation, "toeZoneId": wall.toe_zone_id,
            })
            assembly = None
            child_elements: list[int] = []
            panel_element_rows: list[dict] = []
            if detailed_mode:
                assembly_place = w.local_placement(storey_place, 0, 0, 0)
                assembly = w.entity(
                    f"IFCELEMENTASSEMBLY({_step_string(_guid())},#{owner},{_step_string(wall.panel_code + '-ASSEMBLY')},"
                    f"'Calculation wall and construction panel assembly generated by PitGuard',$,#{assembly_place},$,$,.SITE.,.USERDEFINED.)"
                )
                product_ids.append(assembly)
                w.property_set(owner, assembly, "Pset_CalculationWallAssembly", {
                    **_wall_design_properties(project, wall),
                    "ConstructionPanelCount": len(spans),
                    "AssemblySemantics": "calculation_wall_parent_with_constructible_panel_children",
                })
            if detailed_mode and pile_row_spec:
                pile_ids, pile_bar_ids = _emit_pile_row(
                    w, owner=owner, context=context, storey_place=storey_place,
                    wall=wall, spec=pile_row_spec, as_proxy=construction_visual_mode,
                )
                product_ids.extend(pile_ids)
                product_ids.extend(pile_bar_ids)
                rebar_product_ids.extend(pile_bar_ids)
                child_elements.extend(pile_ids)
                if assembly:
                    w.property_set(owner, assembly, "Pset_PileRowSummary", {
                        "PileCount": len(pile_ids),
                        "PileDiameter_m": pile_row_spec["diameterM"],
                        "PileSpacing_m": round(pile_row_spec["spacingM"], 3),
                        "PileLength_m": pile_row_spec.get("lengthM"),
                        "BarsPerPile": len(pile_bar_ids) // max(len(pile_ids), 1),
                        "CalculationModel": "equivalent diaphragm wall (internal calculation only)",
                        "BimModel": "bored pile row with circular cage",
                    })
                    if child_elements:
                        w.entity(
                            f"IFCRELAGGREGATES({_step_string(_guid())},#{owner},'Piles of calculation wall',$,#{assembly},("
                            + ",".join(f"#{eid}" for eid in child_elements) + "))"
                        )
                object_manifest.setdefault("pileRows", []).append({
                    "calculationWallCode": wall.panel_code,
                    "pileCount": len(pile_ids),
                    "barCount": len(pile_bar_ids),
                    "diameterM": pile_row_spec["diameterM"],
                    "spacingM": round(pile_row_spec["spacingM"], 3),
                })
                continue
            export_spans = spans if detailed_mode else [{
                "panelIndex": 1, "panelCode": wall.panel_code, "startChainageM": 0.0,
                "endChainageM": _line_direction(wall.axis.points[0], wall.axis.points[-1])[2],
                "lengthM": _line_direction(wall.axis.points[0], wall.axis.points[-1])[2],
                "start": wall.axis.points[0], "end": wall.axis.points[-1], "cageCount": 1,
                "jointType": "calculation_wall", "liftingReviewRequired": True,
            }]
            for panel in export_spans:
                start_point, end_point = panel["start"], panel["end"]
                ux, uy, panel_length, _ = _line_direction(start_point, end_point)
                mx, my = _midpoint(start_point, end_point)
                height = wall.top_elevation - wall.bottom_elevation
                placement = w.local_placement(storey_place, mx, my, wall.bottom_elevation, refdir=(ux, uy, 0))
                shape = w.rect_swept_shape(context, panel_length, wall.thickness, height)
                element = w.entity(
                    f"IFCWALL({_step_string(_guid())},#{owner},{_step_string(panel['panelCode'])},"
                    f"'Diaphragm wall construction panel generated by PitGuard',$,#{placement},#{shape},$,.STANDARD.)"
                )
                product_ids.append(element)
                concrete_product_ids.append(element)
                child_elements.append(element)
                panel_element_rows.append({"elementId": element, "panel": panel})
                w.property_set(owner, element, "Pset_RetainingWallDesign", _wall_design_properties(project, wall, panel=panel if detailed_mode else None))
                w.property_set(owner, element, "Pset_ParameterizedReinforcement", {
                    "Strategy": "parameterized_only" if not detailed_mode else ("visual_proxy_groups_plus_parameterized_rebar" if construction_visual_mode else "parameterized_groups_plus_representative_ifc_rebars"),
                    "Groups": _rebar_summary(wall.reinforcement),
                    "DetailedIfcReinforcingBar": "omitted_for_coordination_light" if not detailed_mode else ("visual_proxy_entities_generated" if construction_visual_mode else "representative_group_entities_generated_per_construction_panel"),
                    "ExportMode": mode,
                    "ConstructionPanelCode": panel["panelCode"],
                    "CalculationWallCode": wall.panel_code,
                })
                object_manifest["constructionPanels"].append({
                    "calculationWallId": wall.id, "calculationWallCode": wall.panel_code,
                    "panelCode": panel["panelCode"], "panelIndex": panel["panelIndex"],
                    "startChainageM": panel["startChainageM"], "endChainageM": panel["endChainageM"],
                    "lengthM": panel["lengthM"], "cageCount": panel["cageCount"],
                    "jointType": panel["jointType"], "liftingReviewRequired": panel["liftingReviewRequired"],
                    "ifcEntityId": element,
                })
                if detailed_mode:
                    panel_rebar_ids: list[int] = []
                    cage = cage_by_panel.get(str(panel.get("panelCode")))
                    if cage:
                        # Full cage: vertical bars on both faces, horizontal bars
                        # and ties at the design spacing.
                        panel_rebar_ids = _emit_wall_cage_bars(
                            w, owner=owner, context=context, storey_place=storey_place,
                            wall=wall, panel=panel, cage=cage,
                            as_proxy=construction_visual_mode,
                        )
                        product_ids.extend(panel_rebar_ids)
                        rebar_product_ids.extend(panel_rebar_ids)
                        object_manifest["reinforcementGroups"].append({
                            "hostPanelCode": panel["panelCode"], "calculationWallCode": wall.panel_code,
                            "representation": "full_cage_grid",
                            "barCount": len(panel_rebar_ids),
                            "ifcEntityIds": panel_rebar_ids,
                        })
                    else:
                        for idx, group in enumerate(wall.reinforcement, start=1):
                            offset = (idx - (len(wall.reinforcement) + 1) / 2.0) * max(wall.thickness / max(len(wall.reinforcement), 2), 0.05)
                            bar = _add_reinforcing_bar(
                                w, owner=owner, context=context, storey_place=storey_place,
                                name=f"RB-{panel['panelCode']}-{idx}",
                                description=f"Representative reinforcement group for construction panel: {group.name}",
                                x=mx - uy * offset, y=my + ux * offset, z=wall.bottom_elevation,
                                refdir=(0, 0, 1), length=height, diameter_mm=group.diameter, grade=group.grade,
                                properties={
                                    "HostElement": panel["panelCode"], "CalculationWallCode": wall.panel_code,
                                    "ConstructionPanelCode": panel["panelCode"], "GroupName": group.name,
                                    "BarType": group.bar_type, "Diameter_mm": group.diameter,
                                    "Spacing_mm": group.spacing, "Count": group.count,
                                    "AreaPerMeter_mm2_per_m": group.area_per_meter,
                                    "RequiredAreaPerMeter_mm2_per_m": group.required_area_per_meter,
                                    "LocationDescription": group.location_description,
                                    "RepresentationLevel": "representative_group_bar_per_construction_panel",
                                }, vertical=True, as_proxy=construction_visual_mode,
                            )
                            product_ids.append(bar)
                            rebar_product_ids.append(bar)
                            panel_rebar_ids.append(bar)
                            object_manifest["reinforcementGroups"].append({
                                "hostPanelCode": panel["panelCode"], "calculationWallCode": wall.panel_code,
                                "groupId": group.id, "groupName": group.name, "barType": group.bar_type,
                                "diameterMm": group.diameter, "spacingMm": group.spacing,
                                "ifcEntityId": bar,
                            })
                    if panel_rebar_ids:
                        cage_group = w.entity(
                            f"IFCGROUP({_step_string(_guid())},#{owner},{_step_string('CAGE-' + str(panel['panelCode']))},"
                            f"'Reinforcement cage group for construction panel { _ifc_text(str(panel['panelCode'])) }','REBAR_CAGE')"
                        )
                        w.property_set(owner, cage_group, "Pset_RebarCageTraceability", {
                            "CalculationWallCode": wall.panel_code,
                            "ConstructionPanelCode": panel["panelCode"],
                            "PanelIndex": panel["panelIndex"],
                            "RepresentativeBarEntityCount": len(panel_rebar_ids),
                            "JointType": panel["jointType"],
                            "LiftingReviewRequired": panel["liftingReviewRequired"],
                            "RepresentationBoundary": "Representative IFC bars; fabrication quantities remain in BBS/detailing package",
                        })
                        w.entity(
                            f"IFCRELASSIGNSTOGROUP({_step_string(_guid())},#{owner},'Representative bars assigned to cage',$,("
                            + ",".join(f"#{eid}" for eid in panel_rebar_ids) + f"),$ ,#{cage_group})"
                        )
                        object_manifest["rebarCageGroups"].append({
                            "calculationWallCode": wall.panel_code,
                            "constructionPanelCode": panel["panelCode"],
                            "ifcGroupEntityId": cage_group,
                            "representativeBarEntityIds": panel_rebar_ids,
                        })
            if assembly and child_elements:
                w.entity(
                    f"IFCRELAGGREGATES({_step_string(_guid())},#{owner},'Construction panels of calculation wall',$,#{assembly},("
                    + ",".join(f"#{eid}" for eid in child_elements) + "))"
                )
                for left, right in zip(panel_element_rows[:-1], panel_element_rows[1:]):
                    left_panel, right_panel = left["panel"], right["panel"]
                    relation = w.entity(
                        f"IFCRELCONNECTSELEMENTS({_step_string(_guid())},#{owner},"
                        f"{_step_string('JOINT-' + str(left_panel['panelCode']) + '-' + str(right_panel['panelCode']))},"
                        f"{_step_string('Construction joint between adjacent diaphragm-wall panels')},$,"
                        f"#{left['elementId']},#{right['elementId']})"
                    )
                    object_manifest["constructionJoints"].append({
                        "calculationWallCode": wall.panel_code,
                        "leftPanelCode": left_panel["panelCode"],
                        "rightPanelCode": right_panel["panelCode"],
                        "jointType": right_panel.get("jointType") or left_panel.get("jointType"),
                        "chainageM": right_panel.get("startChainageM"),
                        "ifcRelationEntityId": relation,
                    })
        for beam in [*project.retaining_system.crown_beams, *project.retaining_system.wale_beams, *getattr(project.retaining_system, "ring_beams", [])]:
            axis = _axis_from_polyline(beam)
            if not axis:
                continue
            start, end = axis
            ux, uy, length, _ = _line_direction(start, end)
            width = beam.section.width or 0.8
            height = beam.section.height or 0.8
            mx, my = _midpoint(start, end)
            placement = w.local_placement(storey_place, mx, my, beam.elevation - height / 2.0, refdir=(ux, uy, 0))
            shape = w.rect_swept_shape(context, length, width, height)
            entity_name = "IFCBEAM"
            predefined = ".BEAM."
            element = w.entity(f"{entity_name}({_step_string(_guid())},#{owner},{_step_string(beam.code)},'Crown/wale beam generated by PitGuard',$,#{placement},#{shape},$,{predefined})")
            product_ids.append(element)
            concrete_product_ids.append(element)
            w.property_set(owner, element, "Pset_BeamDesign", {
                "Code": beam.code,
                "BeamRole": getattr(beam, "beam_role", "manual"),
                "SupportLevel": getattr(beam, "support_level", None),
                "Elevation": beam.elevation,
                "Section": beam.section.name,
                "Material": beam.material.grade,
                "DesignMoment_kNm": getattr(getattr(beam, "design_result", None), "max_moment_design", None),
                "DesignShear_kN": getattr(getattr(beam, "design_result", None), "max_shear_design", None),
                "MaxDeflection_m": getattr(getattr(beam, "design_result", None), "max_deflection", None),
                "MainRebar": (f"D{beam.design_result.main_bar_diameter}@{beam.design_result.main_bar_spacing}" if getattr(beam, "design_result", None) else None),
                "Stirrups": (f"D{beam.design_result.stirrup_diameter}@{beam.design_result.stirrup_spacing}" if getattr(beam, "design_result", None) else None),
                "NodeRebarCoordination": getattr(getattr(beam, "design_result", None), "node_additional_reinforcement_note", None),
                "CheckStatus": getattr(getattr(beam, "design_result", None), "check_status", None),
            })
            if detailed_mode:
                beam_bars = _emit_beam_cage_bars(
                    w, owner=owner, context=context, storey_place=storey_place,
                    beam=beam, as_proxy=construction_visual_mode,
                )
                product_ids.extend(beam_bars)
                rebar_product_ids.extend(beam_bars)
                object_manifest.setdefault("beamCages", []).append({
                    "beamCode": beam.code,
                    "barCount": len(beam_bars),
                    "representation": "full_cage_grid",
                })
        for support in project.retaining_system.supports:
            ux, uy, length, _ = _line_direction(support.start, support.end)
            width = support.section.width or 0.8
            height = support.section.height or 0.8
            mx, my = _midpoint(support.start, support.end)
            placement = w.local_placement(storey_place, mx, my, support.elevation - height / 2.0, refdir=(ux, uy, 0))
            shape = w.rect_swept_shape(context, length, width, height)
            element = w.entity(f"IFCBEAM({_step_string(_guid())},#{owner},{_step_string(support.code)},'Internal support beam generated by PitGuard',$,#{placement},#{shape},$,.BEAM.)")
            product_ids.append(element)
            concrete_product_ids.append(element if support.section_type == "rc_rectangular" else None)
            if support.section_type != "rc_rectangular":
                steel_product_ids.append(element)
            w.property_set(owner, element, "Pset_InternalSupportDesign", {
                "LevelIndex": support.level_index,
                "Elevation": support.elevation,
                "SectionType": support.section_type,
                "SupportRole": support.support_role,
                "LayoutNote": support.layout_note,
                "SpanLength_m": support.span_length,
                "BaySpacing_m": support.bay_spacing,
                "StartFaceCode": support.start_face_code,
                "EndFaceCode": support.end_face_code,
                "StartTributaryWidth_m": getattr(support, "start_tributary_width", None),
                "EndTributaryWidth_m": getattr(support, "end_tributary_width", None),
                "ForceDistributionNote": getattr(support, "force_distribution_note", None),
                "ForceDistributionModel": "continuous_wale_beam_elastic_supports_v1_6",
                "AnalysisModelRole": "axial bar / spring element" if analysis_mode else None,
                "AxisStartXY": f"{support.start.x:.3f},{support.start.y:.3f}",
                "AxisEndXY": f"{support.end.x:.3f},{support.end.y:.3f}",
                "SpringEAOverL_kN_per_m": round(((support.material.elastic_modulus or 3.25e7) * (support.section.width or 1.0) * (support.section.height or 1.0) / max(length, 0.1)), 3) if analysis_mode else None,
                "SectionSize": support.section.name,
                "Material": support.material.grade,
                "DesignAxialForce_kN": support.design_axial_force,
                "Preload_kN": support.preload,
                "PreloadRatio": getattr(support, "preload_ratio", None),
                "TemperatureDelta_C": getattr(support, "temperature_delta_c", None),
                "ThermalAxialForce_kN": getattr(support, "thermal_axial_force", None),
                "GapClosureForce_kN": getattr(support, "gap_closure_force", None),
                "ConstructionDeviation_mm": getattr(support, "construction_deviation_mm", None),
                "EccentricityMoment_kNm": getattr(support, "eccentricity_moment", None),
                "EffectiveAxialForceStandard_kN": getattr(support, "effective_axial_force_standard", None),
                "ConstructionEffectNote": getattr(support, "construction_effect_note", None),
                "CheckStatus": "calculated_pass_pending_professional_signoff",
                "ReinforcementGroups": _rebar_summary(support.reinforcement),
            })
            object_manifest["supports"].append({
                "id": support.id, "code": support.code, "levelIndex": support.level_index,
                "role": support.support_role, "startFaceCode": support.start_face_code,
                "endFaceCode": support.end_face_code, "spanLengthM": support.span_length,
                "ifcEntityId": element,
            })
            if detailed_mode:
                support_bars = _emit_support_cage_bars(
                    w, owner=owner, context=context, storey_place=storey_place,
                    support=support, as_proxy=construction_visual_mode,
                )
                product_ids.extend(support_bars)
                rebar_product_ids.extend(support_bars)
                object_manifest.setdefault("supportCages", []).append({
                    "supportCode": support.code,
                    "barCount": len(support_bars),
                    "representation": "full_cage_grid",
                })
        for column in project.retaining_system.columns:
            width = column.section.width or 0.6
            depth = column.section.height or width
            height = column.top_elevation - column.bottom_elevation
            placement = w.local_placement(storey_place, column.location.x, column.location.y, column.bottom_elevation, refdir=(1, 0, 0))
            shape = w.rect_swept_shape(context, width, depth, height)
            element = w.entity(f"IFCCOLUMN({_step_string(_guid())},#{owner},{_step_string(column.code)},'Temporary column generated by PitGuard',$,#{placement},#{shape},$,.COLUMN.)")
            product_ids.append(element)
            steel_product_ids.append(element)
            w.property_set(owner, element, "Pset_TemporaryColumnDesign", {
                "TopElevation": column.top_elevation,
                "BottomElevation": column.bottom_elevation,
                "Section": column.section.name,
                "Material": column.material.grade,
                "SupportedSupportCodes": ",".join(column.support_codes),
                "ServiceAreaNote": getattr(column, "service_area_note", None),
                "FoundationType": column.foundation_design.foundation_type if column.foundation_design else None,
                "FoundationCode": column.foundation_design.code if column.foundation_design else None,
                "PileDiameter_m": column.foundation_design.pile_diameter if column.foundation_design else None,
                "PileLength_m": column.foundation_design.pile_length if column.foundation_design else None,
                "PileCapacity_kN": column.foundation_design.pile_capacity if column.foundation_design else None,
                "PileUtilization": column.foundation_design.pile_utilization if column.foundation_design else None,
                "V20IfcDetailLevel": "column + pile proxy + pile design property set",
            })
            fdn = column.foundation_design
            if fdn and fdn.foundation_type == "column_pile" and fdn.pile_length and fdn.pile_diameter:
                pile_top = column.bottom_elevation
                pile_height = max(float(fdn.pile_length), 0.5)
                pile_place = w.local_placement(storey_place, column.location.x, column.location.y, pile_top - pile_height, refdir=(1, 0, 0))
                pile_shape = w.rect_swept_shape(context, max(fdn.pile_diameter, 0.3), max(fdn.pile_diameter, 0.3), pile_height)
                pile_el = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string('PILE-' + column.code)},'Temporary column pile proxy generated by PitGuard V2.0',$,#{pile_place},#{pile_shape},$,$)")
                product_ids.append(pile_el)
                concrete_product_ids.append(pile_el)
                w.property_set(owner, pile_el, "Pset_ColumnPileDesign", {
                    "HostColumn": column.code,
                    "PileDiameter_m": fdn.pile_diameter,
                    "PileLength_m": fdn.pile_length,
                    "PileCount": fdn.pile_count,
                    "PileCapacity_kN": fdn.pile_capacity,
                    "PileUtilization": fdn.pile_utilization,
                    "PileTipElevation": fdn.pile_tip_elevation,
                    "DesignNote": fdn.design_note,
                    "ConstructionStageRole": "temporary support column vertical DOF in V2.0 spatial frame",
                })
        for node in getattr(project.retaining_system, "support_nodes", []):
            plate = node.bearing_plate
            size = max(0.35, plate.plate_width if plate else 0.5)
            placement = w.local_placement(storey_place, node.location.x, node.location.y, node.elevation - size / 2.0, refdir=(1, 0, 0))
            shape = w.rect_swept_shape(context, size, size, size)
            element = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string(node.code)},'Support-wale node generated by PitGuard',$,#{placement},#{shape},$,$)")
            product_ids.append(element)
            concrete_product_ids.append(element)
            w.property_set(owner, element, "Pset_SupportWaleNodeDesign", {
                "SupportCode": node.support_code,
                "LevelIndex": node.level_index,
                "Elevation": node.elevation,
                "FaceCode": node.face_code,
                "WaleBeamCode": node.wale_beam_code,
                "NodeType": node.node_type,
                "PlateWidth_m": plate.plate_width if plate else None,
                "PlateHeight_m": plate.plate_height if plate else None,
                "PlateThickness_m": plate.plate_thickness if plate else None,
                "BearingStress_kPa": plate.bearing_stress if plate else None,
                "BearingCapacity_kPa": plate.bearing_capacity if plate else None,
                "CheckStatus": node.check_status,
                "ReinforcementGroups": _rebar_summary(node.reinforcement),
                "V20NodeRigidZone": "support-wale rigid zone participates in spatial frame matrix",
                "EmbeddedParts": "bearing plate + anchor/preembedded proxy exported" if detailed_mode else "omitted_for_coordination_light",
                "ExportMode": mode,
            })
            # V2.0 explicit bearing plate and preembedded/anchor proxy for construction-detail review.
            if detailed_mode and plate:
                plate_place = w.local_placement(storey_place, node.location.x, node.location.y, node.elevation, refdir=(1, 0, 0), axis=(0, 0, 1))
                plate_shape = w.rect_swept_shape(context, max(plate.plate_width, 0.05), max(plate.plate_height, 0.05), max(plate.plate_thickness, 0.02))
                plate_el = w.entity(f"IFCPLATE({_step_string(_guid())},#{owner},{_step_string('BPL-' + node.code)},'Bearing plate generated by PitGuard V2.0',$,#{plate_place},#{plate_shape},$,.USERDEFINED.)")
                product_ids.append(plate_el)
                steel_product_ids.append(plate_el)
                w.property_set(owner, plate_el, "Pset_BearingPlateDetail", {
                    "HostNode": node.code,
                    "SupportCode": node.support_code,
                    "PlateWidth_m": plate.plate_width,
                    "PlateHeight_m": plate.plate_height,
                    "PlateThickness_m": plate.plate_thickness,
                    "BearingStress_kPa": plate.bearing_stress,
                    "BearingCapacity_kPa": plate.bearing_capacity,
                    "DetailStatus": "施工图深化接口；焊缝、锚筋和局部压应力需复核",
                })
            if detailed_mode:
                anchor_place = w.local_placement(storey_place, node.location.x, node.location.y, node.elevation - 0.2, refdir=(1, 0, 0), axis=(0, 0, 1))
                anchor_shape = w.rect_swept_shape(context, 0.35, 0.12, 0.35)
                anchor_el = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string('EMB-' + node.code)},'Preembedded anchor proxy generated by PitGuard V2.0',$,#{anchor_place},#{anchor_shape},$,$)")
                product_ids.append(anchor_el)
                steel_product_ids.append(anchor_el)
                w.property_set(owner, anchor_el, "Pset_PreembeddedAnchorDetail", {
                    "HostNode": node.code,
                    "HostWaleBeam": node.wale_beam_code,
                    "HostSupport": node.support_code,
                    "DetailRole": "preembedded plate / anchor bar / shear key proxy",
                    "ConstructionStage": "support installation and preload stage",
                })

        if analysis_mode:
            # Analysis-model IFC: exchange axes, support springs, lateral load proxies and construction-stage activation data.
            # It intentionally omits physical rebar/plates while preserving data useful for FEM/structural-analysis exchange.
            for idx, support in enumerate(project.retaining_system.supports, start=1):
                ux, uy, length, _ = _line_direction(support.start, support.end)
                mx, my = _midpoint(support.start, support.end)
                size = 0.18
                spring_place = w.local_placement(storey_place, mx, my, support.elevation, refdir=(ux, uy, 0))
                spring_shape = w.rect_swept_shape(context, max(length, 0.2), size, size)
                spring_el = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string('SPR-' + support.code)},'Analysis support axial spring generated by PitGuard V2.0.7',$,#{spring_place},#{spring_shape},$,$)")
                product_ids.append(spring_el)
                steel_product_ids.append(spring_el)
                area = max((support.section.width or 1.0) * (support.section.height or 1.0), 0.01)
                e_mod = support.material.elastic_modulus or 3.25e7
                k_axial = e_mod * area / max(length, 0.1)
                w.property_set(owner, spring_el, "Pset_AnalysisSupportSpring", {
                    "HostSupport": support.code,
                    "LevelIndex": support.level_index,
                    "Elevation": support.elevation,
                    "AxisStartX": support.start.x,
                    "AxisStartY": support.start.y,
                    "AxisEndX": support.end.x,
                    "AxisEndY": support.end.y,
                    "DirectionCosineX": round(ux, 6),
                    "DirectionCosineY": round(uy, 6),
                    "Length_m": round(length, 3),
                    "Area_m2": round(area, 4),
                    "ElasticModulus_kPa": e_mod,
                    "EAOverL_kN_per_m": round(k_axial, 3),
                    "Preload_kN": support.preload,
                    "DesignAxialForce_kN": support.design_axial_force,
                    "AnalysisRole": "axial_spring_element",
                })
            for wall in project.retaining_system.diaphragm_walls:
                if len(wall.axis.points) < 2:
                    continue
                start, end = wall.axis.points[0], wall.axis.points[-1]
                ux, uy, length, _ = _line_direction(start, end)
                mx, my = _midpoint(start, end)
                load_place = w.local_placement(storey_place, mx, my, wall.top_elevation, refdir=(ux, uy, 0))
                load_shape = w.rect_swept_shape(context, max(length, 0.2), 0.12, 0.12)
                load_el = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string('LOAD-' + wall.panel_code)},'Analysis lateral pressure load line generated by PitGuard V2.0.7',$,#{load_place},#{load_shape},$,$)")
                product_ids.append(load_el)
                concrete_product_ids.append(load_el)
                res = wall.design_results
                w.property_set(owner, load_el, "Pset_AnalysisLateralLoad", {
                    "HostWall": wall.panel_code,
                    "SegmentId": wall.segment_id,
                    "LineLength_m": round(length, 3),
                    "TopElevation": wall.top_elevation,
                    "BottomElevation": wall.bottom_elevation,
                    "MaxMoment_kNm_per_m": res.max_moment if res else None,
                    "MaxShear_kN_per_m": res.max_shear if res else None,
                    "MaxDisplacement_mm": res.max_displacement if res else None,
                    "AnalysisRole": "lateral_pressure_resultant_and_wall_beam_axis",
                })
            for idx, case in enumerate(project.calculation_cases, start=1):
                for sidx, stage in enumerate(case.stages, start=1):
                    stage_place = w.local_placement(storey_place, 0, 0, stage.excavation_elevation, refdir=(1, 0, 0))
                    stage_shape = w.rect_swept_shape(context, 0.4, 0.4, 0.4)
                    stage_el = w.entity(f"IFCBUILDINGELEMENTPROXY({_step_string(_guid())},#{owner},{_step_string('STAGE-' + str(idx) + '-' + str(sidx))},'Analysis construction stage data generated by PitGuard V2.0.7',$,#{stage_place},#{stage_shape},$,$)")
                    product_ids.append(stage_el)
                    w.property_set(owner, stage_el, "Pset_AnalysisConstructionStage", {
                        "CaseName": case.name,
                        "StageName": stage.name,
                        "StageType": stage.stage_type,
                        "ExcavationElevation": stage.excavation_elevation,
                        "ActiveSupportCount": len(stage.active_support_ids),
                        "ActiveSupportIds": ';'.join(stage.active_support_ids[:40]),
                        "DeactivatedSupportIds": ';'.join(stage.deactivated_support_ids[:40]),
                        "GroundwaterInside": stage.groundwater_level_inside,
                        "GroundwaterOutside": stage.groundwater_level_outside,
                        "Surcharge_kPa": stage.surcharge,
                        "AnalysisRole": "construction_stage_activation_record",
                    })

    product_ids = [pid for pid in product_ids if pid is not None]
    concrete_product_ids = [pid for pid in concrete_product_ids if pid is not None]
    steel_product_ids = [pid for pid in steel_product_ids if pid is not None]
    rebar_product_ids = [pid for pid in rebar_product_ids if pid is not None]
    if product_ids:
        w.entity(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_step_string(_guid())},#{owner},'PitGuard model containment',$,(" + ",".join(f"#{pid}" for pid in product_ids) + f"),#{storey})")
    if concrete_product_ids:
        w.entity(f"IFCRELASSOCIATESMATERIAL({_step_string(_guid())},#{owner},'Concrete material association',$,(" + ",".join(f"#{pid}" for pid in concrete_product_ids) + f"),#{material_concrete})")
    if steel_product_ids:
        w.entity(f"IFCRELASSOCIATESMATERIAL({_step_string(_guid())},#{owner},'Steel material association',$,(" + ",".join(f"#{pid}" for pid in steel_product_ids) + f"),#{material_steel})")
    if rebar_product_ids:
        w.entity(f"IFCRELASSOCIATESMATERIAL({_step_string(_guid())},#{owner},'Rebar material association',$,(" + ",".join(f"#{pid}" for pid in rebar_product_ids) + f"),#{material_rebar})")

    header = [
        "ISO-10303-21;",
        "HEADER;",
        f"FILE_DESCRIPTION(('PitGuard BIM Designer {mode} IFC4 export with viewer-safe STEP text, swept solids and design property sets'),'2;1');",
        f"FILE_NAME({_step_string(path.name)},{_step_string(timestamp)},('PitGuard'),('PitGuard'), {_step_string('PitGuard BIM Designer ' + SOFTWARE_VERSION)}, {_step_string('PitGuard IFC exporter ' + SOFTWARE_VERSION)}, '');",
        "FILE_SCHEMA(('IFC4'));",
        "ENDSEC;",
        "DATA;",
    ]
    footer = ["ENDSEC;", "END-ISO-10303-21;"]
    path.write_text("\n".join(header + w.lines + footer), encoding="utf-8")
    object_manifest.update({
        "ifcFile": path.name,
        "entityCount": len(w.lines),
        "calculationWallCount": len(object_manifest["calculationWalls"]),
        "constructionPanelCount": len(object_manifest["constructionPanels"]),
        "constructionJointCount": len(object_manifest["constructionJoints"]),
        "rebarCageGroupCount": len(object_manifest["rebarCageGroups"]),
        "reinforcementGroupEntityCount": len(object_manifest["reinforcementGroups"]),
        "supportCount": len(object_manifest["supports"]),
    })
    path.with_suffix(".ifc_manifest.json").write_text(
        __import__("json").dumps(object_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
