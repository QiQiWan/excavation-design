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

    positions = sorted({0.0, length, *[round(node.chainage, 6) for node in support_nodes]})
    if len(positions) < 2:
        return WaleBeamAnalysis([], None)
    pos_index = {x: idx for idx, x in enumerate(positions)}
    n_dof = 2 * len(positions)
    k_global = np.zeros((n_dof, n_dof), dtype=float)
    f_global = np.zeros(n_dof, dtype=float)
    wale = _find_wale_for_face(wale_beams, support_nodes[0].support.level_index, face_code)
    ei = _wale_ei(wale)
    element_records: list[tuple[int, int, float, np.ndarray, np.ndarray]] = []

    for i in range(len(positions) - 1):
        le = max(positions[i + 1] - positions[i], 1e-6)
        ke = _beam_element_stiffness(ei, le)
        fe = _equivalent_nodal_load(pressure_line_load, le)
        dofs = [2 * i, 2 * i + 1, 2 * (i + 1), 2 * (i + 1) + 1]
        element_records.append((i, i + 1, le, ke, fe))
        for a in range(4):
            f_global[dofs[a]] += fe[a]
            for b in range(4):
                k_global[dofs[a], dofs[b]] += ke[a, b]

    springs_by_pos: dict[float, float] = {}
    for node in support_nodes:
        x = round(node.chainage, 6)
        springs_by_pos[x] = springs_by_pos.get(x, 0.0) + node.stiffness
        k_global[2 * pos_index[x], 2 * pos_index[x]] += node.stiffness

    avg_support_k = sum(node.stiffness for node in support_nodes) / len(support_nodes)
    end_k = max(MIN_SPRING_KN_M * 0.25, avg_support_k * END_SUPPORT_STIFFNESS_RATIO)
    for x in (0.0, length):
        k_global[2 * pos_index[x], 2 * pos_index[x]] += end_k

    diag_scale = max(float(np.max(np.diag(k_global))), 1.0)
    for idx in range(len(positions)):
        k_global[2 * idx + 1, 2 * idx + 1] += diag_scale * ROTATIONAL_REGULARIZATION

    try:
        displacement = np.linalg.solve(k_global, f_global)
    except np.linalg.LinAlgError:
        reactions = _fallback_reactions(pressure_line_load, length, support_nodes, face_code)
        return WaleBeamAnalysis(reactions, _fallback_internal_force(pressure_line_load, length, support_nodes, face_code, stage_id))

    reactions: list[WaleBeamReaction] = []
    for node in support_nodes:
        x = round(node.chainage, 6)
        w = float(displacement[2 * pos_index[x]])
        normal_reaction_at_shared_node = max(0.0, node.stiffness * w)
        total_spring_at_x = max(springs_by_pos.get(x, node.stiffness), EPS)
        normal_reaction = normal_reaction_at_shared_node * node.stiffness / total_spring_at_x
        axial = normal_reaction / max(node.normal_projection, 0.20)
        width = node.support.start_tributary_width if node.endpoint == "start" else node.support.end_tributary_width
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
                beam_node_count=len(positions),
                tributary_width=round(width, 3) if width else None,
                wale_beam_code=node.wale_beam_code,
                method="continuous_wale_beam_elastic_supports",
                note="围檩按连续梁离散，墙面压力作为均布线荷载，支撑端部按 EA/L 和法向投影作为弹性支座分配节点反力。",
            )
        )

    node_shear: dict[float, list[float]] = {p: [] for p in positions}
    node_moment: dict[float, list[float]] = {p: [] for p in positions}
    max_abs_shear = 0.0
    max_abs_moment = 0.0
    for i, j, le, ke, fe in element_records:
        dofs = [2 * i, 2 * i + 1, 2 * j, 2 * j + 1]
        ue = displacement[dofs]
        end_forces = ke @ ue - fe
        # end_forces = [V_i, M_i, V_j, M_j] in local sign convention.
        vi, mi, vj, mj = [float(x) for x in end_forces]
        xi, xj = positions[i], positions[j]
        node_shear[xi].append(vi)
        node_shear[xj].append(-vj)
        node_moment[xi].append(mi)
        node_moment[xj].append(mj)
        max_abs_shear = max(max_abs_shear, abs(vi), abs(vj))
        max_abs_moment = max(max_abs_moment, abs(mi), abs(mj), abs(pressure_line_load * le * le / 8.0 + (mi + mj) / 2.0))

    # Store both support/end nodes and intra-span samples so downstream reports
    # can draw realistic bending/shear/deflection diagrams instead of only node
    # values.  Internal force interpolation is an engineering screening diagram
    # derived from beam-end forces and uniform load; detailed sign convention is
    # preserved by reporting positive/negative envelopes separately later.
    sampled: dict[float, WaleBeamInternalForcePoint] = {}
    def add_sample(x: float, shear: float, moment: float, deflection: float) -> None:
        key = round(max(0.0, min(length, x)), 3)
        old = sampled.get(key)
        if old is None or abs(moment) + abs(shear) > abs(old.moment) + abs(old.shear):
            sampled[key] = WaleBeamInternalForcePoint(
                chainage=key, shear=round(shear, 3), moment=round(moment, 3), deflection=round(deflection, 6)
            )

    for pos in positions:
        idx = pos_index[pos]
        shear = sum(node_shear[pos]) / max(len(node_shear[pos]), 1)
        moment = sum(node_moment[pos]) / max(len(node_moment[pos]), 1)
        add_sample(pos, shear, moment, float(displacement[2 * idx]))

    for i, j, le, ke, fe in element_records:
        dofs = [2 * i, 2 * i + 1, 2 * j, 2 * j + 1]
        ue = displacement[dofs]
        end_forces = ke @ ue - fe
        vi, mi, _vj, _mj = [float(x) for x in end_forces]
        xi = positions[i]
        for r in (0.25, 0.5, 0.75):
            x_local = le * r
            # Approximate section actions under uniform load q with left-end
            # forces. This is sufficient for envelope/plotting at preliminary
            # design level; exact FE recovery remains a later production item.
            shear = vi - pressure_line_load * x_local
            moment = mi + vi * x_local - 0.5 * pressure_line_load * x_local * x_local
            # Cubic interpolation of transverse displacement.
            n1 = 1 - 3*r*r + 2*r*r*r
            n2 = le * (r - 2*r*r + r*r*r)
            n3 = 3*r*r - 2*r*r*r
            n4 = le * (-r*r + r*r*r)
            deflection = n1*ue[0] + n2*ue[1] + n3*ue[2] + n4*ue[3]
            add_sample(xi + x_local, shear, moment, float(deflection))

    points = [sampled[k] for k in sorted(sampled)]
    max_defl = max((abs(p.deflection) for p in points), default=0.0)
    max_abs_shear = max(max_abs_shear, *(abs(p.shear) for p in points))
    max_abs_moment = max(max_abs_moment, *(abs(p.moment) for p in points))
    internal = WaleBeamInternalForceResult(
        wale_beam_code=getattr(wale, "code", None) or f"WB-L{support_nodes[0].support.level_index}-{face_code}",
        face_code=face_code,
        level_index=support_nodes[0].support.level_index,
        elevation=support_nodes[0].support.elevation,
        stage_id=stage_id,
        pressure_line_load=round(pressure_line_load, 3),
        beam_length=round(length, 3),
        support_node_count=len(support_nodes),
        points=points,
        max_moment=round(max_abs_moment, 3),
        max_shear=round(max_abs_shear, 3),
        max_deflection=round(max_defl, 6),
        method="continuous Euler-Bernoulli wale beam; wall pressure line load; elastic strut node springs; end continuity springs",
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
