from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import ezdxf

from app.schemas.domain import Project
from app.services.review_workflow import project_snapshot_hash, review_status


def validate_dxf_file(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        return {"file": path.as_posix(), "status": "fail", "issues": [f"DXF解析失败: {exc}"]}
    if doc.dxfversion not in {"AC1027", "AC1032"}:
        issues.append(f"DXF版本应为R2013/R2018，当前为{doc.dxfversion}")
    if int(doc.header.get("$INSUNITS", 0) or 0) != 4:
        issues.append("模型空间单位未设置为毫米")
    if "PIT_CN" not in doc.styles:
        issues.append("缺少PIT_CN中文文字样式")
    paper_layouts = [layout for layout in doc.layouts if layout.name.lower() not in {"model", "layout1"}]
    if not paper_layouts:
        issues.append("缺少正式纸空间布局")
    else:
        for layout in paper_layouts:
            if not any(entity.dxftype() == "VIEWPORT" for entity in layout):
                issues.append(f"纸空间{layout.name}缺少视口")
    audit = doc.audit()
    if audit.errors:
        issues.append(f"DXF审计发现{len(audit.errors)}个错误")
    entity_types = {entity.dxftype() for entity in doc.modelspace()}
    if "LWPOLYLINE" in entity_types and doc.dxfversion == "AC1009":
        issues.append("R12文件包含不兼容LWPOLYLINE")
    return {
        "file": path.as_posix(),
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "dxfVersion": doc.dxfversion,
        "modelEntityCount": len(doc.modelspace()),
        "paperLayoutCount": len(paper_layouts),
        "auditErrorCount": len(audit.errors),
    }


def validate_dxf_package(package_dir: Path) -> dict[str, Any]:
    files = sorted(package_dir.rglob("*.dxf"))
    results = [validate_dxf_file(path) for path in files]
    fail_count = sum(item["status"] == "fail" for item in results)
    return {
        "status": "pass" if files and fail_count == 0 else "fail",
        "fileCount": len(files),
        "failCount": fail_count,
        "results": results,
    }


def build_construction_issue_gate(
    project: Project,
    detailing: dict[str, Any],
    dxf_validation: dict[str, Any],
    issue_mode: str,
    drawing_completeness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = review_status(project)
    snapshot = project_snapshot_hash(project)
    latest = project.calculation_results[-1] if project.calculation_results else None
    checks: list[dict[str, Any]] = []

    def add(code: str, passed: bool, message: str, severity: str = "fail") -> None:
        checks.append({"code": code, "status": "pass" if passed else severity, "message": message})

    add("CALCULATION_EXISTS", latest is not None, "存在当前设计快照的计算结果")
    fail_count = int((latest.check_summary or {}).get("fail", 0) or 0) if latest else 999
    add("CALCULATION_NO_FAIL", latest is not None and fail_count == 0, f"计算硬失败数量={fail_count}")
    scheme_fail = int((detailing.get("designScheme") or {}).get("summary", {}).get("failCount", 0) or 0)
    add("REBAR_DESIGN_NO_FAIL", scheme_fail == 0, f"配筋设计硬失败数量={scheme_fail}")
    fabrication_fail = int((detailing.get("fabrication") or {}).get("summary", {}).get("hardFailureCount", 0) or 0)
    add("FABRICATION_NO_FAIL", fabrication_fail == 0, f"加工与净距硬失败数量={fabrication_fail}")
    deep_summary = (detailing.get("deepDetailing") or {}).get("summary") or {}
    deep_fail = int(deep_summary.get("hardFailureCount", 0) or 0)
    add("DEEP_DETAILING_NO_FAIL", deep_fail == 0, f"节点钢构件、吊装与预埋件碰撞硬失败数量={deep_fail}")
    add("DXF_VALID", dxf_validation.get("status") == "pass", f"DXF校验失败文件数={dxf_validation.get('failCount', 0)}")
    if drawing_completeness is not None:
        add("DRAWING_COMPLETENESS", drawing_completeness.get("status") != "fail", f"施工图完整性阻断项={drawing_completeness.get('blockerCount', 0)}")

    if issue_mode == "construction":
        add("FOUR_LEVEL_APPROVAL", bool(review.get("approvalValid")), "当前设计快照已完成四级岗位分离审签")
        revisions = [r for r in project.drawing_revisions if r.issue_status == "construction" and r.snapshot_hash == snapshot]
        add("CONSTRUCTION_REVISION", bool(revisions), "存在绑定当前设计快照的施工版修订记录")
    else:
        add("FOUR_LEVEL_APPROVAL", bool(review.get("approvalValid")), "审查版允许未批准，但正式发行前必须完成四级审签", "warning")
        add("CONSTRUCTION_REVISION", any(r.snapshot_hash == snapshot for r in project.drawing_revisions), "审查版允许无施工版修订记录", "warning")

    blockers = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warning"]
    status = "fail" if blockers else "warning" if warnings else "pass"
    return {
        "status": status,
        "allowedForConstructionIssue": issue_mode == "construction" and not blockers,
        "issueMode": issue_mode,
        "snapshotHash": snapshot,
        "review": review,
        "checks": checks,
        "blockerCount": len(blockers),
        "warningCount": len(warnings),
        "dxfValidationSummary": {k: v for k, v in dxf_validation.items() if k != "results"},
        "drawingCompletenessSummary": {k: v for k, v in (drawing_completeness or {}).items() if k != "checks"},
    }


def write_sha256_manifest(root: Path, output: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == output:
            continue
        rel = path.relative_to(root).as_posix()
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.write_text("\n".join(f"{digest}  {rel}" for rel, digest in hashes.items()) + "\n", encoding="utf-8")
    return hashes
