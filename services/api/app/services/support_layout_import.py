from __future__ import annotations

import csv
import io
import math
from typing import Any

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import MaterialDefinition, Point2D, Project, SectionDefinition, SupportElement
from app.services.support_layout import (
    _assign_tributary_widths,
    _line_segment_samples_inside,
    _nearest_face_hit,
    make_column_elements,
    make_support_wale_nodes,
    wale_support_bay_audit,
)

_REQUIRED = ("levelIndex", "elevationM", "startX", "startY", "endX", "endY")


def _f(row: dict[str, str], key: str) -> float:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"missing numeric field {key}")
    return float(value)


def _i(row: dict[str, str], key: str) -> int:
    return int(round(_f(row, key)))


def _role(value: str) -> str:
    candidate = str(value or "manual").strip()
    return candidate if candidate in {"main_strut", "secondary_strut", "corner_diagonal", "ring_strut", "manual"} else "manual"


def _section(row: dict[str, str]) -> tuple[str, SectionDefinition, MaterialDefinition]:
    source_material = str(row.get("sourceMaterial") or row.get("material") or "").strip()
    section_type = str(row.get("sectionType") or "").strip()
    if section_type not in {"rc_rectangular", "steel_pipe", "h_steel"}:
        section_type = "steel_pipe" if "steel" in source_material.lower() else "rc_rectangular"
    if section_type == "steel_pipe":
        diameter = float(row.get("diameterM") or 0.609)
        thickness = float(row.get("wallThicknessM") or 0.016)
        section = SectionDefinition(diameter=diameter, wallThickness=thickness, name=str(row.get("section") or "Imported steel support placeholder"))
        material = MaterialDefinition(name="Structural steel", grade=str(row.get("grade") or "Q355"), elasticModulus=2.06e8)
    elif section_type == "h_steel":
        section = SectionDefinition(
            width=float(row.get("widthM") or 0.4),
            height=float(row.get("heightM") or 0.4),
            wallThickness=float(row.get("wallThicknessM") or 0.02),
            name=str(row.get("section") or "Imported H-section placeholder"),
        )
        material = MaterialDefinition(name="Structural steel", grade=str(row.get("grade") or "Q355"), elasticModulus=2.06e8)
    else:
        width = float(row.get("widthM") or 1.0)
        height = float(row.get("heightM") or width)
        section = SectionDefinition(width=width, height=height, name=str(row.get("section") or f"{width:.2f}x{height:.2f} imported RC placeholder"))
        material = MaterialDefinition(name="Concrete", grade=str(row.get("grade") or "C35"), elasticModulus=3.15e7)
    return section_type, section, material


def import_support_layout_csv(project: Project, payload: bytes | str, *, replace: bool = True) -> dict[str, Any]:
    """Import an engineer/reference support layout without silently redesigning it.

    The CSV is geometry-first.  When the source contains only PLAXIS anchor EA
    values and no constructible section, the imported section remains a clearly
    labelled placeholder and must pass the normal member-design stage before
    drawings or formal issue.
    """
    if project.excavation is None:
        raise ValueError("project has no excavation")
    if project.retaining_system is None or not project.retaining_system.diaphragm_walls:
        raise ValueError("generate/import retaining walls before support layout")
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else str(payload)
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    missing = [name for name in _REQUIRED if name not in fields]
    if missing:
        raise ValueError(f"support CSV missing columns: {', '.join(missing)}")

    points = list(project.excavation.outline.points)
    supports: list[SupportElement] = []
    errors: list[str] = []
    for index, row in enumerate(reader, start=2):
        try:
            level = _i(row, "levelIndex")
            elevation = _f(row, "elevationM")
            start_wall = Point2D(x=_f(row, "startX"), y=_f(row, "startY"))
            end_wall = Point2D(x=_f(row, "endX"), y=_f(row, "endY"))
            wall_to_wall_length = math.hypot(end_wall.x - start_wall.x, end_wall.y - start_wall.y)
            if wall_to_wall_length < 0.5:
                raise ValueError("support length below 0.5 m")
            if not _line_segment_samples_inside(start_wall, end_wall, points):
                raise ValueError("support centerline leaves excavation polygon")
            s_hit = _nearest_face_hit(start_wall, project.excavation, tolerance=1.35)
            e_hit = _nearest_face_hit(end_wall, project.excavation, tolerance=1.35)
            if s_hit is None or e_hit is None:
                raise ValueError("both imported endpoints must connect to retaining-wall faces")
            clearance = max(0.0, float(getattr(project.design_settings, "support_wall_clearance_m", 1.0) or 0.0))
            if wall_to_wall_length <= 2.0 * clearance + 0.5:
                raise ValueError("support is too short after wall/waIe centre-line clearance")
            ux = (end_wall.x - start_wall.x) / wall_to_wall_length
            uy = (end_wall.y - start_wall.y) / wall_to_wall_length
            start = Point2D(x=start_wall.x + ux * clearance, y=start_wall.y + uy * clearance)
            end = Point2D(x=end_wall.x - ux * clearance, y=end_wall.y - uy * clearance)
            length = math.hypot(end.x - start.x, end.y - start.y)
            section_type, section, material = _section(row)
            support = SupportElement(
                code=str(row.get("code") or f"IMP-L{level}-{len(supports)+1:03d}"),
                levelIndex=level,
                elevation=elevation,
                start=start,
                end=end,
                supportRole=_role(str(row.get("supportRole") or "manual")),
                layoutNote=str(row.get("layoutNote") or "Imported engineer/reference support geometry; section requires project design verification."),
                spanLength=length,
                startFaceCode=s_hit.face_code,
                endFaceCode=e_hit.face_code,
                startWallConnection=start_wall,
                endWallConnection=end_wall,
                startWallClearanceM=clearance,
                endWallClearanceM=clearance,
                centerlineOffsetM=clearance,
                sectionType=section_type,
                section=section,
                material=material,
                topologyFamily="manual",
                designZone=str(row.get("designZone") or "reference_imported"),
                placementReason=str(row.get("placementReason") or "导入设计院/PLAXIS参考支撑"),
                loadPathClass="wall_to_wall",
                professionalReviewRequired=True,
                optimizationLocked=True,
                optimizationLockReason="imported reference support geometry",
            )
            supports.append(support)
        except Exception as exc:  # report all malformed rows in one response
            errors.append(f"row {index}: {exc}")
    if errors:
        raise ValueError("; ".join(errors[:20]))
    if not supports:
        raise ValueError("support CSV contains no support rows")

    system = project.retaining_system
    if replace:
        system.supports = supports
    else:
        existing_codes = {item.code for item in system.supports}
        system.supports.extend(item for item in supports if item.code not in existing_codes)
    _assign_tributary_widths(system.supports, project.excavation)
    system.columns = make_column_elements(
        project.excavation,
        system.supports,
        max_unbraced_span_m=float(getattr(project.design_settings, "column_max_unbraced_span_m", 18.0) or 18.0),
    )
    system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)
    system.layout_summary = {
        **(system.layout_summary or {}),
        "supportLayoutSource": "imported_reference_csv",
        "importedSupportCount": len(supports),
        "supportReferenceRequiresSectionDesign": True,
        "candidateSchemes": [],
        "selectedCandidateId": "reference-imported",
    }
    project.retaining_system = system
    quality = evaluate_support_layout_quality(project)
    wale = wale_support_bay_audit(
        project.excavation,
        system.supports,
        target_bay_m=float(getattr(project.design_settings, "max_wale_support_bay_m", 6.0) or 6.0),
        hard_max_bay_m=float(getattr(project.design_settings, "hard_max_wale_support_bay_m", 9.0) or 9.0),
    )
    return {
        "supportCount": len(system.supports),
        "columnCount": len(system.columns),
        "supportNodeCount": len(system.support_nodes),
        "quality": quality.model_dump(by_alias=True),
        "waleSupportBayAudit": wale,
        "source": "imported_reference_csv",
        "sectionStatus": "placeholder_requires_member_design",
    }
