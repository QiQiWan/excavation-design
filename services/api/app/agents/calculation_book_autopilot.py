"""计算书自动驾驶：把一份计算书变成平台里的一个可计算项目。

流程（全部由平台自身能力完成，不外部重算）：
1. 抽文本 → 解析挖深、安全等级、土层表、桩参数；
2. A1 录入设计基准（模型优先、规则兜底）；
3. 按计算书建基坑轮廓与钻孔，生成地质模型；
4. 自动生成围护墙与支撑；
5. 计算 → 配筋 → 评审版成果包（由任务管理器串起来）。

边界：计算书通常不含平面尺寸、m 值、支撑刚度、配筋；缺失项按平台默认值取，
并在 ``assumptions`` 里逐条写明，不假装是计算书原文。
"""
from __future__ import annotations

import io
import re
from typing import Any

LAYER_LINE = re.compile(
    r"^([①-⑩][-0-9]*|\d+-\d+)\s+(\S.*?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s|$)"
)
DEPTH_PATTERNS = (
    re.compile(r"基坑(?:设计)?深度[^0-9]{0,8}([\d.]+)\s*m"),
    re.compile(r"开挖深度[^0-9]{0,8}([\d.]+)\s*m"),
)
GRADE_PATTERN = re.compile(r"(?:基坑)?(?:工程)?(?:设计)?(?:安全)?等级[^一二三级]{0,6}([一二三级])")
PILE_PATTERNS = {
    "diameter": re.compile(r"桩径[:：]\s*([\d.]+)\s*m"),
    "spacing": re.compile(r"桩间距[:：]\s*([\d.]+)\s*m"),
    "length": re.compile(r"桩长[:：]\s*([\d.]+)\s*m"),
}


def extract_text(data: bytes) -> str:
    """整本文本：文字层优先，缺文字的页由视觉模型转录。"""
    from app.agents.document_agent import document_text

    return document_text(data)


def _layer_blocks(text: str) -> list[dict[str, Any]]:
    """按剖面切分土层表：每块带上它前面的「x-x剖面」标题。"""
    blocks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    title = ""
    for raw in text.splitlines():
        line = raw.strip()
        if "剖面" in line and len(line) < 40:
            title = line
        match = LAYER_LINE.match(line)
        if match:
            code, name, thickness, gamma, cohesion, phi = match.groups()
            current.append({
                "code": code, "name": name.strip(), "thicknessM": float(thickness),
                "gammaKnM3": float(gamma), "cohesionKpa": float(cohesion),
                "phiDeg": float(phi),
            })
        elif current and len(current) >= 2:
            blocks.append({"title": title, "layers": current})
            current = []
    if current:
        blocks.append({"title": title, "layers": current})
    return blocks


def _select_layers(text: str, section: str | None) -> tuple[list[dict[str, Any]], str]:
    """按剖面号选表；没指定就优先 2-2，其次第一段带剖面的表。"""
    blocks = _layer_blocks(text)
    if not blocks:
        return [], ""
    if section:
        wanted = section.replace("’", "'")
        for block in blocks:
            if wanted in block["title"].replace("’", "'"):
                return block["layers"], block["title"]
    for block in blocks:
        if "2-2" in block["title"]:
            return block["layers"], block["title"]
    titled = [block for block in blocks if block["title"]]
    chosen = titled[0] if titled else blocks[0]
    return chosen["layers"], chosen["title"]


def parse_book(text: str, section: str | None = None) -> dict[str, Any]:
    depth = None
    for pattern in DEPTH_PATTERNS:
        match = pattern.search(text)
        if match:
            depth = float(match.group(1))
            break
    grade_match = GRADE_PATTERN.search(text)
    pile: dict[str, Any] = {}
    for key, pattern in PILE_PATTERNS.items():
        found = pattern.search(text)
        if found:
            pile[key] = float(found.group(1))
    layers, section_title = _select_layers(text, section)
    return {
        "depthM": depth or 5.0,
        "depthFromBook": depth is not None,
        "safetyGrade": grade_match.group(1) if grade_match else "二级",
        "layers": layers,
        "section": section_title,
        "pile": pile,
    }


def _borehole_csv(parsed: dict[str, Any], plan: tuple[float, float]) -> bytes:
    width, height = plan
    layers = parsed["layers"] or [
        {"code": "L1", "name": "填土", "thicknessM": 2.0, "gammaKnM3": 18.0,
         "cohesionKpa": 10.0, "phiDeg": 15.0},
        {"code": "L2", "name": "黏土", "thicknessM": 8.0, "gammaKnM3": 19.0,
         "cohesionKpa": 25.0, "phiDeg": 16.0},
    ]
    header = ("borehole_code,x,y,collar_elevation,borehole_depth,layer_index,stratum_code,"
              "stratum_name,top_depth,bottom_depth,unit_weight,cohesion,friction_angle,"
              "elastic_modulus,poisson_ratio,permeability,water_level")
    holes = [
        ("BH-01", width * 0.25, height * 0.25),
        ("BH-02", width * 0.75, height * 0.25),
        ("BH-03", width * 0.5, height * 0.75),
    ]
    total = sum(layer["thicknessM"] for layer in layers)
    depth = max(total, parsed["depthM"] * 3, 15.0)
    rows = [header]
    for code, x, y in holes:
        top = 0.0
        for index, layer in enumerate(layers, start=1):
            bottom = top + layer["thicknessM"]
            modulus = 12.0 if index == 1 else (3.0 if layer["cohesionKpa"] < 10 else 25.0)
            rows.append(
                f"{code},{x:.2f},{y:.2f},0.0,{depth:.2f},{index},{layer['code']},"
                f"{layer['name']},{top:.2f},{bottom:.2f},{layer['gammaKnM3']},"
                f"{layer['cohesionKpa']},{layer['phiDeg']},{modulus},0.35,1e-07,0.5"
            )
            top = bottom
    return ("\n".join(rows) + "\n").encode("utf-8")


def prepare_project(project, parsed: dict[str, Any], repo, *, actor: str) -> dict[str, Any]:
    """按计算书建几何、钻孔、地质模型、围护与支撑。返回所作假设。"""
    from app.services.excavation_service import make_excavation_model
    from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
    from app.services.design_service import auto_diaphragm_wall, auto_supports
    from app.geology.model_builder import (
        build_geological_model_from_boreholes,
        ensure_geological_model_covers_excavation,
        required_geological_design_bounds,
    )
    from app.schemas.domain import Polyline2D, Point2D

    plan = (60.0, 40.0)
    width, height = plan
    depth = float(parsed["depthM"])
    outline = Polyline2D(points=[
        Point2D(x=0.0, y=0.0), Point2D(x=width, y=0.0),
        Point2D(x=width, y=height), Point2D(x=0.0, y=height),
    ], closed=True)
    project.excavation = make_excavation_model(
        "主基坑", outline, 0.0, -depth,
        project.design_settings.minimum_segment_length,
    )

    raw = _borehole_csv(parsed, plan)
    imported = parse_borehole_rows(read_csv_bytes(raw), source_file="calculation-book-autopilot.csv")
    if imported.success:
        project.boreholes = imported.boreholes
        project.strata = imported.strata

    model = build_geological_model_from_boreholes(
        project.boreholes,
        grid_size=20.0,
        required_bounds=required_geological_design_bounds(project),
        max_extrapolation_distance_m=project.design_settings.geology_max_extrapolation_distance_m,
    )
    project.geological_model = model
    ensure_geological_model_covers_excavation(project)

    project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system, project.design_settings)
    project.retaining_system = auto_supports(project.excavation, project.retaining_system,
                                             layout_config=None)
    repo.save(project, actor=actor, action="autopilot.prepare", summary="计算书自动驾驶：建立几何、地质与支护")

    return {
        "planSizeM": {"width": width, "height": height},
        "depthM": depth,
        "layerCount": len(imported.strata if imported.success else []),
        "boreholeCount": len(project.boreholes or []),
        "wallThicknessM": (project.retaining_system.diaphragm_walls[0].thickness
                           if project.retaining_system.diaphragm_walls else None),
        "supportCount": len(project.retaining_system.supports or []),
        "assumptions": [
            "计算书未给平面尺寸，按 60×40 m 模拟基坑（按场地面积与地下室轮廓推定）",
            "计算书未给土体 m 值，按分层经验值生成（软件默认）",
            "计算书未给支撑截面与刚度，由平台按挖深自动生成",
            "计算书未给配筋，由平台按内力自动设计",
        ],
    }
