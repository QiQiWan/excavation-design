from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.domain import Project

DEFAULT_CAD_TEMPLATE: dict[str, Any] = {
    "templateVersion": "PitGuard V2.5.0 configurable enterprise CAD standard",
    "enterpriseName": "PitGuard Demo Design Institute",
    "projectCode": "PG-FD",
    "stage": "施工图深化接口",
    "designer": "AI-DRAFT",
    "checker": "ENGINEER-REVIEW",
    "approver": "CHIEF-REVIEW",
    "sheetPrefix": "S",
    "drawingUnit": "m",
    "titleBlock": {
        "width": 120.0,
        "height": 16.0,
        "originX": 0.0,
        "originY": -18.0,
        "projectNameLabel": "工程名称",
        "sheetTitleLabel": "图名",
        "stageLabel": "阶段",
        "sheetNoLabel": "图号",
        "scaleLabel": "比例",
        "designerLabel": "设计",
        "checkerLabel": "校核",
        "approverLabel": "审定",
    },
    "layerStandard": {
        "frame": "PIT_FRAME",
        "title": "PIT_TITLE",
        "text": "PIT_TEXT",
        "dimension": "PIT_DIM",
        "excavation": "PIT_EXCAVATION",
        "wall": "PIT_WALL",
        "wale": "PIT_WALE",
        "support": "PIT_SUPPORT",
        "column": "PIT_COLUMN",
        "pile": "PIT_PILE",
        "rebarMain": "PIT_REBAR_MAIN",
        "rebarStirrup": "PIT_REBAR_STIRRUP",
        "monitor": "PIT_MONITOR",
        "highlight": "PIT_HIGHLIGHT",
    },
    "sheetRules": {
        "defaultScalePlan": "1:200",
        "defaultScaleSection": "1:100",
        "defaultScaleDetail": "1:50",
        "includeCoordinates": True,
        "includeObjectIds": True,
        "includeReviewBoundaryNotes": True,
    },
    "dimensionRules": {
        "textHeight": 0.35,
        "dimensionTextHeight": 0.28,
        "arrowSize": 0.25,
    },
    "issueBinding": {
        "enableCadLocator": True,
        "highlightLayer": "PIT_HIGHLIGHT",
        "writeObjectCodeText": True,
        "writeSheetLocatorIndex": True,
    },
    "signatureWorkflow": {
        "requireDesigner": True,
        "requireChecker": True,
        "requireApprover": True,
        "requireRegisteredEngineer": True,
        "registeredEngineer": "REGISTERED-ENGINEER-REVIEW",
        "sealStatus": "pending_company_workflow",
    },
    "sheetNumberRules": {
        "disciplineCode": "JH",
        "startNumber": 1,
        "numberWidth": 2,
        "separator": "-",
    },
    "fontRules": {
        "primaryFont": "HZTXT",
        "latinFont": "Arial",
        "minTextHeight": 0.22,
        "titleTextHeight": 0.55,
    },
    "lineTypeRules": {
        "center": "CENTER",
        "hidden": "HIDDEN",
        "dimension": "CONTINUOUS",
        "rebar": "CONTINUOUS",
    },
    "plotStyle": {
        "paperSize": "A1",
        "ctb": "monochrome.ctb",
        "lineweightByLayer": True,
    },
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif value is not None:
            result[key] = value
    return result


def normalize_cad_template(project: Project | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    template = deepcopy(DEFAULT_CAD_TEMPLATE)
    if project is not None:
        template = _deep_merge(template, getattr(project, "cad_template", {}) or {})
    if payload:
        template = _deep_merge(template, payload)
    # Light validation and coercion suitable for a configurable enterprise template.
    template["sheetPrefix"] = str(template.get("sheetPrefix") or "S")[:8]
    for key in ("enterpriseName", "projectCode", "stage", "designer", "checker", "approver"):
        template[key] = str(template.get(key) or DEFAULT_CAD_TEMPLATE[key])[:80]
    title = template.setdefault("titleBlock", {})
    title["width"] = float(title.get("width") or 120.0)
    title["height"] = float(title.get("height") or 16.0)
    title["originX"] = float(title.get("originX") or 0.0)
    title["originY"] = float(title.get("originY") or -18.0)
    layers = template.setdefault("layerStandard", {})
    for layer_key, default_layer in DEFAULT_CAD_TEMPLATE["layerStandard"].items():
        value = str(layers.get(layer_key) or default_layer).strip().replace(" ", "_")[:31]
        layers[layer_key] = value or default_layer
    return template


def update_project_cad_template(project: Project, payload: dict[str, Any]) -> Project:
    project.cad_template = normalize_cad_template(project, payload)
    return project



def validate_cad_template(project: Project | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic CAD-template readiness report.

    The check is intentionally rule-based and solver-free.  It verifies that the
    enterprise CAD template contains all fields required by the generated DXF
    drawing-set, including title block, layer map, sheet-numbering and signing
    workflow metadata.
    """
    template = normalize_cad_template(project, payload)
    required_top = ["enterpriseName", "projectCode", "stage", "designer", "checker", "approver", "sheetPrefix"]
    required_layers = ["frame", "title", "text", "dimension", "excavation", "wall", "wale", "support", "column", "pile", "rebarMain", "rebarStirrup", "monitor", "highlight"]
    required_title = ["width", "height", "originX", "originY", "projectNameLabel", "sheetTitleLabel", "stageLabel", "sheetNoLabel", "scaleLabel", "designerLabel", "checkerLabel", "approverLabel"]
    missing: list[str] = []
    for key in required_top:
        if not str(template.get(key) or "").strip():
            missing.append(key)
    for key in required_layers:
        if not str(template.get("layerStandard", {}).get(key) or "").strip():
            missing.append(f"layerStandard.{key}")
    for key in required_title:
        if template.get("titleBlock", {}).get(key) in (None, ""):
            missing.append(f"titleBlock.{key}")
    signature = template.get("signatureWorkflow", {}) or {}
    signature_required = ["requireDesigner", "requireChecker", "requireApprover", "requireRegisteredEngineer", "registeredEngineer", "sealStatus"]
    for key in signature_required:
        if key not in signature or signature.get(key) in (None, ""):
            missing.append(f"signatureWorkflow.{key}")
    duplicate_layers = []
    values = list((template.get("layerStandard", {}) or {}).values())
    for value in sorted(set(values)):
        if values.count(value) > 1 and value not in {"0"}:
            duplicate_layers.append(value)
    completion = round(max(0.0, 100.0 - len(missing) * 6.0 - len(duplicate_layers) * 2.0), 1)
    status = "pass" if completion >= 100.0 and not duplicate_layers else "warning" if completion >= 80.0 else "manual_review"
    return {
        "templateVersion": template.get("templateVersion"),
        "status": status,
        "completion": 100.0 if not missing and not duplicate_layers else completion,
        "missingFields": missing,
        "duplicateLayers": duplicate_layers,
        "checkedItems": {
            "titleBlock": len(required_title),
            "layerStandard": len(required_layers),
            "signatureWorkflow": len(signature_required),
            "sheetNumberRules": len(template.get("sheetNumberRules", {}) or {}),
            "fontRules": len(template.get("fontRules", {}) or {}),
            "lineTypeRules": len(template.get("lineTypeRules", {}) or {}),
        },
        "recommendation": "CAD 企业模板已满足当前 DXF 图纸集生成要求。" if not missing else "补齐缺失模板字段后重新导出 CAD 图纸包。",
        "professionalBoundary": "模板通过表示软件可生成企业化图纸集；正式签章仍由企业签审流程和注册工程师确认。",
    }
