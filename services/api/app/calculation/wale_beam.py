from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.schemas.domain import Point2D, SupportElement, WaleBeamEnvelopePoint, WaleBeamEnvelopeResult, WaleBeamInternalForcePoint, WaleBeamInternalForceResult

EPS = 1e-9
E_CONCRETE_KN_M2 = {
    "C30": 30_000_000.0,
    "C35": 31_500_000.0,
    "C40": 32_500_000.0,
    "C45": 33_500_000.0,
    "C50": 34_500_000.0,
}
E_STEEL_KN_M2 = 206_000_000.0
WALE_EI_DEFAULT_KNM2 = 8.0e5
MIN_SPRING_KN_M = 8.0e3
MAX_SPRING_KN_M = 1.5e6
END_SUPPORT_STIFFNESS_RATIO = 0.04
ROTATIONAL_REGULARIZATION = 1.0e-3


@dataclass
class WaleSupportNode:
    support: SupportElement
    endpoint: str
    chainage: float
    stiffness: float
    normal_projection: float
    wale_beam_code: str | None = None


@dataclass
class WaleBeamReaction:
    support_id: str
    endpoint: str
    face_code: str
    chainage: float
    reaction: float
    axial_force: float
    stiffness: float
    normal_projection: float
    beam_node_count: int
    tributary_width: float | None
    wale_beam_code: str | None
    method: str
    note: str


@dataclass
class WaleBeamAnalysis:
    reactions: list[WaleBeamReaction]
    internal_force: WaleBeamInternalForceResult | None


def distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def project_chainage(point: Point2D, a: Point2D, b: Point2D) -> tuple[float, float]:
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length <= EPS:
        return 0.0, distance(point, a)
    t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / (length * length)
    t = max(0.0, min(1.0, t))
    proj = Point2D(x=a.x + t * dx, y=a.y + t * dy)
    return t * length, distance(point, proj)


def _support_area(support: SupportElement) -> float:
    section = support.section
    if support.section_type == "steel_pipe" and section.diameter and section.wall_thickness:
        do = section.diameter
        di = max(0.0, do - 2.0 * section.wall_thickness)
        return math.pi * (do * do - di * di) / 4.0
    if section.diameter:
        return math.pi * section.diameter * section.diameter / 4.0
    return max(float(section.width or 0.8), 0.2) * max(float(section.height or 0.8), 0.2)


def support_elastic_modulus(support: SupportElement) -> float:
    if support.material.name.lower().startswith("steel") or support.material.grade.upper().startswith("Q"):
        return E_STEEL_KN_M2
    return E_CONCRETE_KN_M2.get(support.material.grade, E_CONCRETE_KN_M2["C35"])


def support_axial_area(support: SupportElement) -> float:
    return _support_area(support)


def _role_stiffness_factor(support: SupportElement) -> float:
    return {"main_strut": 1.0, "secondary_strut": 1.0, "ring_strut": 0.8, "corner_diagonal": 0.55, "manual": 1.0}.get(getattr(support, "support_role", "main_strut"), 1.0)


def _normal_projection_factor(support: SupportElement, segment) -> float:
    sx, sy = support.end.x - support.start.x, support.end.y - support.start.y
    sl = math.hypot(sx, sy)
    tx, ty = segment.end.x - segment.start.x, segment.end.y - segment.start.y
    tl = math.hypot(tx, ty)
    if sl <= EPS or tl <= EPS:
        return 1.0
    nx, ny = -ty / tl, tx / tl
    return max(0.20, min(1.0, abs((sx / sl) * nx + (sy / sl) * ny)))


def support_spring_stiffness(support: SupportElement, segment) -> tuple[float, float]:
    length = max(float(support.span_length or distance(support.start, support.end)), 1.0)
    e = support_elastic_modulus(support)
    area = _support_area(support)
    normal_projection = _normal_projection_factor(support, segment)
    k = e * area / length * normal_projection * normal_projection * _role_stiffness_factor(support)
    return max(MIN_SPRING_KN_M, min(MAX_SPRING_KN_M, k)), normal_projection


def _endpoint_for_face(support: SupportElement, face_code: str) -> tuple[str, Point2D, float | None] | None:
    if support.start_face_code == face_code:
        return "start", support.start_wall_connection or support.start, support.start_tributary_width
    if support.end_face_code == face_code:
        return "end", support.end_wall_connection or support.end, support.end_tributary_width
    return None


def _wale_ei(wale_beam: Any | None) -> float:
    if not wale_beam:
        return WALE_EI_DEFAULT_KNM2
    section = getattr(wale_beam, "section", None)
    material = getattr(wale_beam, "material", None)
    width = float(getattr(section, "width", None) or 0.9)
    height = float(getattr(section, "height", None) or 0.7)
    grade = str(getattr(material, "grade", "C35"))
    e = E_CONCRETE_KN_M2.get(grade, E_CONCRETE_KN_M2["C35"])
    i = width * height ** 3 / 12.0
    return max(1.0e5, e * i)


def _equivalent_nodal_load(q: float, le: float) -> np.ndarray:
    return np.array([q * le / 2.0, q * le * le / 12.0, q * le / 2.0, -q * le * le / 12.0], dtype=float)


def _beam_element_stiffness(ei: float, le: float) -> np.ndarray:
    fac = ei / (le ** 3)
    return fac * np.array(
        [
            [12.0, 6.0 * le, -12.0, 6.0 * le],
            [6.0 * le, 4.0 * le * le, -6.0 * le, 2.0 * le * le],
            [-12.0, -6.0 * le, 12.0, -6.0 * le],
            [6.0 * le, 2.0 * le * le, -6.0 * le, 4.0 * le * le],
        ],
        dtype=float,
    )


def _find_wale_for_face(wale_beams: list[Any] | None, level_index: int, face_code: str) -> Any | None:
    for beam in wale_beams or []:
        if getattr(beam, "support_level", None) == level_index and str(getattr(beam, "code", "")).endswith(f"-{face_code}"):
            return beam
    return None


def analyze_wale_continuous_beam(
    *,
    pressure_line_load: float,
    segment,
    supports: list[SupportElement],
    face_code: str,
    wale_beams: list[Any] | None = None,
    stage_id: str | None = None,
) -> WaleBeamAnalysis:
    """Return a stable multi-bay wale design envelope and support reactions.

    V3.14 separates two tasks that were previously mixed in one spring-beam
    solve.  The project-level global coupled matrix remains responsible for
    deformation compatibility.  Wale member design uses a statically balanced,
    bay-by-bay envelope based on the actual direct support stations:

    * support reactions are q times the adjacent tributary wall length and are
      split by spring stiffness only when several members share one node;
    * interior spans use the simply-supported positive envelope plus a
      conservative continuous-support negative moment q(L1^2+L2^2)/12;
    * end bays use the closed-perimeter rigid corner joint as an analysis support;
    * deflection is evaluated with closed-form Euler-Bernoulli expressions.

    This prevents an under-restrained global translation mode from creating
    fictitious hundreds-of-thousands of kN*m wale moments while preserving a
    hard failure whenever the actual support bay itself is excessive.
    """
    length = float(getattr(segment, "length", 0.0) or distance(segment.start, segment.end))
    if length <= EPS or pressure_line_load <= 0.0 or not supports:
        return WaleBeamAnalysis([], None)

    support_nodes: list[WaleSupportNode] = []
    for support in supports:
        endpoint = _endpoint_for_face(support, face_code)
        if not endpoint:
            continue
        endpoint_name, point, _width = endpoint
        chainage, dist = project_chainage(point, segment.start, segment.end)
        if dist > 1.25:
            continue
        k, normal_projection = support_spring_stiffness(support, segment)
        wale = _find_wale_for_face(wale_beams, support.level_index, face_code)
        support_nodes.append(
            WaleSupportNode(
                support=support,
                endpoint=endpoint_name,
                chainage=max(0.0, min(length, chainage)),
                stiffness=k,
                normal_projection=normal_projection,
                wale_beam_code=getattr(wale, "code", None),
            )
        )
    if not support_nodes:
        return WaleBeamAnalysis([], None)

    grouped: dict[float, list[WaleSupportNode]] = {}
    for node in support_nodes:
        grouped.setdefault(round(float(node.chainage), 6), []).append(node)
    support_positions = sorted(grouped)
    wale = _find_wale_for_face(wale_beams, support_nodes[0].support.level_index, face_code)
    ei = _wale_ei(wale)
    q = float(pressure_line_load)

    reactions: list[WaleBeamReaction] = []
    for index, x in enumerate(support_positions):
        previous_x = support_positions[index - 1] if index > 0 else None
        next_x = support_positions[index + 1] if index + 1 < len(support_positions) else None
        left_boundary = 0.0 if previous_x is None else 0.5 * (previous_x + x)
        right_boundary = length if next_x is None else 0.5 * (x + next_x)
        tributary = max(0.0, right_boundary - left_boundary)
        total_reaction = q * tributary
        nodes_here = grouped[x]
        total_k = max(sum(max(node.stiffness, EPS) for node in nodes_here), EPS)
        for node in nodes_here:
            normal_reaction = total_reaction * max(node.stiffness, EPS) / total_k
            axial = normal_reaction / max(node.normal_projection, 0.20)
            stored_width = node.support.start_tributary_width if node.endpoint == "start" else node.support.end_tributary_width
            reactions.append(
                WaleBeamReaction(
                    support_id=node.support.id,
                    endpoint=node.endpoint,
                    face_code=face_code,
                    chainage=round(node.chainage, 3),
                    reaction=round(normal_reaction, 3),
                    axial_force=round(axial, 3),
                    stiffness=round(node.stiffness, 3),
                    normal_projection=round(node.normal_projection, 3),
                    beam_node_count=len(support_positions) + 2,
                    tributary_width=round(stored_width if stored_width is not None else tributary, 3),
                    wale_beam_code=node.wale_beam_code,
                    method="balanced_wale_bay_tributary_reaction",
                    note=(
                        "围檩节点反力按相邻支点中线控制的墙面分担长度积分；同一节点多根支撑按 EA/L "
                        "和法向投影刚度分配，全部节点反力与 qL 保持静力平衡。"
                    ),
                )
            )

    # Each face wale belongs to a closed perimeter ring.  A rigidly detailed
    # corner joint transfers the end-bay action into the adjacent perpendicular
    # wale; it must not be represented as a free cantilever.  Direct-support
    # reactions remain conservatively distributed over the full qL wall load,
    # while member strength/deflection uses the closed-corner joint as an
    # analysis support.  The global coupled model remains the deformation check.
    positions = sorted({0.0, length, *support_positions})
    analysis_support_position_set = {0.0, round(length, 6), *support_positions}
    sampled: dict[float, WaleBeamInternalForcePoint] = {}

    def add_sample(x: float, shear: float, moment: float, deflection: float) -> None:
        key = round(max(0.0, min(length, x)), 3)
        old = sampled.get(key)
        candidate = WaleBeamInternalForcePoint(
            chainage=key,
            shear=round(float(shear), 3),
            moment=round(float(moment), 3),
            deflection=round(float(deflection), 6),
        )
        if old is None or abs(candidate.moment) + abs(candidate.shear) > abs(old.moment) + abs(old.shear):
            sampled[key] = candidate

    max_abs_moment = 0.0
    max_abs_shear = 0.0
    max_abs_deflection = 0.0
    support_position_set = set(support_positions)
    bay_lengths: list[float] = []
    for bay_index, (xa, xb) in enumerate(zip(positions[:-1], positions[1:])):
        L = max(xb - xa, 1.0e-6)
        bay_lengths.append(L)
        left_supported = round(xa, 6) in analysis_support_position_set
        right_supported = round(xb, 6) in analysis_support_position_set
        for ratio in (0.0, 0.25, 0.5, 0.75, 1.0):
            x_local = ratio * L
            x_global = xa + x_local
            if left_supported and right_supported:
                # Simply-supported positive envelope; continuity negative moments
                # are added separately at each interior support below.
                shear = q * (L / 2.0 - x_local)
                moment = q * x_local * (L - x_local) / 2.0
                deflection = q * x_local * (L**3 - 2.0 * L * x_local**2 + x_local**3) / max(24.0 * ei, EPS)
            elif left_supported and not right_supported:
                # Right free overhang, coordinate from fixed support at xa.
                shear = q * (L - x_local)
                moment = -0.5 * q * (L - x_local) ** 2
                deflection = q * x_local**2 * (6.0 * L**2 - 4.0 * L * x_local + x_local**2) / max(24.0 * ei, EPS)
            elif right_supported and not left_supported:
                # Left free overhang, mirror of a cantilever fixed at xb.
                z = L - x_local
                shear = -q * z
                moment = -0.5 * q * z**2
                deflection = q * z**2 * (6.0 * L**2 - 4.0 * L * z + z**2) / max(24.0 * ei, EPS)
            else:
                # This can only occur when no direct support exists; retain a
                # large screening envelope so the hard wale-bay gate blocks it.
                shear = q * (L / 2.0 - x_local)
                moment = q * x_local * (L - x_local) / 2.0
                deflection = 5.0 * q * L**4 / max(384.0 * ei, EPS)
            add_sample(x_global, shear, moment, deflection)
            max_abs_moment = max(max_abs_moment, abs(moment))
            max_abs_shear = max(max_abs_shear, abs(shear))
            max_abs_deflection = max(max_abs_deflection, abs(deflection))

    # Conservative hogging envelope at direct supports.  For a support between
    # two bays this expression is at least as severe as the equal-span qL^2/12
    # coefficient and remains transparent in the calculation report.
    for index, x in enumerate(support_positions):
        left = x - (positions[positions.index(x) - 1] if positions.index(x) > 0 else x)
        pos_idx = positions.index(x)
        right = (positions[pos_idx + 1] - x) if pos_idx + 1 < len(positions) else 0.0
        if left > EPS and right > EPS:
            negative_moment = -q * (left * left + right * right) / 12.0
            add_sample(x, 0.0, negative_moment, 0.0)
            max_abs_moment = max(max_abs_moment, abs(negative_moment))

    points = [sampled[key] for key in sorted(sampled)]
    warnings: list[str] = []
    max_bay = max(bay_lengths, default=length)
    if max_bay > 9.0:
        warnings.append(
            f"墙面 {face_code} 第 {support_nodes[0].support.level_index} 道围檩存在 {max_bay:.2f}m 支点间距；"
            "截面设计结果必须与支撑拓扑硬门禁联合使用。"
        )
    internal = WaleBeamInternalForceResult(
        wale_beam_code=getattr(wale, "code", None) or f"WB-L{support_nodes[0].support.level_index}-{face_code}",
        face_code=face_code,
        level_index=support_nodes[0].support.level_index,
        elevation=support_nodes[0].support.elevation,
        stage_id=stage_id,
        pressure_line_load=round(q, 3),
        beam_length=round(length, 3),
        support_node_count=len(support_nodes),
        points=points,
        max_moment=round(max_abs_moment, 3),
        max_shear=round(max_abs_shear, 3),
        max_deflection=round(max_abs_deflection, 6),
        method=(
            "V3.14 statically balanced closed-perimeter multi-bay wale envelope: direct support stations, "
            "rigid corner-joint end restraint, conservative interior hogging, and closed-form deflection"
        ),
        warnings=warnings,
    )
    return WaleBeamAnalysis(reactions, internal)

def solve_wale_continuous_beam_reactions(
    *,
    pressure_line_load: float,
    segment,
    supports: list[SupportElement],
    face_code: str,
    wale_beams: list[Any] | None = None,
) -> list[WaleBeamReaction]:
    return analyze_wale_continuous_beam(
        pressure_line_load=pressure_line_load,
        segment=segment,
        supports=supports,
        face_code=face_code,
        wale_beams=wale_beams,
    ).reactions


def _fallback_internal_force(
    pressure_line_load: float,
    length: float,
    support_nodes: list[WaleSupportNode],
    face_code: str,
    stage_id: str | None,
) -> WaleBeamInternalForceResult:
    max_m = pressure_line_load * length * length / 8.0
    max_v = pressure_line_load * length / 2.0
    level = support_nodes[0].support.level_index if support_nodes else 0
    elevation = support_nodes[0].support.elevation if support_nodes else 0.0
    return WaleBeamInternalForceResult(
        wale_beam_code=support_nodes[0].wale_beam_code if support_nodes else f"WB-L{level}-{face_code}",
        face_code=face_code,
        level_index=level,
        elevation=elevation,
        stage_id=stage_id,
        pressure_line_load=round(pressure_line_load, 3),
        beam_length=round(length, 3),
        support_node_count=len(support_nodes),
        points=[
            WaleBeamInternalForcePoint(chainage=0.0, shear=round(max_v, 3), moment=0.0, deflection=0.0),
            WaleBeamInternalForcePoint(chainage=round(length / 2.0, 3), shear=0.0, moment=round(max_m, 3), deflection=0.0),
            WaleBeamInternalForcePoint(chainage=round(length, 3), shear=round(-max_v, 3), moment=0.0, deflection=0.0),
        ],
        max_moment=round(max_m, 3),
        max_shear=round(max_v, 3),
        max_deflection=0.0,
        method="fallback simply-supported equivalent wale beam envelope",
        warnings=["连续梁求解条件不足，围檩内力采用简支等效包络作为保守占位。"],
    )


def _fallback_reactions(pressure_line_load: float, length: float, support_nodes: list[WaleSupportNode], face_code: str) -> list[WaleBeamReaction]:
    total_k = sum(node.stiffness for node in support_nodes) or 1.0
    total_load = pressure_line_load * length
    result: list[WaleBeamReaction] = []
    for node in support_nodes:
        normal = total_load * node.stiffness / total_k
        axial = normal / max(node.normal_projection, 0.20)
        width = node.support.start_tributary_width if node.endpoint == "start" else node.support.end_tributary_width
        result.append(
            WaleBeamReaction(
                support_id=node.support.id,
                endpoint=node.endpoint,
                face_code=face_code,
                chainage=round(node.chainage, 3),
                reaction=round(normal, 3),
                axial_force=round(axial, 3),
                stiffness=round(node.stiffness, 3),
                normal_projection=round(node.normal_projection, 3),
                beam_node_count=len(support_nodes) + 2,
                tributary_width=round(width, 3) if width else None,
                wale_beam_code=node.wale_beam_code,
                method="continuous_wale_beam_fallback_stiffness_distribution",
                note="连续梁矩阵求解条件不足，退化为按支撑弹性刚度比例分配墙面总线荷载。",
            )
        )
    return result


def build_wale_beam_envelope(wale_beam_code: str, results: list[WaleBeamInternalForceResult]) -> WaleBeamEnvelopeResult | None:
    """Build multi-stage envelope data for one wale beam.

    Results may come from different excavation stages. Chainages are rounded to
    0.1 m buckets so tables and front-end diagrams remain compact and stable.
    """
    if not results:
        return None
    buckets: dict[float, dict[str, float]] = {}
    stages: set[str] = set()
    for result in results:
        if result.stage_id:
            stages.add(result.stage_id)
        for point in result.points:
            x = round(point.chainage, 1)
            item = buckets.setdefault(x, {"max_pos_m": 0.0, "max_neg_m": 0.0, "max_v": 0.0, "max_d": 0.0})
            item["max_pos_m"] = max(item["max_pos_m"], point.moment)
            item["max_neg_m"] = min(item["max_neg_m"], point.moment)
            item["max_v"] = max(item["max_v"], abs(point.shear))
            item["max_d"] = max(item["max_d"], abs(point.deflection))
    points = [
        WaleBeamEnvelopePoint(
            chainage=x,
            max_positive_moment=round(v["max_pos_m"], 3),
            max_negative_moment=round(v["max_neg_m"], 3),
            max_abs_shear=round(v["max_v"], 3),
            max_abs_deflection=round(v["max_d"], 6),
        )
        for x, v in sorted(buckets.items())
    ]
    return WaleBeamEnvelopeResult(
        wale_beam_code=wale_beam_code,
        level_index=results[0].level_index,
        face_code=results[0].face_code,
        governing_stage_ids=sorted(stages),
        points=points,
        max_positive_moment=max((p.max_positive_moment for p in points), default=0.0),
        max_negative_moment=min((p.max_negative_moment for p in points), default=0.0),
        max_abs_shear=max((p.max_abs_shear for p in points), default=0.0),
        max_abs_deflection=max((p.max_abs_deflection for p in points), default=0.0),
    )
