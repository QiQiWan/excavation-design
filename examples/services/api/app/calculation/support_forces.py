from __future__ import annotations

from collections import defaultdict

from app.schemas.domain import PressureProfile, SupportElement, SupportForceResult
from app.calculation.wale_beam import analyze_wale_continuous_beam
from app.rules.gb50009.load_combination_rules import design_effect_standard_to_uls, importance_factor


def _pressure_at_depth(profile: PressureProfile, depth: float) -> float:
    points = sorted(profile.points, key=lambda p: p.depth)
    if not points:
        return 0.0
    if depth <= points[0].depth:
        return points[0].total_pressure
    if depth >= points[-1].depth:
        return points[-1].total_pressure
    for a, b in zip(points, points[1:]):
        if a.depth <= depth <= b.depth:
            t = (depth - a.depth) / max(b.depth - a.depth, 1e-9)
            return a.total_pressure + t * (b.total_pressure - a.total_pressure)
    return points[-1].total_pressure


def _integrate_pressure(profile: PressureProfile, top_depth: float, bottom_depth: float) -> float:
    if bottom_depth <= top_depth:
        return 0.0
    samples = sorted({top_depth, bottom_depth, *[p.depth for p in profile.points if top_depth < p.depth < bottom_depth]})
    total = 0.0
    for d1, d2 in zip(samples, samples[1:]):
        total += 0.5 * (_pressure_at_depth(profile, d1) + _pressure_at_depth(profile, d2)) * (d2 - d1)
    return total


def _group_supports_by_level(supports: list[SupportElement]) -> list[tuple[float, list[SupportElement]]]:
    groups: dict[float, list[SupportElement]] = defaultdict(list)
    for support in supports:
        groups[round(support.elevation, 3)].append(support)
    return sorted(groups.items(), key=lambda item: item[0], reverse=True)


def _role_factor(support: SupportElement) -> float:
    return {"main_strut": 1.0, "secondary_strut": 1.0, "ring_strut": 0.85, "corner_diagonal": 0.35, "manual": 1.0}.get(getattr(support, "support_role", "main_strut"), 1.0)


def _tributary_width_for_segment(support: SupportElement, segment_name: str | None, fallback_width: float) -> float:
    if not segment_name:
        return fallback_width
    if support.start_face_code == segment_name and support.start_tributary_width:
        return support.start_tributary_width
    if support.end_face_code == segment_name and support.end_tributary_width:
        return support.end_tributary_width
    return fallback_width


def estimate_support_axial_forces(
    pressure_profile: PressureProfile,
    supports: list[SupportElement],
    segment_length: float,
    top_elevation: float,
    bottom_elevation: float,
    safety_grade: str = "二级",
    partial_factor: float = 1.25,
    segment_name: str | None = None,
    segment=None,
    wale_beams: list | None = None,
    stage_id: str | None = None,
    wale_result_collector: list | None = None,
) -> list[SupportForceResult]:
    """Estimate support axial forces through a continuous wale-beam model.

    V1.6 upgrades the V1.5 tributary-width distribution.  Each wall face and
    support level is modelled as a continuous Euler-Bernoulli wale beam under
    uniform wall line load.  Strut endpoints act as elastic springs based on
    axial EA/L and normal projection.  The resulting nodal reaction is converted
    to the support axial force and then to the ULS design effect.  If a segment
    object is unavailable, the function falls back to V1.5 tributary-width logic.
    """
    if not supports:
        return []
    level_groups = _group_supports_by_level(supports)
    support_depths = [top_elevation - elevation for elevation, _ in level_groups]
    excavation_depth = top_elevation - bottom_elevation
    results: list[SupportForceResult] = []
    gamma0 = importance_factor(safety_grade)
    for idx, (elevation, level_supports) in enumerate(level_groups):
        current_depth = support_depths[idx]
        previous_depth = 0.0 if idx == 0 else (support_depths[idx - 1] + current_depth) / 2.0
        next_depth = excavation_depth if idx == len(level_groups) - 1 else (current_depth + support_depths[idx + 1]) / 2.0
        tributary_top = max(0.0, previous_depth)
        tributary_bottom = min(excavation_depth, next_depth)
        line_load = _integrate_pressure(pressure_profile, tributary_top, tributary_bottom)  # kN/m of wall
        if segment is not None and segment_name:
            analysis = analyze_wale_continuous_beam(
                pressure_line_load=line_load,
                segment=segment,
                supports=level_supports,
                face_code=segment_name,
                wale_beams=wale_beams,
                stage_id=stage_id,
            )
            reactions = analysis.reactions
            if analysis.internal_force is not None and wale_result_collector is not None:
                wale_result_collector.append(analysis.internal_force)
            if reactions:
                for reaction in reactions:
                    axial_design = design_effect_standard_to_uls(reaction.axial_force, safety_grade=safety_grade, combined_partial_factor=partial_factor)
                    results.append(
                        SupportForceResult(
                            support_id=reaction.support_id,
                            level_index=level_supports[0].level_index if level_supports else 0,
                            elevation=elevation,
                            tributary_top=round(top_elevation - tributary_top, 6),
                            tributary_bottom=round(top_elevation - tributary_bottom, 6),
                            axial_force=round(reaction.axial_force, 3),
                            axial_force_design=round(axial_design, 3),
                            importance_factor=round(gamma0, 3),
                            partial_factor=round(max(partial_factor, 1.25), 3),
                            face_code=reaction.face_code,
                            support_endpoint=reaction.endpoint,
                            wale_beam_code=reaction.wale_beam_code,
                            wale_chainage=reaction.chainage,
                            tributary_width=reaction.tributary_width,
                            continuous_beam_reaction=reaction.reaction,
                            elastic_support_stiffness=reaction.stiffness,
                            normal_projection_factor=reaction.normal_projection,
                            beam_node_count=reaction.beam_node_count,
                            distribution_method=reaction.method,
                            distribution_note=reaction.note,
                            method=(
                                "JGJ120 pressure band -> continuous wale beam -> elastic strut-node reaction; "
                                "tributary width retained as reference; support axial force = node reaction / normal projection; GB50009/JGJ120 design-effect factor"
                            ),
                        )
                    )
                continue
        fallback_width = segment_length / max(len(level_supports), 1)
        for support in level_supports:
            wall_width = _tributary_width_for_segment(support, segment_name, fallback_width)
            role_factor = _role_factor(support)
            per_support_force = line_load * wall_width * role_factor
            axial_design = design_effect_standard_to_uls(per_support_force, safety_grade=safety_grade, combined_partial_factor=partial_factor)
            results.append(
                SupportForceResult(
                    support_id=support.id,
                    level_index=support.level_index,
                    elevation=elevation,
                    tributary_top=round(top_elevation - tributary_top, 6),
                    tributary_bottom=round(top_elevation - tributary_bottom, 6),
                    axial_force=round(per_support_force, 3),
                    axial_force_design=round(axial_design, 3),
                    importance_factor=round(gamma0, 3),
                    partial_factor=round(max(partial_factor, 1.25), 3),
                    face_code=segment_name,
                    support_endpoint="start" if support.start_face_code == segment_name else "end" if support.end_face_code == segment_name else "unknown",
                    tributary_width=wall_width,
                    distribution_method="tributary_width_fallback",
                    distribution_note="未能形成连续围檩梁模型时，退化为 V1.5 墙面 tributary width 分配。",
                    method=(
                        "fallback: JGJ120-2012 tributary pressure integration; support force = wall line load "
                        "x support-end tributary width x role factor; GB50009/JGJ120 design-effect factor"
                    ),
                )
            )
    return results
