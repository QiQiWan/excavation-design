from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas.domain import Project


@lru_cache(maxsize=1)
def load_enterprise_libraries() -> dict[str, dict[str, Any]]:
    root = Path(__file__).resolve().parents[4] / "packages" / "enterprise"
    libraries: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        library_id = str(payload.get("libraryId") or path.stem)
        payload["sourcePath"] = path.as_posix()
        libraries[library_id] = payload
    return libraries


def list_enterprise_libraries() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for library in load_enterprise_libraries().values():
        rows.append({
            "libraryId": library.get("libraryId"),
            "libraryVersion": library.get("libraryVersion"),
            "name": library.get("name"),
            "status": library.get("status"),
            "standardTemplateCount": len(library.get("standardTemplates") or []),
            "nodeTemplateCount": len(library.get("nodeTemplates") or []),
            "rebarCombinationCount": len(library.get("rebarCombinations") or []),
            "boundary": library.get("boundary"),
        })
    return rows


def resolve_enterprise_library(project: Project) -> dict[str, Any]:
    libraries = load_enterprise_libraries()
    requested = str(project.design_settings.enterprise_library_id or "pitguard_default")
    library = libraries.get(requested) or libraries.get("pitguard_default") or {}
    standard_id = str(project.design_settings.local_standard_template_id or "")
    node_library_id = str(project.design_settings.node_template_library_id or "")
    rebar_library_id = str(project.design_settings.rebar_combination_library_id or "")
    standard = next((row for row in library.get("standardTemplates") or [] if str(row.get("id")) == standard_id), None)
    if standard is None:
        standard = next(iter(library.get("standardTemplates") or []), None)
    return {
        "library": library,
        "selection": {
            "enterpriseLibraryId": library.get("libraryId") or requested,
            "localStandardTemplateId": standard.get("id") if isinstance(standard, dict) else standard_id,
            "nodeTemplateLibraryId": node_library_id,
            "rebarCombinationLibraryId": rebar_library_id,
        },
        "standardTemplate": standard,
        "nodeTemplates": list(library.get("nodeTemplates") or []),
        "rebarCombinations": list(library.get("rebarCombinations") or []),
        "drawingStandards": dict(library.get("drawingStandards") or {}),
        "boundary": library.get("boundary"),
    }


def select_node_template(project: Project, *, section_type: str, axial_force_kn: float) -> dict[str, Any] | None:
    resolved = resolve_enterprise_library(project)
    candidates: list[dict[str, Any]] = []
    for row in resolved.get("nodeTemplates") or []:
        if section_type not in (row.get("hostTypes") or []):
            continue
        low, high = list(row.get("axialForceRangeKn") or [0.0, float("inf")])[:2]
        if float(low) <= axial_force_kn <= float(high):
            candidates.append(dict(row))
    if not candidates:
        return None
    candidates.sort(key=lambda row: float((row.get("axialForceRangeKn") or [0, 1e12])[1]))
    return candidates[0]


def validate_enterprise_library(project: Project) -> dict[str, Any]:
    resolved = resolve_enterprise_library(project)
    library = dict(resolved.get("library") or {})
    issues: list[dict[str, Any]] = []
    if not library:
        issues.append({"code": "ENTERPRISE_LIBRARY_MISSING", "status": "fail", "message": "企业资源库不可用。"})
    if not resolved.get("standardTemplate"):
        issues.append({"code": "STANDARD_TEMPLATE_MISSING", "status": "warning", "message": "未找到项目选择的地方/企业标准模板。"})
    if not resolved.get("nodeTemplates"):
        issues.append({"code": "NODE_TEMPLATE_MISSING", "status": "warning", "message": "节点模板目录为空。"})
    if not resolved.get("rebarCombinations"):
        issues.append({"code": "REBAR_COMBINATION_MISSING", "status": "warning", "message": "钢筋组合目录为空。"})

    def duplicate_ids(rows: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set(); duplicates: set[str] = set()
        for row in rows:
            item_id = str(row.get("id") or "").strip()
            if not item_id:
                issues.append({"code": "RESOURCE_ID_MISSING", "status": "warning", "message": "资源库存在缺少 id 的记录。"})
            elif item_id in seen:
                duplicates.add(item_id)
            seen.add(item_id)
        return sorted(duplicates)

    for family, rows in (
        ("标准模板", list(library.get("standardTemplates") or [])),
        ("节点模板", list(resolved.get("nodeTemplates") or [])),
        ("钢筋组合", list(resolved.get("rebarCombinations") or [])),
    ):
        duplicates = duplicate_ids(rows)
        if duplicates:
            issues.append({"code": "DUPLICATE_RESOURCE_ID", "status": "fail", "message": f"{family}存在重复 id：{'、'.join(duplicates)}。"})

    for template in list(library.get("standardTemplates") or []):
        targets = dict(template.get("safetyTargets") or {})
        invalid = [key for key, value in targets.items() if not isinstance(value, (int, float)) or float(value) < 1.0]
        if invalid:
            issues.append({"code": "INVALID_SAFETY_TARGET", "status": "fail", "message": f"模板 {template.get('id')} 的安全目标无效：{'、'.join(invalid)}。"})
    for node in list(resolved.get("nodeTemplates") or []):
        force_range = list(node.get("axialForceRangeKn") or [])
        if len(force_range) < 2 or float(force_range[0]) < 0 or float(force_range[1]) <= float(force_range[0]):
            issues.append({"code": "INVALID_NODE_FORCE_RANGE", "status": "fail", "message": f"节点模板 {node.get('id')} 的轴力适用范围无效。"})
        if not node.get("drawingRef"):
            issues.append({"code": "NODE_DRAWING_REF_MISSING", "status": "warning", "message": f"节点模板 {node.get('id')} 缺少标准图引用。"})

    status = "fail" if any(row["status"] == "fail" for row in issues) else "warning" if issues else "pass"
    return {
        "status": status,
        "selection": resolved.get("selection"),
        "libraryVersion": library.get("libraryVersion"),
        "issues": issues,
        "summary": {
            "standardTemplateCount": len(library.get("standardTemplates") or []),
            "nodeTemplateCount": len(resolved.get("nodeTemplates") or []),
            "rebarCombinationCount": len(resolved.get("rebarCombinations") or []),
            "standardTemplateAvailable": bool(resolved.get("standardTemplate")),
        },
        "boundary": resolved.get("boundary"),
    }
