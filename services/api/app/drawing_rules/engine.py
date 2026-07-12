from __future__ import annotations

import hashlib
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterable

from app.schemas.domain import Project
from app.version import SOFTWARE_VERSION


RULE_SCHEMA_VERSION = "1.0"
KNOWN_RENDERERS = {
    "general_notes", "master_plan", "legacy_support_plan", "support_level_plan",
    "excavation_section", "monitoring_plan", "wall_rebar_general", "wall_rebar_elevation",
    "single_wall_rebar_elevation", "wall_rebar_cage", "support_rebar_general",
    "wale_rebar_general", "rebar_bending_schedule", "rebar_geometry_plan", "splice_layout",
    "cage_lifting_plan", "cover_conflict_check", "shop_signoff", "detail_compilation",
    "support_wale_detail", "corner_detail", "support_column_detail", "wall_support_detail",
    "column_pile_detail", "wall_joint_detail", "support_splice_detail", "grid_node_detail", "concave_return_detail",
    "serviceability_quality", "collision_quality", "node_local_quality", "monitoring_calibration",
    "node_hardware_detail", "cage_hoisting_analysis", "coupler_schedule_detail", "embedded_collision_quality",
}
KNOWN_SCOPES = {"general", "rebar", "details"}
KNOWN_MODULES = {"general", "rebar", "details", "quality", "monitoring"}
PAPER_SIZES_MM = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
}


def _sheet(
    rule_id: str,
    sheet_no: str,
    title: str,
    category: str,
    scope: str,
    renderer: str,
    file: str,
    *,
    scale: str | None = None,
    scale_policy: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
    expansion: str = "single",
    model_binding: list[str] | None = None,
    priority: int = 50,
    required: bool = False,
    legacy: bool = False,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "enabled": True,
        "sheetNo": sheet_no,
        "title": title,
        "category": category,
        "scope": scope,
        "renderer": renderer,
        "file": file,
        "fixedScale": scale,
        "scalePolicy": scale_policy or {},
        "trigger": trigger or {"op": "always"},
        "expansion": expansion,
        "modelBinding": model_binding or [],
        "priority": priority,
        "required": required,
        "legacy": legacy,
    }


BASE_RULE_SET: dict[str, Any] = {
    "schemaVersion": RULE_SCHEMA_VERSION,
    "id": "pitguard-balanced",
    "name": "PitGuard 平衡型出图规则集",
    "version": "3.8.0",
    "description": "面向工程审查和施工图深化的默认规则集。",
    "preset": "balanced",
    "modules": {
        "general": {"enabled": True, "required": True},
        "rebar": {"enabled": True, "required": True},
        "details": {"enabled": True, "required": False},
        "quality": {"enabled": True, "required": False},
        "monitoring": {"enabled": True, "required": False}
    },
    "parameters": {
        "defaultPaperSize": "A1",
        "usablePaperRatio": 0.82,
        "allowedPlanScales": [100, 150, 200, 250, 300, 400, 500],
        "allowedSectionScales": [50, 100, 150, 200],
        "allowedDetailScales": [10, 20, 25, 50],
        "minimumTextHeightMm": 2.5,
        "maximumSheetCount": 80,
        "wallSheetsPerDrawing": 1,
        "includeEmptyQualitySheets": False,
        "includeLegacyCompatibilitySheets": True,
        "includeMonitoringLayoutWithoutRecords": True,
        "includePerWallElevations": True,
        "constructionRequiresCurrentApproval": True,
        "constructionRequiresRevision": True,
        "reviewWatermark": True,
    },
    "objectiveWeights": {
        "coverage": 0.32,
        "readability": 0.25,
        "constructability": 0.20,
        "compactness": 0.13,
        "consistency": 0.10,
    },
    "issuePolicy": {
        "review": {"allowBlockingChecks": True, "watermark": True},
        "construction": {
            "allowBlockingChecks": False,
            "requireApproval": True,
            "requireCurrentRevision": True,
            "requireCalculation": True,
        },
    },
    "sheetRules": [
        _sheet("G00", "G-00", "图纸目录、设计总说明与图例", "general", "general", "general_notes", "00_general/G-00_drawing_index_general_notes.dxf", scale="NTS", required=True, priority=100, model_binding=["project", "standards", "drawing_register"]),
        _sheet("S00", "S-00", "基坑围护与支撑总平面图", "global_plan", "general", "master_plan", "10_plans/S-00_retaining_support_general_arrangement.dxf", scale_policy={"kind": "plan", "extent": "project", "preferred": 200}, required=True, priority=100, model_binding=["excavation", "walls", "wales", "supports", "columns"]),
        _sheet("S01", "S-01", "围护结构分幅及构件编号图", "plan", "general", "legacy_support_plan", "S-01_support_plan.dxf", scale_policy={"kind": "plan", "extent": "project", "preferred": 200}, trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.wallCount", "op": "gt", "value": 0}]}, legacy=True, model_binding=["walls"]),
        _sheet("S02", "S-02-L{level:02d}", "第{level}道支撑平面布置图", "level_plan", "general", "support_level_plan", "10_plans/S-02-L{level:02d}_support_level_plan.dxf", scale_policy={"kind": "plan", "extent": "project", "preferred": 150}, trigger={"path": "facts.supportLevelCount", "op": "gt", "value": 0}, expansion="per_level", priority=90, model_binding=["support_level_{level}", "walls", "columns", "nodes"]),
        _sheet("S03", "S-03", "典型开挖剖面与施工阶段图", "section", "general", "excavation_section", "20_sections/S-03_excavation_stage_section.dxf", scale_policy={"kind": "section", "extent": "depth", "preferred": 100}, trigger={"path": "facts.hasExcavation", "op": "eq", "value": True}, required=True, priority=95),
        _sheet("M01", "M-01", "监测点布置总图", "monitoring", "general", "monitoring_plan", "S-06_monitoring_plan.dxf", scale_policy={"kind": "plan", "extent": "project", "preferred": 200}, trigger={"any": [{"path": "facts.monitoringRecordCount", "op": "gt", "value": 0}, {"path": "parameters.includeMonitoringLayoutWithoutRecords", "op": "eq", "value": True}]}, legacy=True),
        _sheet("R01", "R-01", "地下连续墙配筋总图", "rebar_general", "rebar", "wall_rebar_general", "30_rebar/R-01_wall_rebar_general_arrangement.dxf", scale_policy={"kind": "plan", "extent": "project", "preferred": 200}, trigger={"path": "facts.wallCount", "op": "gt", "value": 0}, required=True, priority=95),
        _sheet("R02", "R-02", "地下连续墙分区配筋立面图", "rebar_elevation", "rebar", "wall_rebar_elevation", "30_rebar/R-02_wall_rebar_zone_elevation.dxf", scale_policy={"kind": "section", "extent": "wall_elevation", "preferred": 100}, trigger={"path": "facts.wallCount", "op": "gt", "value": 0}, required=True, priority=95),
        _sheet("R02W", "R-02-W{wall_index:02d}", "{wall_code} 地下连续墙单幅分区配筋立面", "wall_rebar_elevation", "rebar", "single_wall_rebar_elevation", "30_rebar/walls/R-02-W{wall_index:02d}_{wall_token}_rebar_elevation.dxf", scale_policy={"kind": "section", "extent": "single_wall", "preferred": 50}, trigger={"all": [{"path": "facts.wallCount", "op": "gt", "value": 0}, {"path": "parameters.includePerWallElevations", "op": "eq", "value": True}]}, expansion="per_wall", priority=80, model_binding=["{wall_id}", "{segment_id}", "rebar_design_scheme"]),
        _sheet("R03", "R-03", "地下连续墙钢筋笼、接头与吊装详图", "rebar_detail", "rebar", "wall_rebar_cage", "S-02_wall_rebar_cage.dxf", scale_policy={"kind": "detail", "preferred": 50}, trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.wallCount", "op": "gt", "value": 0}]}, legacy=True),
        _sheet("R04", "R-04", "钢筋混凝土支撑配筋总图", "rebar_general", "rebar", "support_rebar_general", "30_rebar/R-04_support_rebar_general_arrangement.dxf", scale_policy={"kind": "section", "preferred": 100}, trigger={"path": "facts.rcSupportCount", "op": "gt", "value": 0}, priority=90),
        _sheet("R05", "R-05", "冠梁、围檩及环梁配筋总图", "rebar_general", "rebar", "wale_rebar_general", "30_rebar/R-05_wale_rebar_general_arrangement.dxf", scale_policy={"kind": "section", "preferred": 100}, trigger={"path": "facts.beamCount", "op": "gt", "value": 0}, priority=85),
        _sheet("R06", "R-06", "钢筋下料、弯曲及逐根索引图", "rebar_schedule", "rebar", "rebar_bending_schedule", "S-07_rebar_bending_schedule.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.hasRetainingSystem", "op": "eq", "value": True}]}, legacy=True),
        _sheet("R07", "R-07", "逐根钢筋几何索引图", "rebar_schedule", "rebar", "rebar_geometry_plan", "S-08_individual_rebar_geometry.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.hasRetainingSystem", "op": "eq", "value": True}]}, legacy=True),
        _sheet("R08", "R-08", "钢筋搭接分区图", "rebar_detail", "rebar", "splice_layout", "S-09_lap_splice_layout.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.hasRetainingSystem", "op": "eq", "value": True}]}, legacy=True),
        _sheet("R09", "R-09", "钢筋笼分节与吊装图", "rebar_detail", "rebar", "cage_lifting_plan", "S-10_cage_segment_lifting_plan.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.wallCount", "op": "gt", "value": 0}]}, legacy=True),
        _sheet("Q01", "Q-01", "保护层、弯折、搭接与签审检查图", "quality", "rebar", "cover_conflict_check", "S-11_cover_bend_check.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.hasRetainingSystem", "op": "eq", "value": True}]}, legacy=True),
        _sheet("Q01B", "Q-01B", "钢筋施工图签审清单", "quality", "rebar", "shop_signoff", "S-12_shop_drawing_signoff_checklist.dxf", scale="NTS", trigger={"all": [{"path": "parameters.includeLegacyCompatibilitySheets", "op": "eq", "value": True}, {"path": "facts.hasRetainingSystem", "op": "eq", "value": True}]}, legacy=True),
        _sheet("D00", "D-00", "典型节点大样索引与组合图", "detail_index", "details", "detail_compilation", "40_details/D-00_typical_detail_compilation.dxf", scale="1:20/1:50", trigger={"path": "facts.supportCount", "op": "gt", "value": 0}, required=True, priority=95),
        _sheet("D01", "D-01", "支撑—围檩节点大样", "node_detail", "details", "support_wale_detail", "S-03_support_wale_node_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.supportNodeCount", "op": "gt", "value": 0}, legacy=True, priority=90),
        _sheet("D02", "D-02", "角撑节点与转角加强大样", "node_detail", "details", "corner_detail", "40_details/D-02_corner_brace_node_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.cornerBraceCount", "op": "gt", "value": 0}, priority=80),
        _sheet("D03", "D-03", "支撑—立柱交叉节点大样", "node_detail", "details", "support_column_detail", "40_details/D-03_support_column_intersection_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"all": [{"path": "facts.supportCount", "op": "gt", "value": 0}, {"path": "facts.columnCount", "op": "gt", "value": 0}]}, priority=85),
        _sheet("D04", "D-04", "地连墙支撑区局部加强大样", "node_detail", "details", "wall_support_detail", "40_details/D-04_wall_support_zone_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"all": [{"path": "facts.wallCount", "op": "gt", "value": 0}, {"path": "facts.supportCount", "op": "gt", "value": 0}]}, priority=85),
        _sheet("D05", "D-05", "临时立柱与立柱桩详图", "pile_detail", "details", "column_pile_detail", "S-05_column_pile_detail.dxf", scale_policy={"kind": "detail", "preferred": 50}, trigger={"path": "facts.columnCount", "op": "gt", "value": 0}, legacy=True),
        _sheet("D06", "D-06", "地下连续墙墙幅接头与钢筋笼连接大样", "node_detail", "details", "wall_joint_detail", "40_details/D-06_wall_panel_joint_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.wallCount", "op": "gt", "value": 1}, priority=85),
        _sheet("D07", "D-07", "钢筋混凝土支撑端部锚固与错开搭接大样", "node_detail", "details", "support_splice_detail", "40_details/D-07_support_anchorage_splice_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.rcSupportCount", "op": "gt", "value": 0}, priority=85),
        _sheet("D08", "D-08", "主次支撑网格交叉节点与立柱连接大样", "node_detail", "details", "grid_node_detail", "40_details/D-08_bidirectional_grid_node_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.secondarySupportCount", "op": "gt", "value": 0}, priority=90, model_binding=["main_strut", "secondary_strut", "columns"]),
        _sheet("D09", "D-09", "异形凹角回墙局部支撑与围檩转折大样", "node_detail", "details", "concave_return_detail", "40_details/D-09_concave_return_wall_support_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"all": [{"path": "facts.concaveVertexCount", "op": "gt", "value": 0}, {"path": "facts.secondarySupportCount", "op": "gt", "value": 0}]}, priority=92, model_binding=["concave_vertices", "secondary_strut", "walls", "wales", "columns"]),
        _sheet("D10", "D-10", "节点承压板、加劲板、焊缝与锚筋深化大样", "node_detail", "details", "node_hardware_detail", "40_details/D-10_node_hardware_detail.dxf", scale_policy={"kind": "detail", "preferred": 20}, trigger={"path": "facts.supportNodeCount", "op": "gt", "value": 0}, priority=96, model_binding=["support_nodes", "bearing_plates", "stiffeners", "welds", "anchor_bars"]),
        _sheet("R10", "R-10", "钢筋笼吊装、运输与临时加强分析图", "rebar_detail", "rebar", "cage_hoisting_analysis", "30_rebar/R-10_cage_hoisting_analysis.dxf", scale="NTS", trigger={"path": "facts.wallCount", "op": "gt", "value": 0}, priority=92, model_binding=["wall_cages", "lifting_points", "transport_segments"]),
        _sheet("R11", "R-11", "机械连接套筒、丝头和接头错开详图", "rebar_detail", "rebar", "coupler_schedule_detail", "30_rebar/R-11_coupler_splice_detail.dxf", scale="NTS", trigger={"path": "facts.hasRetainingSystem", "op": "eq", "value": True}, priority=90, model_binding=["rebar_couplers", "splice_groups"]),
        _sheet("Q04", "Q-04", "预埋件、钢筋和施工净空碰撞检查图", "quality", "details", "embedded_collision_quality", "50_quality/Q-04_embedded_item_collision_check.dxf", scale="NTS", trigger={"path": "facts.supportNodeCount", "op": "gt", "value": 0}, priority=88, model_binding=["embedded_items", "individual_rebar", "clearance_checks"]),
        _sheet("Q02", "Q-02", "长期效应与裂缝控制检查图", "quality", "details", "serviceability_quality", "50_quality/Q-02_serviceability_crack_check.dxf", scale="NTS", trigger={"any": [{"path": "facts.hasCalculation", "op": "eq", "value": True}, {"path": "parameters.includeEmptyQualitySheets", "op": "eq", "value": True}]}, priority=70),
        _sheet("Q03", "Q-03", "构件碰撞、净距与节点拥挤检查图", "quality", "details", "collision_quality", "50_quality/Q-03_collision_clearance_check.dxf", scale="NTS", trigger={"any": [{"path": "facts.supportCount", "op": "gt", "value": 0}, {"path": "parameters.includeEmptyQualitySheets", "op": "eq", "value": True}]}, priority=70),
        _sheet("N01", "N-01", "高利用率节点局部复核索引图", "node_analysis", "details", "node_local_quality", "50_quality/N-01_node_local_analysis.dxf", scale="NTS", trigger={"any": [{"path": "facts.nodeWarningCount", "op": "gt", "value": 0}, {"path": "parameters.includeEmptyQualitySheets", "op": "eq", "value": True}]}, priority=75),
        _sheet("M02", "M-02", "监测反演与参数校准记录图", "monitoring", "details", "monitoring_calibration", "60_monitoring/M-02_monitoring_calibration.dxf", scale="NTS", trigger={"any": [{"path": "facts.monitoringRecordCount", "op": "gt", "value": 0}, {"path": "parameters.includeMonitoringLayoutWithoutRecords", "op": "eq", "value": True}]}, priority=65),
    ],
}


PRESET_PATCHES: dict[str, dict[str, Any]] = {
    "compact": {
        "id": "pitguard-compact",
        "name": "紧凑审查型",
        "description": "减少逐墙图和空白检查图，适合方案审查和快速交流。",
        "preset": "compact",
        "parameters": {
            "defaultPaperSize": "A1", "maximumSheetCount": 45, "includePerWallElevations": False,
            "includeEmptyQualitySheets": False, "includeLegacyCompatibilitySheets": False,
            "includeMonitoringLayoutWithoutRecords": False,
        },
        "objectiveWeights": {"coverage": 0.25, "readability": 0.22, "constructability": 0.12, "compactness": 0.31, "consistency": 0.10},
    },
    "balanced": {},
    "construction": {
        "id": "pitguard-construction",
        "name": "施工深化型",
        "description": "保留逐墙立面、全部节点大样和质量台账，适合施工图复核。",
        "preset": "construction",
        "parameters": {
            "defaultPaperSize": "A1", "maximumSheetCount": 120, "includePerWallElevations": True,
            "includeEmptyQualitySheets": True, "includeLegacyCompatibilitySheets": True,
            "includeMonitoringLayoutWithoutRecords": True,
        },
        "objectiveWeights": {"coverage": 0.35, "readability": 0.25, "constructability": 0.27, "compactness": 0.03, "consistency": 0.10},
    },
    "enterprise-minimal": {
        "id": "pitguard-enterprise-minimal",
        "name": "企业最小发行集",
        "description": "保留总图、分层图、关键配筋和节点，便于企业模板二次深化。",
        "preset": "enterprise-minimal",
        "parameters": {
            "defaultPaperSize": "A1", "maximumSheetCount": 60, "includePerWallElevations": True,
            "includeEmptyQualitySheets": False, "includeLegacyCompatibilitySheets": False,
            "includeMonitoringLayoutWithoutRecords": False,
        },
    },
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif value is not None:
            result[key] = deepcopy(value)
    return result


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _core_rule_pack_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "packages" / "drawing-rules"


def _rule_pack_dirs() -> list[Path]:
    dirs = [_core_rule_pack_dir()]
    override = os.getenv("PITGUARD_DRAWING_RULE_DIR", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _available_preset_names() -> list[str]:
    names = set(PRESET_PATCHES)
    for directory in _rule_pack_dirs():
        preset_dir = directory / "presets"
        if preset_dir.is_dir():
            names.update(path.stem for path in preset_dir.glob("*.json") if path.is_file())
    return sorted(names)


def _load_external_preset(name: str) -> dict[str, Any] | None:
    # The last directory has the highest priority, allowing an enterprise package to override core presets.
    for directory in reversed(_rule_pack_dirs()):
        path = directory / "presets" / f"{name}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid drawing rule preset {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Drawing rule preset must be a JSON object: {path}")
        package_id = directory.name
        manifest_path = directory / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                package_id = str(manifest_payload.get("packageId") or package_id)
            except (OSError, json.JSONDecodeError):
                pass
        payload["sourceFile"] = f"presets/{name}.json"
        payload["sourcePackageId"] = package_id
        return payload
    return None


def get_preset_rule_set(name: str) -> dict[str, Any]:
    external = _load_external_preset(name)
    if external is not None:
        rules = external
    elif name in PRESET_PATCHES:
        rules = _deep_merge(BASE_RULE_SET, PRESET_PATCHES[name])
    else:
        raise ValueError(f"Unknown drawing rule preset: {name}")
    rules = deepcopy(rules)
    rules["version"] = str(rules.get("version") or "3.8.0")
    rules["schemaVersion"] = RULE_SCHEMA_VERSION
    rules["preset"] = str(rules.get("preset") or name)
    return rules


def list_drawing_rule_presets() -> list[dict[str, Any]]:
    result = []
    for name in _available_preset_names():
        rules = get_preset_rule_set(name)
        result.append({
            "id": name,
            "name": rules["name"],
            "description": rules.get("description"),
            "parameters": rules.get("parameters"),
            "objectiveWeights": rules.get("objectiveWeights"),
            "ruleCount": len(rules.get("sheetRules", [])),
        })
    return result


def normalize_drawing_rule_set(payload: dict[str, Any] | None = None, *, preset: str = "balanced") -> dict[str, Any]:
    base_name = str((payload or {}).get("preset") or preset)
    if base_name not in _available_preset_names():
        base_name = "balanced"
    rules = _deep_merge(get_preset_rule_set(base_name), payload or {})
    rules["schemaVersion"] = RULE_SCHEMA_VERSION
    rules["version"] = str(rules.get("version") or "3.8.0")[:32]
    rules["id"] = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(rules.get("id") or "project-drawing-rules"))[:64]
    rules["name"] = str(rules.get("name") or "Project drawing rules")[:120]
    modules = rules.setdefault("modules", {})
    for module_name in KNOWN_MODULES:
        default_module = BASE_RULE_SET["modules"][module_name]
        current_module = modules.get(module_name) if isinstance(modules.get(module_name), dict) else {}
        modules[module_name] = {
            "enabled": bool(current_module.get("enabled", default_module["enabled"])),
            "required": bool(current_module.get("required", default_module["required"])),
        }
    params = rules.setdefault("parameters", {})
    params["defaultPaperSize"] = str(params.get("defaultPaperSize") or "A1").upper()
    if params["defaultPaperSize"] not in PAPER_SIZES_MM:
        params["defaultPaperSize"] = "A1"
    params["usablePaperRatio"] = min(max(float(params.get("usablePaperRatio") or 0.82), 0.55), 0.95)
    params["maximumSheetCount"] = min(max(int(params.get("maximumSheetCount") or 80), 10), 500)
    params["wallSheetsPerDrawing"] = min(max(int(params.get("wallSheetsPerDrawing") or 1), 1), 12)
    params["minimumTextHeightMm"] = min(max(float(params.get("minimumTextHeightMm") or 2.5), 1.8), 5.0)
    for key in ("allowedPlanScales", "allowedSectionScales", "allowedDetailScales"):
        values = sorted({int(x) for x in (params.get(key) or []) if 5 <= int(x) <= 5000})
        params[key] = values or deepcopy(BASE_RULE_SET["parameters"][key])
    for key in ("includeEmptyQualitySheets", "includeLegacyCompatibilitySheets", "includeMonitoringLayoutWithoutRecords", "includePerWallElevations", "constructionRequiresCurrentApproval", "constructionRequiresRevision", "reviewWatermark"):
        params[key] = bool(params.get(key, BASE_RULE_SET["parameters"][key]))
    normalized_rules: list[dict[str, Any]] = []
    for index, raw in enumerate(rules.get("sheetRules") or []):
        item = deepcopy(raw)
        item["id"] = str(item.get("id") or f"RULE-{index+1:03d}")[:64]
        item["enabled"] = bool(item.get("enabled", True))
        item["scope"] = str(item.get("scope") or "general")
        category = str(item.get("category") or "")
        inferred_module = "monitoring" if category == "monitoring" else "quality" if category in {"quality", "node_analysis"} else "rebar" if item["scope"] == "rebar" else "details" if item["scope"] == "details" else "general"
        item["module"] = str(item.get("module") or inferred_module)
        item["priority"] = int(item.get("priority") or 50)
        item["required"] = bool(item.get("required", False))
        item["modelBinding"] = [str(x) for x in item.get("modelBinding") or []]
        normalized_rules.append(item)
    rules["sheetRules"] = normalized_rules
    rules["ruleSetHash"] = _stable_hash({k: v for k, v in rules.items() if k != "ruleSetHash"})
    return rules


def get_effective_drawing_rule_set(project: Project, preset: str | None = None) -> dict[str, Any]:
    stored = getattr(project, "drawing_rule_set", {}) or {}
    if preset:
        return normalize_drawing_rule_set({"preset": preset}, preset=preset)
    return normalize_drawing_rule_set(stored, preset=str(stored.get("preset") or "balanced"))


def _project_extents(project: Project) -> tuple[float, float]:
    if not project.excavation or not project.excavation.outline.points:
        return 60.0, 30.0
    xs = [float(p.x) for p in project.excavation.outline.points]
    ys = [float(p.y) for p in project.excavation.outline.points]
    return max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)


def _concave_vertex_count(project: Project) -> int:
    if not project.excavation:
        return 0
    points = [(float(p.x), float(p.y)) for p in project.excavation.outline.points]
    if len(points) > 2 and points[0] == points[-1]:
        points.pop()
    if len(points) < 4:
        return 0
    area2 = sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))
    orientation = 1.0 if area2 >= 0.0 else -1.0
    count = 0
    for index, current in enumerate(points):
        previous = points[(index - 1) % len(points)]
        following = points[(index + 1) % len(points)]
        cross = (current[0] - previous[0]) * (following[1] - current[1]) - (current[1] - previous[1]) * (following[0] - current[0])
        if cross * orientation < -1e-8:
            count += 1
    return count


def _count_rebar_groups(project: Project) -> int:
    if not project.retaining_system:
        return 0
    items: list[Any] = []
    items.extend(project.retaining_system.diaphragm_walls)
    items.extend(project.retaining_system.supports)
    items.extend(project.retaining_system.crown_beams)
    items.extend(project.retaining_system.wale_beams)
    items.extend(project.retaining_system.ring_beams or [])
    return sum(len(getattr(item, "reinforcement_groups", []) or []) for item in items)


def build_drawing_context(project: Project, rule_set: dict[str, Any] | None = None, advanced_suite: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = normalize_drawing_rule_set(rule_set or get_effective_drawing_rule_set(project))
    ret = project.retaining_system
    supports = list(ret.supports) if ret else []
    levels = sorted({int(s.level_index) for s in supports})
    width, height = _project_extents(project)
    depth = abs(float(project.excavation.top_elevation - project.excavation.bottom_elevation)) if project.excavation else 0.0
    node_warnings = 0
    collision_warnings = 0
    if advanced_suite:
        node_warnings = int((advanced_suite.get("nodeLocal", {}).get("summary") or {}).get("warningCount") or 0)
        collision_warnings = int((advanced_suite.get("collisions", {}).get("summary") or {}).get("warningCount") or 0)
    facts = {
        "hasExcavation": project.excavation is not None,
        "hasRetainingSystem": ret is not None,
        "hasCalculation": bool(project.calculation_results),
        "wallCount": len(ret.diaphragm_walls) if ret else 0,
        "supportCount": len(supports),
        "supportLevelCount": len(levels),
        "supportLevels": levels,
        "columnCount": len(ret.columns) if ret else 0,
        "supportNodeCount": len(ret.support_nodes) if ret else 0,
        "beamCount": (len(ret.crown_beams) + len(ret.wale_beams) + len(ret.ring_beams or [])) if ret else 0,
        "rcSupportCount": sum(1 for s in supports if str(getattr(s, "material", "")).lower().find("concrete") >= 0 or str(getattr(s.section, "shape", "")) == "rectangular"),
        "cornerBraceCount": sum(1 for s in supports if s.support_role == "corner_diagonal"),
        "secondarySupportCount": sum(1 for s in supports if s.support_role == "secondary_strut"),
        "monitoringRecordCount": len(project.monitoring_records),
        "calibrationRunCount": len(project.calibration_runs),
        "drawingRevisionCount": len(project.drawing_revisions),
        "rebarGroupCount": _count_rebar_groups(project),
        "nodeWarningCount": node_warnings,
        "collisionWarningCount": collision_warnings,
        "projectWidthM": width,
        "projectHeightM": height,
        "excavationDepthM": depth,
        "projectAreaM2": width * height,
        "isLargePlan": max(width, height) > 100.0,
        "isDeepExcavation": depth >= 20.0,
        "concaveVertexCount": _concave_vertex_count(project),
    }
    return {"facts": facts, "parameters": rules.get("parameters", {}), "project": {"id": project.id, "name": project.name}, "ruleSet": rules}


def _resolve(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for part in str(path).split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def evaluate_condition(condition: dict[str, Any] | None, context: dict[str, Any]) -> tuple[bool, str]:
    if not condition or condition.get("op") == "always":
        return True, "always"
    if "all" in condition:
        results = [evaluate_condition(item, context) for item in condition.get("all") or []]
        return all(x[0] for x in results), " AND ".join(x[1] for x in results)
    if "any" in condition:
        results = [evaluate_condition(item, context) for item in condition.get("any") or []]
        return any(x[0] for x in results), " OR ".join(x[1] for x in results)
    if "not" in condition:
        result, trace = evaluate_condition(condition.get("not"), context)
        return not result, f"NOT({trace})"
    path = str(condition.get("path") or "")
    op = str(condition.get("op") or "eq")
    expected = condition.get("value")
    actual = _resolve(context, path)
    try:
        if op == "eq": result = actual == expected
        elif op == "neq": result = actual != expected
        elif op == "gt": result = float(actual) > float(expected)
        elif op == "gte": result = float(actual) >= float(expected)
        elif op == "lt": result = float(actual) < float(expected)
        elif op == "lte": result = float(actual) <= float(expected)
        elif op == "in": result = actual in (expected or [])
        elif op == "contains": result = expected in (actual or [])
        elif op == "exists": result = actual is not None
        elif op == "truthy": result = bool(actual)
        else: return False, f"unsupported operator {op}"
    except (TypeError, ValueError):
        result = False
    return bool(result), f"{path} {op} {expected!r} (actual={actual!r})"


def _choose_scale(kind: str, preferred: int, project: Project, rules: dict[str, Any], extent: str = "project", paper_size: str | None = None, orientation: str = "landscape") -> tuple[str, dict[str, Any]]:
    params = rules.get("parameters", {})
    if kind == "detail":
        allowed = params.get("allowedDetailScales") or [10, 20, 25, 50]
        chosen = min(allowed, key=lambda x: abs(int(x) - int(preferred)))
        return f"1:{chosen}", {"mode": "fixed-nearest", "preferred": preferred, "chosen": chosen}
    if kind == "section":
        allowed = params.get("allowedSectionScales") or [50, 100, 150, 200]
        width_m = max(_project_extents(project)[0], 30.0) if extent == "wall_elevation" else max(abs(float(project.excavation.top_elevation - project.excavation.bottom_elevation)) if project.excavation else 20.0, 20.0)
        height_m = max(abs(float(project.excavation.top_elevation - project.excavation.bottom_elevation)) if project.excavation else 20.0, 10.0)
    else:
        allowed = params.get("allowedPlanScales") or [100, 150, 200, 250, 300, 500]
        width_m, height_m = _project_extents(project)
    paper_name = str(paper_size or params.get("defaultPaperSize") or "A1").upper()
    paper = PAPER_SIZES_MM.get(paper_name, PAPER_SIZES_MM["A1"])
    ratio = float(params.get("usablePaperRatio") or 0.82)
    landscape = (paper[0] * ratio, paper[1] * ratio)
    portrait = (paper[1] * ratio, paper[0] * ratio)
    orientation = str(orientation or "landscape").lower()
    def required_for(size: tuple[float, float]) -> float:
        return max(width_m * 1000.0 / max(size[0], 1.0), height_m * 1000.0 / max(size[1], 1.0))
    if orientation == "portrait":
        usable_w, usable_h = portrait
    elif orientation == "auto" and required_for(portrait) < required_for(landscape):
        usable_w, usable_h = portrait
        orientation = "portrait"
    else:
        usable_w, usable_h = landscape
        orientation = "landscape"
    required = required_for((usable_w, usable_h))
    valid = [int(x) for x in allowed if int(x) >= required]
    chosen = min(valid) if valid else max(int(x) for x in allowed)
    if int(preferred) >= required:
        preferred_near = min((int(x) for x in allowed if int(x) >= required), key=lambda x: abs(x - int(preferred)), default=chosen)
        chosen = preferred_near
    return f"1:{chosen}", {"mode": "auto-fit", "requiredDenominator": round(required, 2), "chosen": chosen, "paper": paper_name, "orientation": orientation, "usableRatio": ratio, "minimumTextHeightMm": params.get("minimumTextHeightMm"), "extentM": [round(width_m, 3), round(height_m, 3)]}


def _format_template(value: str, variables: dict[str, Any]) -> str:
    try:
        return value.format(**variables)
    except (KeyError, ValueError):
        return value


def _expand_rule(project: Project, raw: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    expansion = raw.get("expansion") or "single"
    variables_list: list[dict[str, Any]] = [{}]
    ret = project.retaining_system
    if expansion == "per_level":
        levels = sorted({int(s.level_index) for s in (ret.supports if ret else [])})
        variables_list = [{"level": level} for level in levels]
    elif expansion == "per_wall":
        variables_list = []
        walls = list(ret.diaphragm_walls if ret else [])
        group_size = max(int(rules.get("parameters", {}).get("wallSheetsPerDrawing") or 1), 1)
        for group_index, start in enumerate(range(0, len(walls), group_size), start=1):
            group = walls[start:start + group_size]
            if not group:
                continue
            codes = [str(wall.panel_code) for wall in group]
            token = re.sub(r"[^A-Za-z0-9_-]+", "_", "_".join(codes))[:80]
            variables_list.append({
                "wall_index": group_index,
                "wall_code": "、".join(codes),
                "wall_token": token,
                "wall_id": group[0].id,
                "segment_id": group[0].segment_id,
                "wall_ids": [wall.id for wall in group],
                "segment_ids": [wall.segment_id for wall in group],
                "wall_codes": codes,
                "wall_count_on_sheet": len(group),
            })
    expanded: list[dict[str, Any]] = []
    for variables in variables_list:
        item = deepcopy(raw)
        for key in ("sheetNo", "title", "file"):
            item[key] = _format_template(str(item.get(key) or ""), variables)
        item["modelBinding"] = [_format_template(str(x), variables) for x in item.get("modelBinding") or []]
        if variables.get("wall_ids"):
            item["modelBinding"] = list(dict.fromkeys(item["modelBinding"] + [str(x) for x in variables["wall_ids"]] + [str(x) for x in variables.get("segment_ids", [])]))
        item["variables"] = variables
        fixed = item.get("fixedScale")
        if fixed:
            item["scale"] = fixed
            item["scaleDecision"] = {"mode": "fixed", "chosen": fixed}
        else:
            policy = item.get("scalePolicy") or {}
            item["scale"], item["scaleDecision"] = _choose_scale(
                str(policy.get("kind") or "plan"), int(policy.get("preferred") or 100), project, rules,
                str(policy.get("extent") or "project"), str(policy.get("paperSize") or "") or None,
                str(policy.get("orientation") or "landscape"),
            )
        expanded.append(item)
    return expanded


def build_drawing_plan(
    project: Project,
    rule_set: dict[str, Any] | None = None,
    *,
    scope: str = "full",
    issue_mode: str = "review",
    advanced_suite: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = normalize_drawing_rule_set(rule_set or get_effective_drawing_rule_set(project))
    context = build_drawing_context(project, rules, advanced_suite)
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    allowed_scopes = KNOWN_SCOPES if scope == "full" else {scope}
    for raw in sorted(rules.get("sheetRules") or [], key=lambda x: (-int(x.get("priority") or 0), str(x.get("id")))):
        enabled = bool(raw.get("enabled", True))
        trigger_result, trace = evaluate_condition(raw.get("trigger"), context)
        scope_result = str(raw.get("scope")) in allowed_scopes
        module_name = str(raw.get("module") or "general")
        module_result = bool((rules.get("modules", {}).get(module_name) or {}).get("enabled", True))
        renderer_result = str(raw.get("renderer")) in KNOWN_RENDERERS
        include = enabled and trigger_result and scope_result and module_result and renderer_result
        decisions.append({
            "ruleId": raw.get("id"), "module": module_name, "moduleEnabled": module_result,
            "enabled": enabled, "triggered": trigger_result,
            "scopeMatched": scope_result, "rendererKnown": renderer_result, "included": include,
            "trace": trace, "sheetNoPattern": raw.get("sheetNo"), "title": raw.get("title"),
        })
        if include:
            selected.extend(_expand_rule(project, raw, rules))
    max_sheets = int(rules.get("parameters", {}).get("maximumSheetCount") or 80)
    overflow: list[dict[str, Any]] = []
    if len(selected) > max_sheets:
        required = [x for x in selected if x.get("required")]
        optional = sorted([x for x in selected if not x.get("required")], key=lambda x: (-int(x.get("priority") or 0), str(x.get("sheetNo"))))
        kept = required + optional[: max(0, max_sheets - len(required))]
        kept_ids = {id(x) for x in kept}
        overflow = [x for x in selected if id(x) not in kept_ids]
        selected = kept
    selected.sort(key=lambda x: (str(x.get("file", "")), str(x.get("sheetNo", ""))))
    categories = {key: sum(1 for item in selected if item.get("category") == key) for key in sorted({str(item.get("category")) for item in selected})}
    plan = {
        "projectId": project.id,
        "softwareVersion": SOFTWARE_VERSION,
        "drawingRuleSchemaVersion": RULE_SCHEMA_VERSION,
        "drawingRuleSetId": rules.get("id"),
        "drawingRuleSetVersion": rules.get("version"),
        "drawingRuleSetHash": rules.get("ruleSetHash"),
        "preset": rules.get("preset"),
        "scope": scope,
        "issueMode": issue_mode,
        "sheetCount": len(selected),
        "supportLevels": context["facts"]["supportLevels"],
        "categories": categories,
        "sheets": selected,
        "decisions": decisions,
        "overflowSheets": [{"sheetNo": x.get("sheetNo"), "title": x.get("title"), "priority": x.get("priority")} for x in overflow],
        "modules": rules.get("modules"),
        "parameters": rules.get("parameters"),
        "objectiveWeights": rules.get("objectiveWeights"),
        "contextFacts": context.get("facts"),
        "packageFolders": ["00_general", "10_plans", "20_sections", "30_rebar", "40_details", "50_quality", "60_monitoring", "90_schedules"],
        "issueBoundary": "本图纸包由可配置规则集生成。正式施工图发行仍需通过工程闸门、当前设计快照审签、修订绑定及注册工程师复核。",
    }
    from app.drawing_intelligence import build_drawing_intelligence
    intelligence = build_drawing_intelligence(project, context, selected, decisions)
    plan["drawingIntelligence"] = intelligence
    plan["planHash"] = _stable_hash({"rules": rules.get("ruleSetHash"), "scope": scope, "issueMode": issue_mode, "sheets": [{"sheetNo": x.get("sheetNo"), "file": x.get("file"), "scale": x.get("scale")} for x in selected], "intelligence": intelligence.get("quality")})
    return plan


def _condition_valid(condition: Any, path: str, errors: list[dict[str, str]], depth: int = 0) -> None:
    if condition is None:
        return
    if depth > 8:
        errors.append({"path": path, "message": "condition nesting exceeds 8 levels"}); return
    if not isinstance(condition, dict):
        errors.append({"path": path, "message": "condition must be an object"}); return
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        if not isinstance(condition[key], list): errors.append({"path": f"{path}.{key}", "message": "must be an array"})
        else:
            for i, item in enumerate(condition[key]): _condition_valid(item, f"{path}.{key}[{i}]", errors, depth + 1)
        return
    if "not" in condition:
        _condition_valid(condition["not"], f"{path}.not", errors, depth + 1); return
    op = condition.get("op")
    if op not in {"always", "eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "exists", "truthy"}:
        errors.append({"path": f"{path}.op", "message": f"unsupported operator: {op}"})
    if op != "always" and not condition.get("path"):
        errors.append({"path": f"{path}.path", "message": "path is required"})
    elif op != "always":
        root = str(condition.get("path") or "").split(".", 1)[0]
        if root not in {"facts", "parameters", "project", "ruleSet"}:
            errors.append({"path": f"{path}.path", "message": f"unsupported context root: {root}"})


def validate_drawing_rule_set(project: Project | None, payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    incoming_schema = str((payload or {}).get("schemaVersion") or RULE_SCHEMA_VERSION)
    if incoming_schema != RULE_SCHEMA_VERSION:
        errors.append({"path": "schemaVersion", "message": f"unsupported schema version: {incoming_schema}; expected {RULE_SCHEMA_VERSION}"})
    rules = normalize_drawing_rule_set(payload)
    seen_ids: set[str] = set(); seen_patterns: set[str] = set()
    for index, item in enumerate(rules.get("sheetRules") or []):
        base = f"sheetRules[{index}]"
        rule_id = str(item.get("id") or "")
        if rule_id in seen_ids: errors.append({"path": f"{base}.id", "message": f"duplicate rule id: {rule_id}"})
        seen_ids.add(rule_id)
        renderer = str(item.get("renderer") or "")
        if renderer not in KNOWN_RENDERERS: errors.append({"path": f"{base}.renderer", "message": f"unknown renderer: {renderer}"})
        scope = str(item.get("scope") or "")
        if scope not in KNOWN_SCOPES: errors.append({"path": f"{base}.scope", "message": f"unsupported scope: {scope}"})
        module_name = str(item.get("module") or "")
        if module_name not in KNOWN_MODULES: errors.append({"path": f"{base}.module", "message": f"unsupported module: {module_name}"})
        file_pattern = str(item.get("file") or "")
        unsafe_drive = bool(re.match(r"^[A-Za-z]:[\\/]", file_pattern))
        if not file_pattern or file_pattern.startswith("/") or unsafe_drive or "\\" in file_pattern or ".." in file_pattern.split("/"): errors.append({"path": f"{base}.file", "message": "file must be a relative safe path"})
        sheet_pattern = str(item.get("sheetNo") or "")
        if sheet_pattern in seen_patterns and item.get("expansion") == "single": warnings.append({"path": f"{base}.sheetNo", "message": f"duplicate sheet number pattern: {sheet_pattern}"})
        seen_patterns.add(sheet_pattern)
        _condition_valid(item.get("trigger"), f"{base}.trigger", errors)
    for module_name, module in (rules.get("modules") or {}).items():
        if module.get("required") and not module.get("enabled"):
            errors.append({"path": f"modules.{module_name}.enabled", "message": "required drawing module cannot be disabled"})
    weight_sum = sum(float(x) for x in (rules.get("objectiveWeights") or {}).values())
    if abs(weight_sum - 1.0) > 0.02:
        warnings.append({"path": "objectiveWeights", "message": f"weights sum to {weight_sum:.3f}; optimizer will normalize internally"})
    preview = None
    if project is not None and not errors:
        preview = build_drawing_plan(project, rules)
        sheet_nos = [str(x.get("sheetNo")) for x in preview.get("sheets", [])]
        duplicates = sorted({x for x in sheet_nos if sheet_nos.count(x) > 1})
        if duplicates: errors.append({"path": "preview.sheets", "message": f"duplicate expanded sheet numbers: {duplicates}"})
        files = [str(x.get("file")) for x in preview.get("sheets", [])]
        duplicate_files = sorted({x for x in files if files.count(x) > 1})
        if duplicate_files: errors.append({"path": "preview.sheets", "message": f"duplicate expanded output paths: {duplicate_files}"})
        facts = preview.get("contextFacts", {})
        mandatory = {"general_notes", "master_plan", "excavation_section"}
        if facts.get("wallCount", 0): mandatory.update({"wall_rebar_general", "wall_rebar_elevation"})
        if facts.get("supportCount", 0): mandatory.update({"support_level_plan", "detail_compilation"})
        included_renderers = {str(x.get("renderer")) for x in preview.get("sheets", [])}
        missing_mandatory = sorted(mandatory - included_renderers)
        if missing_mandatory: errors.append({"path": "preview.sheets", "message": f"mandatory engineering drawing capabilities are missing: {missing_mandatory}"})
        if preview.get("overflowSheets"): warnings.append({"path": "parameters.maximumSheetCount", "message": f"{len(preview['overflowSheets'])} sheets were dropped by the maximum sheet count"})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "normalized": rules, "preview": preview}



def evaluate_drawing_issue_gate(
    project: Project,
    *,
    issue_mode: str,
    engineering_gate_allowed: bool,
    approval: dict[str, Any],
    current_revision_valid: bool,
    rule_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = normalize_drawing_rule_set(rule_set or get_effective_drawing_rule_set(project))
    policy = (rules.get("issuePolicy") or {}).get(issue_mode, {})
    if issue_mode == "review":
        return {"allowed": True, "reasons": [], "policy": policy, "ruleSetHash": rules.get("ruleSetHash")}
    reasons: list[dict[str, Any]] = []
    if not engineering_gate_allowed:
        reasons.append({"code": "ENGINEERING_GATE_BLOCKED", "message": "工程计算或配筋仍有硬阻断项。"})
    require_calculation = bool(policy.get("requireCalculation", True))
    if require_calculation and not project.calculation_results:
        reasons.append({"code": "CALCULATION_REQUIRED", "message": "施工版发行要求存在当前有效计算结果。"})
    project_requires_approval = bool(getattr(project.design_settings, "require_formal_approval_for_construction", True))
    rule_requires_approval = bool(policy.get("requireApproval", rules.get("parameters", {}).get("constructionRequiresCurrentApproval", True)))
    if (project_requires_approval or rule_requires_approval) and not approval.get("approvalValid"):
        reasons.append({"code": "APPROVAL_REQUIRED", "message": "当前设计快照尚未完成有效四级审签。"})
    rule_requires_revision = bool(policy.get("requireCurrentRevision", rules.get("parameters", {}).get("constructionRequiresRevision", True)))
    if rule_requires_revision and not current_revision_valid:
        reasons.append({"code": "CURRENT_REVISION_REQUIRED", "message": "缺少绑定当前设计快照的施工版修订记录。"})
    return {
        "allowed": not reasons, "reasons": reasons, "policy": policy,
        "ruleSetHash": rules.get("ruleSetHash"), "ruleSetId": rules.get("id"),
    }

def _plan_score(plan: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    weights = rules.get("objectiveWeights") or {}
    total_rules = max(sum(1 for d in plan.get("decisions", []) if d.get("enabled") and d.get("scopeMatched") and d.get("rendererKnown")), 1)
    triggered_rules = sum(1 for d in plan.get("decisions", []) if d.get("included"))
    coverage = min(triggered_rules / total_rules, 1.0)
    required = [x for x in rules.get("sheetRules", []) if x.get("required") and x.get("enabled", True)]
    required_patterns = {str(x.get("id")) for x in required}
    included_ids = {str(x.get("id")) for x in plan.get("sheets", [])}
    required_coverage = len(required_patterns & included_ids) / max(len(required_patterns), 1)
    auto_fit = [x.get("scaleDecision", {}) for x in plan.get("sheets", []) if x.get("scaleDecision", {}).get("mode") == "auto-fit"]
    readability = 1.0
    if auto_fit:
        ratios = [min(float(x.get("requiredDenominator") or x.get("chosen") or 1) / max(float(x.get("chosen") or 1), 1.0), 1.0) for x in auto_fit]
        readability = sum(ratios) / len(ratios)
    detail_count = sum(1 for x in plan.get("sheets", []) if x.get("scope") in {"rebar", "details"})
    constructability = min(detail_count / max(int(plan.get("contextFacts", {}).get("supportLevelCount") or 1) + 8, 1), 1.0)
    max_sheets = max(int(rules.get("parameters", {}).get("maximumSheetCount") or 80), 1)
    compactness = max(0.0, 1.0 - len(plan.get("sheets", [])) / max_sheets)
    consistency = 1.0 if not plan.get("overflowSheets") else max(0.0, 1.0 - len(plan.get("overflowSheets")) / max_sheets)
    facts = plan.get("contextFacts", {})
    expected_renderers = {"general_notes", "master_plan", "excavation_section"}
    if facts.get("wallCount", 0): expected_renderers.update({"wall_rebar_general", "wall_rebar_elevation"})
    if facts.get("supportCount", 0): expected_renderers.update({"support_level_plan", "detail_compilation", "support_wale_detail"})
    if facts.get("hasCalculation"): expected_renderers.add("serviceability_quality")
    if facts.get("supportNodeCount", 0): expected_renderers.add("node_local_quality")
    included_renderers = {str(x.get("renderer")) for x in plan.get("sheets", [])}
    capability_coverage = len(expected_renderers & included_renderers) / max(len(expected_renderers), 1)
    metrics = {
        "coverage": 0.45 * coverage + 0.30 * required_coverage + 0.25 * capability_coverage,
        "readability": readability,
        "constructability": constructability,
        "compactness": compactness,
        "consistency": consistency,
    }
    weight_sum = sum(float(weights.get(k, 0.0)) for k in metrics) or 1.0
    score = 100.0 * sum(metrics[k] * float(weights.get(k, 0.0)) for k in metrics) / weight_sum
    return {"score": round(score, 2), "metrics": {k: round(v * 100.0, 2) for k, v in metrics.items()}, "sheetCount": len(plan.get("sheets", [])), "overflowCount": len(plan.get("overflowSheets", []))}


def optimize_drawing_rule_set(project: Project, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    base = normalize_drawing_rule_set(payload.get("ruleSet") or get_effective_drawing_rule_set(project))
    objective_override = payload.get("objectiveWeights") or {}
    include_rule_sets = bool(payload.get("includeRuleSets", False))
    candidates: list[dict[str, Any]] = []
    requested_presets = payload.get("presets") or ["compact", "balanced", "construction", "enterprise-minimal"]
    paper_sizes = payload.get("paperSizes") or [base.get("parameters", {}).get("defaultPaperSize", "A1"), "A2"]
    wall_group_sizes = payload.get("wallSheetsPerDrawing") or [base.get("parameters", {}).get("wallSheetsPerDrawing", 1), 2]
    seen: set[str] = set()

    def add_candidate(candidate: dict[str, Any], *, source: str, label: str) -> None:
        candidate = normalize_drawing_rule_set(candidate)
        if objective_override:
            candidate["objectiveWeights"] = _deep_merge(candidate.get("objectiveWeights", {}), objective_override)
            candidate = normalize_drawing_rule_set(candidate)
        if candidate["ruleSetHash"] in seen:
            return
        seen.add(candidate["ruleSetHash"])
        plan = build_drawing_plan(project, candidate)
        score = _plan_score(plan, candidate)
        candidates.append({
            "candidateId": f"drawing-rule-{candidate['ruleSetHash']}",
            "preset": candidate.get("preset") or source,
            "source": source,
            "label": label,
            "paperSize": candidate["parameters"]["defaultPaperSize"],
            "wallSheetsPerDrawing": candidate["parameters"].get("wallSheetsPerDrawing", 1),
            "ruleSetMeta": {
                "id": candidate.get("id"), "name": candidate.get("name"), "version": candidate.get("version"),
                "preset": candidate.get("preset"), "ruleSetHash": candidate.get("ruleSetHash"),
                "sourcePackageId": candidate.get("sourcePackageId"),
            },
            **({"ruleSet": candidate} if include_rule_sets else {}),
            "planSummary": {
                "sheetCount": plan["sheetCount"],
                "categories": plan["categories"],
                "overflowCount": len(plan["overflowSheets"]),
                "planHash": plan["planHash"],
            },
            **score,
        })

    # Preserve project-specific sheet edits and optimize only layout parameters first.
    for paper in paper_sizes:
        for group_size in wall_group_sizes:
            custom = deepcopy(base)
            custom["id"] = f"{base.get('id', 'project')}-optimized-{paper}-w{group_size}"
            custom["name"] = f"{base.get('name', '项目规则集')} · {paper} · 每图{group_size}幅墙"
            custom.setdefault("parameters", {})["defaultPaperSize"] = paper if paper in PAPER_SIZES_MM else "A1"
            custom["parameters"]["wallSheetsPerDrawing"] = int(group_size)
            add_candidate(custom, source="project-current", label="保留项目自定义规则")

    # Also compare curated presets as alternative baselines.
    for preset in requested_presets:
        if preset not in _available_preset_names():
            continue
        for paper in paper_sizes:
            for group_size in wall_group_sizes:
                candidate = get_preset_rule_set(preset)
                candidate["parameters"]["defaultPaperSize"] = paper if paper in PAPER_SIZES_MM else "A1"
                candidate["parameters"]["wallSheetsPerDrawing"] = int(group_size)
                add_candidate(candidate, source="preset", label=str(candidate.get("name") or preset))

    candidates.sort(key=lambda x: (-float(x["score"]), int(x["sheetCount"]), str(x["candidateId"])))
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return {
        "projectId": project.id,
        "baseRuleSetHash": base.get("ruleSetHash"),
        "candidateCount": len(candidates),
        "recommendedCandidateId": candidates[0]["candidateId"] if candidates else None,
        "candidates": candidates,
        "method": "项目自定义规则保留 + 预设规则对照 + 图幅/逐墙合图参数枚举 + 覆盖度/可读性/施工深化/紧凑性/一致性加权评分。",
        "candidatePayloadMode": "full-rule-set" if include_rule_sets else "metadata-only",
        "boundary": "评分用于规则集推荐，正式图纸内容仍受工程计算、配筋、审签和企业制图标准约束。",
    }



def drawing_rule_capabilities() -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    for directory in _rule_pack_dirs():
        package_id = directory.name
        version = None
        manifest_path = directory / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                package_id = str(manifest.get("packageId") or package_id)
                version = manifest.get("version")
            except (OSError, json.JSONDecodeError):
                pass
        packages.append({"packageId": package_id, "version": version, "presetCount": len(list((directory / "presets").glob("*.json"))) if (directory / "presets").is_dir() else 0})
    return {
        "schemaVersion": RULE_SCHEMA_VERSION,
        "modules": sorted(KNOWN_MODULES),
        "renderers": sorted(KNOWN_RENDERERS),
        "scopes": sorted(KNOWN_SCOPES),
        "expansions": ["single", "per_level", "per_wall"],
        "operators": ["always", "eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "exists", "truthy"],
        "contextRoots": ["facts", "parameters", "project", "ruleSet"],
        "paperSizesMm": {key: list(value) for key, value in PAPER_SIZES_MM.items()},
        "rulePackages": packages,
    }
