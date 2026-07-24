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

    def rect_swept_shape(self, context_id: int, length: float, width: float, height: float) -> int:
        length = max(float(length), 0.001)
        width = max(float(width), 0.001)
        height = max(float(height), 0.001)
        pos2d = self.axis2_2d(0, 0)
        profile = self.entity(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{pos2d},{_real(length)},{_real(width)})")
        swept_pos = self.axis2_3d(0, 0, 0)
        extrusion_dir = self.direction(0, 0, 1)
        solid = self.entity(f"IFCEXTRUDEDAREASOLID(#{profile},#{swept_pos},#{extrusion_dir},{_real(height)})")
        rep = self.entity(f"IFCSHAPEREPRESENTATION(#{context_id},'Body','SweptSolid',(#{solid}))")
        return self.entity(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

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
    # IFC viewers are much more reliable when vertical bars are exported as
    # true vertical swept solids and horizontal bars as long local-X solids.
    if vertical:
        placement = w.local_placement(storey_place, x, y, z, refdir=(1, 0, 0), axis=(0, 0, 1))
        shape = w.rect_swept_shape(context, diameter_m, diameter_m, max(length, diameter_m))
    else:
        placement = w.local_placement(storey_place, x, y, z, refdir=refdir, axis=(0, 0, 1))
        shape = w.rect_swept_shape(context, max(length, diameter_m), diameter_m, diameter_m)
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

    product_ids: list[int] = []
    concrete_product_ids: list[int] = []
    steel_product_ids: list[int] = []
    rebar_product_ids: list[int] = []

    support_quality = evaluate_support_layout_quality(project) if project.retaining_system else None
    support_metrics = dict(support_quality.metrics or {}) if support_quality else {}
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
                for idx, group in enumerate(support.reinforcement[:2], start=1):
                    offset = (idx - 1.5) * max(width / 3.0, 0.10)
                    bar = _add_reinforcing_bar(
                        w,
                        owner=owner,
                        context=context,
                        storey_place=storey_place,
                        name=f"RB-{support.code}-{idx}",
                        description=f"Representative support reinforcement group: {group.name}",
                        x=mx - uy * offset,
                        y=my + ux * offset,
                        z=support.elevation - height / 2.0,
                        refdir=(ux, uy, 0),
                        length=length,
                        diameter_mm=group.diameter,
                        grade=group.grade,
                        properties={
                            "HostElement": support.code,
                            "GroupName": group.name,
                            "BarType": group.bar_type,
                            "Diameter_mm": group.diameter,
                            "Spacing_mm": group.spacing,
                            "Count": group.count,
                            "LocationDescription": group.location_description,
                            "RepresentationLevel": "representative_group_bar",
                        },
                        as_proxy=construction_visual_mode,
                    )
                    product_ids.append(bar)
                    rebar_product_ids.append(bar)
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
