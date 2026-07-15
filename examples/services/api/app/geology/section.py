from __future__ import annotations

from app.schemas.domain import GeologicalLayer, GeologicalSection, Project, SoilParameters


def extract_representative_section(project: Project, segment_id: str) -> GeologicalSection:
    if not project.excavation:
        raise ValueError("Project has no excavation")
    segment = next((s for s in project.excavation.segments if s.id == segment_id or s.name == segment_id), None)
    if segment is None:
        raise ValueError(f"Segment not found: {segment_id}")

    strata_by_code = {s.code: s for s in project.strata}
    warnings: list[str] = []
    layers: list[GeologicalLayer] = []

    # MVP: if boreholes exist, use the nearest borehole to segment midpoint as a representative vertical column.
    if project.boreholes:
        nearest = min(project.boreholes, key=lambda b: (b.x - segment.midpoint.x) ** 2 + (b.y - segment.midpoint.y) ** 2)
        for layer in nearest.layers:
            stratum = strata_by_code.get(layer.stratum_code)
            layers.append(
                GeologicalLayer(
                    stratum_code=layer.stratum_code,
                    stratum_name=layer.stratum_name,
                    top_elevation=layer.top_elevation,
                    bottom_elevation=layer.bottom_elevation,
                    thickness=round(layer.top_elevation - layer.bottom_elevation, 6),
                    parameters=stratum.parameters if stratum else SoilParameters(),
                )
            )
        warnings.append(f"MVP 代表性剖面采用最近钻孔 {nearest.code} 的一维土柱；后续应改为从三维地质模型查询。")
    else:
        # A geometry/topology draft must remain diagnosable before the site
        # investigation is imported. Use one explicitly non-issuable screening
        # layer so structural-quality checks can run; the geological design-domain
        # gate remains a hard failure in the formal report.
        bottom_candidates = [project.excavation.bottom_elevation - 10.0]
        if project.retaining_system:
            bottom_candidates.extend(float(wall.bottom_elevation) - 5.0 for wall in project.retaining_system.diaphragm_walls)
        fallback_bottom = min(bottom_candidates)
        parameters = project.strata[0].parameters if project.strata else SoilParameters(
            unit_weight=18.5,
            saturated_unit_weight=20.0,
            effective_unit_weight=10.0,
            cohesion=8.0,
            friction_angle=25.0,
            elastic_modulus=15000.0,
            poisson_ratio=0.35,
            k0=0.60,
            horizontal_subgrade_modulus=10000.0,
        )
        layers.append(GeologicalLayer(
            stratum_code="SCREENING-UNVERIFIED",
            stratum_name="未验证初步筛查土层",
            top_elevation=project.excavation.top_elevation,
            bottom_elevation=fallback_bottom,
            thickness=round(project.excavation.top_elevation - fallback_bottom, 6),
            parameters=parameters,
        ))
        warnings.append(
            "缺少钻孔和可用地质模型，采用未验证的保守单层土参数执行初步筛查；"
            "该结果不得用于正式设计、施工图发行或监测控制值确定。"
        )

    return GeologicalSection(
        segment_id=segment.id,
        section_name=f"{segment.name} representative section",
        top_elevation=project.excavation.top_elevation,
        bottom_elevation=project.excavation.bottom_elevation,
        layers=layers,
        warnings=warnings,
    )
