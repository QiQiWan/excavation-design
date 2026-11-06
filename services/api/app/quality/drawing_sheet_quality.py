from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import ezdxf


# Minimum evidence expected from each renderer.  These are publication-quality
# checks, not structural design checks.  They ensure that a generated sheet has
# the graphical vocabulary needed for professional review.
_RENDERER_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "general_notes": {"min_entities": 30, "min_text": 18, "layers": ["PIT_FRAME", "PIT_TITLE", "PIT_TEXT"]},
    "drawing_reference_matrix": {"min_entities": 45, "min_text": 24, "layers": ["PIT_TABLE", "PIT_TITLE", "PIT_TEXT"]},
    "master_plan": {"min_entities": 45, "min_text": 8, "min_dimensions": 2, "layers": ["PIT_EXCAVATION", "PIT_WALL", "PIT_DIM", "PIT_SECTION"]},
    "wall_panel_layout": {"min_entities": 55, "min_text": 12, "min_dimensions": 3, "layers": ["PIT_WALL", "PIT_WALL_CL", "PIT_DIM", "PIT_TABLE"]},
    "support_level_plan": {"min_entities": 45, "min_text": 10, "min_dimensions": 2, "layers": ["PIT_EXCAVATION", "PIT_WALL", "PIT_DIM", "PIT_TABLE"]},
    "excavation_section": {"min_entities": 35, "min_text": 10, "min_dimensions": 3, "layers": ["PIT_WALL", "PIT_GROUND", "PIT_EXCAVATION", "PIT_SUPPORT", "PIT_DIM"]},
    "longitudinal_section": {"min_entities": 40, "min_text": 12, "min_dimensions": 3, "layers": ["PIT_WALL", "PIT_GROUND", "PIT_EXCAVATION", "PIT_SUPPORT", "PIT_GEOLOGY", "PIT_WATER", "PIT_DIM"]},
    "construction_stage_sequence": {"min_entities": 55, "min_text": 18, "layers": ["PIT_STAGE", "PIT_WALL", "PIT_SUPPORT", "PIT_EXCAVATION", "PIT_TABLE"]},
    "wall_rebar_general": {"min_entities": 40, "min_text": 10, "layers": ["PIT_REBAR_MAIN", "PIT_REBAR_DIST", "PIT_TABLE"]},
    "wall_rebar_elevation": {"min_entities": 45, "min_text": 10, "layers": ["PIT_REBAR_MAIN", "PIT_REBAR_DIST", "PIT_DIM"]},
    "single_wall_rebar_elevation": {"min_entities": 35, "min_text": 8, "layers": ["PIT_REBAR_MAIN", "PIT_REBAR_DIST", "PIT_DIM"]},
    "support_rebar_general": {"min_entities": 30, "min_text": 8, "layers": ["PIT_REBAR_MAIN", "PIT_REBAR_STIRRUP", "PIT_TABLE"]},
    "wale_rebar_general": {"min_entities": 30, "min_text": 8, "layers": ["PIT_REBAR_MAIN", "PIT_REBAR_STIRRUP", "PIT_TABLE"]},
    "wall_joint_detail": {"min_entities": 70, "min_text": 12, "min_dimensions": 4, "layers": ["PIT_WATERSTOP", "PIT_REBAR_MAIN", "PIT_TABLE", "PIT_DIM"]},
    "support_column_detail": {"min_entities": 35, "min_text": 8, "min_dimensions": 2, "layers": ["PIT_COLUMN", "PIT_SUPPORT", "PIT_FOUNDATION", "PIT_DIM"]},
    "support_splice_detail": {"min_entities": 30, "min_text": 8, "min_dimensions": 2, "layers": ["PIT_SUPPORT", "PIT_REBAR_MAIN", "PIT_DIM"]},
    "serviceability_quality": {"min_entities": 15, "min_text": 5, "layers": ["PIT_FRAME", "PIT_TITLE", "PIT_TEXT"]},
    "collision_quality": {"min_entities": 15, "min_text": 5, "layers": ["PIT_FRAME", "PIT_TITLE", "PIT_TEXT"]},
    "node_local_quality": {"min_entities": 15, "min_text": 5, "layers": ["PIT_FRAME", "PIT_TITLE", "PIT_TEXT"]},
    "drawing_quality_summary": {"min_entities": 35, "min_text": 18, "layers": ["PIT_TABLE", "PIT_TITLE", "PIT_TEXT"]},
}


def _entity_counts(doc: ezdxf.document.Drawing) -> tuple[dict[str, int], dict[str, int]]:
    by_type: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for entity in doc.modelspace():
        etype = entity.dxftype()
        layer = str(entity.dxf.get("layer", "0"))
        by_type[etype] = by_type.get(etype, 0) + 1
        by_layer[layer] = by_layer.get(layer, 0) + 1
    return by_type, by_layer


def _paper_evidence(doc: ezdxf.document.Drawing) -> dict[str, Any]:
    layouts = [layout for layout in doc.layouts if layout.name.lower() not in {"model", "layout1"}]
    viewport_count = sum(1 for layout in layouts for e in layout if e.dxftype() == "VIEWPORT")
    paper_texts = [
        str(e.dxf.get("text", ""))
        for layout in layouts
        for e in layout
        if e.dxftype() in {"TEXT", "MTEXT"}
    ]
    return {
        "layoutCount": len(layouts),
        "viewportCount": viewport_count,
        "paperText": " ".join(paper_texts),
    }


def evaluate_drawing_sheet_quality(
    package_dir: Path,
    manifest: dict[str, Any],
    issue_mode: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sheet_numbers: list[str] = []

    for sheet in manifest.get("sheets", []):
        sheet_no = str(sheet.get("sheetNo") or "")
        renderer = str(sheet.get("renderer") or "")
        rel = Path(str(sheet.get("file") or ""))
        path = package_dir / rel
        sheet_numbers.append(sheet_no)
        row: dict[str, Any] = {
            "sheetNo": sheet_no,
            "title": str(sheet.get("title") or ""),
            "renderer": renderer,
            "file": rel.as_posix(),
            "paperSize": sheet.get("paperSize"),
            "scale": sheet.get("scale"),
            "required": bool(sheet.get("required")),
            "status": "pass",
            "issues": [],
        }
        if not path.exists():
            row["status"] = "fail"
            row["issues"] = ["图纸文件缺失"]
            rows.append(row)
            continue
        try:
            doc = ezdxf.readfile(path)
            by_type, by_layer = _entity_counts(doc)
            paper = _paper_evidence(doc)
        except Exception as exc:
            row["status"] = "fail"
            row["issues"] = [f"DXF无法解析: {exc}"]
            rows.append(row)
            continue

        total = sum(by_type.values())
        text_count = by_type.get("TEXT", 0) + by_type.get("MTEXT", 0)
        dimension_count = by_type.get("DIMENSION", 0)
        row.update({
            "modelEntityCount": total,
            "textCount": text_count,
            "dimensionCount": dimension_count,
            "layerCount": len(by_layer),
            "layoutCount": paper["layoutCount"],
            "viewportCount": paper["viewportCount"],
            "layers": sorted(by_layer),
        })
        req = _RENDERER_REQUIREMENTS.get(renderer, {"min_entities": 18, "min_text": 4, "layers": ["PIT_FRAME", "PIT_TITLE"]})
        issues: list[str] = []
        if total < int(req.get("min_entities", 0)):
            issues.append(f"模型空间实体不足({total}<{req['min_entities']})")
        if text_count < int(req.get("min_text", 0)):
            issues.append(f"文字标注不足({text_count}<{req['min_text']})")
        if dimension_count < int(req.get("min_dimensions", 0)):
            issues.append(f"原生尺寸不足({dimension_count}<{req['min_dimensions']})")
        missing_layers = [layer for layer in req.get("layers", []) if layer not in by_layer and layer not in doc.layers]
        if missing_layers:
            issues.append("缺少关键图层:" + ",".join(missing_layers))
        if paper["layoutCount"] < 1 or paper["viewportCount"] < 1:
            issues.append("缺少正式纸空间或锁定视口")
        paper_text = str(paper["paperText"])
        if sheet_no and sheet_no not in paper_text:
            issues.append("图签未找到当前图号")
        if str(sheet.get("scale") or "") and str(sheet.get("scale")) not in paper_text:
            issues.append("图签未找到当前比例")

        if issues:
            # Required sheets and construction issues are hard failures.  Optional
            # review sheets are warnings so design development can continue.
            hard = issue_mode == "construction"
            row["status"] = "fail" if hard else "warning"
            row["issues"] = issues
        rows.append(row)

    duplicates = sorted({no for no in sheet_numbers if no and sheet_numbers.count(no) > 1})
    fail_count = sum(row["status"] == "fail" for row in rows)
    warning_count = sum(row["status"] == "warning" for row in rows)
    pass_count = sum(row["status"] == "pass" for row in rows)
    possible = max(len(rows), 1)
    score = round(max(0.0, 100.0 * (pass_count + 0.5 * warning_count) / possible), 1)
    status = "fail" if fail_count or duplicates else "warning" if warning_count else "pass"
    grade = "construction_ready" if status == "pass" and issue_mode == "construction" else "review_ready" if status != "fail" else "development_only"
    return {
        "status": status,
        "grade": grade,
        "score": score,
        "sheetCount": len(rows),
        "passCount": pass_count,
        "warningCount": warning_count,
        "failCount": fail_count,
        "duplicateSheetNumbers": duplicates,
        "sheets": rows,
        "boundary": "该报告校验图纸表达深度、图层、尺寸、纸空间、图签与可解析性；结构安全和施工可行性仍由计算、审签和现场复核控制。",
    }


def write_drawing_sheet_quality_files(result: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "sheet_no", "title", "renderer", "status", "model_entities", "texts", "dimensions",
            "layers", "layouts", "viewports", "file", "issues",
        ])
        for row in result.get("sheets", []):
            writer.writerow([
                row.get("sheetNo"), row.get("title"), row.get("renderer"), row.get("status"),
                row.get("modelEntityCount", 0), row.get("textCount", 0), row.get("dimensionCount", 0),
                row.get("layerCount", 0), row.get("layoutCount", 0), row.get("viewportCount", 0),
                row.get("file"), "; ".join(row.get("issues") or []),
            ])
