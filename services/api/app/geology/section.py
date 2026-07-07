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
        warnings.append("缺少钻孔数据，无法生成代表性剖面。")

    return GeologicalSection(
        segment_id=segment.id,
        section_name=f"{segment.name} representative section",
        top_elevation=project.excavation.top_elevation,
        bottom_elevation=project.excavation.bottom_elevation,
        layers=layers,
        warnings=warnings,
    )
