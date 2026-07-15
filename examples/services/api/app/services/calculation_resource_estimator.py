from __future__ import annotations

import math
import os
from typing import Any

from app.schemas.domain import Project


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 1_000_000) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def estimate_calculation_resources(project: Project, *, candidate_count: int = 0) -> dict[str, Any]:
    """Estimate the upper-bound resource class before a heavy calculation.

    The estimator is deliberately conservative. It is a runtime protection gate,
    not an engineering result. It prevents a malformed or excessively detailed
    project from taking the API and worker down together.
    """
    retaining = project.retaining_system
    excavation = project.excavation
    wall_count = len(retaining.diaphragm_walls) if retaining else 0
    support_count = len(retaining.supports) if retaining else 0
    wale_count = len(retaining.wale_beams) if retaining else 0
    column_count = len(retaining.columns) if retaining else 0
    stage_count = max(1, len(project.calculation_cases) or len({getattr(item, "level_index", 0) for item in (retaining.supports if retaining else [])}) + 2)
    segment_count = max(1, len(excavation.segments) if excavation else wall_count)
    borehole_count = len(project.boreholes)
    surface_cells = 0
    if project.geological_model:
        for surface in project.geological_model.surfaces:
            grid = surface.grid
            surface_cells += len(grid.x_values) * len(grid.y_values)

    # Current coupled solver is dense but bounded per face. The dominant cost in
    # real projects is repeated stage/face payload construction and serialization.
    face_dofs = min(96, 18 + max(0, math.ceil(support_count / max(segment_count, 1))))
    dense_matrix_mb = stage_count * segment_count * (face_dofs ** 2) * 8.0 / (1024.0 ** 2)
    result_payload_mb = (
        stage_count * segment_count * 0.45
        + stage_count * support_count * 0.025
        + stage_count * wale_count * 0.10
        + surface_cells * 8.0 / (1024.0 ** 2)
    )
    copy_multiplier = 1.0 + min(max(candidate_count, 0), 3) * 0.65
    estimated_peak_mb = (320.0 + dense_matrix_mb * 4.0 + result_payload_mb * 5.0
                         + support_count * 0.35 + wall_count * 1.5 + column_count * 0.08
                         + borehole_count * 0.25) * copy_multiplier
    estimated_peak_mb = round(max(384.0, estimated_peak_mb), 1)

    worker_limit = _env_int("PITGUARD_WORKER_MEMORY_MAX_MB", 8192, 2048, 262144)
    soft_limit = _env_int("PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB", max(2048, int(worker_limit * 0.82)), 1024, 262144)
    ratio = estimated_peak_mb / max(worker_limit, 1)
    if ratio >= 0.92 or support_count > 2400 or stage_count * segment_count > 1200:
        risk = "blocked"
    elif ratio >= 0.72 or support_count > 1200 or stage_count * segment_count > 650:
        risk = "high"
    elif ratio >= 0.45 or support_count > 600:
        risk = "elevated"
    else:
        risk = "normal"

    recommendations: list[str] = []
    if candidate_count:
        recommendations.append("候选完整计算应逐个执行，当前方案计算完成后再运行A/B/C。")
    if support_count > 600:
        recommendations.append("减少显示级杆件复制，采用分区计算或只保留当前方案完整结果。")
    if stage_count > 20:
        recommendations.append("合并仅用于展示且构件激活状态相同的施工阶段。")
    if surface_cells > 500_000:
        recommendations.append("地质表面网格应在结构计算前降采样，原始网格保留在独立文件中。")
    if risk == "blocked":
        recommendations.append("当前规模超过单worker安全预算，应分区计算、提高worker上限或迁移稀疏求解器。")

    return {
        "status": risk,
        "estimatedPeakMemoryMb": estimated_peak_mb,
        "workerMemoryMaxMb": worker_limit,
        "workerSoftLimitMb": soft_limit,
        "budgetRatio": round(ratio, 4),
        "stageCount": stage_count,
        "segmentCount": segment_count,
        "wallCount": wall_count,
        "supportCount": support_count,
        "waleCount": wale_count,
        "columnCount": column_count,
        "geologySurfaceCellCount": surface_cells,
        "estimatedFaceDofs": face_dofs,
        "candidateCount": max(0, candidate_count),
        "safeModeRequired": risk in {"high", "blocked"},
        "calculationAllowed": risk != "blocked",
        "recommendations": recommendations,
        "method": "conservative stage-face payload and dense-subsystem memory upper-bound estimator",
    }
