from __future__ import annotations

import csv
import hashlib
import html
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.compliance.assurance import evaluate_project_assurance
from app.drawings.formal_issue import export_formal_drawing_package
from app.ifc.exporter import export_simplified_ifc
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
from app.reports.docx_report import export_docx_report
from app.schemas.domain import Project
from app.services.design_scheme_ledger import export_design_scheme_ledger
from app.services.rebar_export import export_rebar_detailing_package
from app.services.review_workflow import project_snapshot_hash, review_status
from app.services.wall_length_optimizer import export_wall_length_redundancy_report
from app.version import SOFTWARE_VERSION, version_manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(root: Path, path: Path, role: str, audience: str, issue_level: str, status: str = "generated", note: str = "", artifact_id: str | None = None) -> dict[str, Any]:
    return {
        "id": artifact_id or f"A-{hashlib.sha1(path.relative_to(root).as_posix().encode('utf-8')).hexdigest()[:10]}",
        "file": path.relative_to(root).as_posix(),
        "filename": path.name,
        "role": role,
        "audience": audience,
        "issueLevel": issue_level,
        "status": status,
        "sizeBytes": path.stat().st_size if path.exists() else 0,
        "sha256": _sha256(path) if path.exists() else None,
        "note": note,
    }


def _copy_artifact(root: Path, source: Path, relative: str, role: str, audience: str, issue_level: str, artifacts: list[dict[str, Any]], note: str = "") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    artifacts.append(_artifact_record(root, target, role, audience, issue_level, note=note))
    return target


def _read_json_from_zip(path: Path, member_suffix: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as zf:
            member = next((name for name in zf.namelist() if name.endswith(member_suffix)), None)
            if not member:
                return {}
            return json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        return {}


def _extract_selected_members(source_zip: Path, root: Path, mapping: dict[str, str], artifacts: list[dict[str, Any]]) -> list[Path]:
    """Expose high-frequency review files without forcing users to open nested ZIPs."""
    extracted: list[Path] = []
    if not source_zip or not source_zip.exists():
        return extracted
    with zipfile.ZipFile(source_zip) as zf:
        names = zf.namelist()
        for suffix, relative in mapping.items():
            member = next((name for name in names if name.endswith(suffix)), None)
            if not member:
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
            artifacts.append(_artifact_record(root, target, "交付包快速审查文件", "设计/校核/审查", "audit", note=f"从正式图纸包提取: {member}"))
            extracted.append(target)
    return extracted


def _write_relationship_matrix(root: Path, artifacts: list[dict[str, Any]]) -> None:
    roles = {item.get("role"): item.get("file") for item in artifacts}
    rows = [
        ("项目快照", roles.get("项目完整快照", "50_data/project_snapshot.json"), "计算书", roles.get("计算书", "30_reports/PitGuard_calculation_report.docx"), "输入参数、构件ID、工况和检查状态"),
        ("项目快照", roles.get("项目完整快照", "50_data/project_snapshot.json"), "施工图发行包", roles.get("施工图发行包", "10_drawings/PitGuard_formal_drawing_package.zip"), "几何、构件编号、标高、配筋和图纸索引"),
        ("项目快照", roles.get("项目完整快照", "50_data/project_snapshot.json"), "钢筋加工深化包", roles.get("钢筋加工深化包", "40_rebar/PitGuard_rebar_detailing_package.zip"), "宿主构件、钢筋组、逐根几何和检查"),
        ("计算书", roles.get("计算书", "30_reports/PitGuard_calculation_report.docx"), "施工图发行包", roles.get("施工图发行包", "10_drawings/PitGuard_formal_drawing_package.zip"), "控制内力、截面、配筋和稳定结论"),
        ("施工图发行包", roles.get("施工图发行包", "10_drawings/PitGuard_formal_drawing_package.zip"), "IFC模型", "20_bim/*.ifc", "构件ID、几何位置、材料和发行快照"),
    ]
    path = root / "90_audit/deliverable_relationship_matrix.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source_role", "source_file", "target_role", "target_file", "traceability_basis"])
        writer.writerows(rows)


def _write_verifier(root: Path) -> None:
    lines = [
        "#!/usr/bin/env python3",
        "from pathlib import Path",
        "import hashlib, sys",
        "root = Path(__file__).resolve().parents[1]",
        "checksum = root / '90_audit' / 'SHA256SUMS.txt'",
        "failed = []",
        "for line in checksum.read_text(encoding='utf-8').splitlines():",
        "    if not line.strip(): continue",
        "    expected, rel = line.split('  ', 1)",
        "    path = root / rel",
        "    if not path.exists(): failed.append((rel, 'missing')); continue",
        "    h = hashlib.sha256(path.read_bytes()).hexdigest()",
        "    if h != expected: failed.append((rel, 'hash mismatch'))",
        "if failed:",
        "    print('FAILED')",
        "    [print(item[0], item[1]) for item in failed]",
        "    sys.exit(1)",
        "print('PASS: all listed files match SHA256SUMS.txt')",
    ]
    (root / "90_audit/verify_delivery_package.py").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_registers(root: Path, artifacts: list[dict[str, Any]], acceptance: list[dict[str, Any]]) -> None:
    with (root / "00_release/deliverable_register.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["artifact_id", "file", "role", "audience", "issue_level", "status", "size_bytes", "sha256", "note"])
        for item in artifacts:
            writer.writerow([item.get("id"), item.get("file"), item.get("role"), item.get("audience"), item.get("issueLevel"), item.get("status"), item.get("sizeBytes"), item.get("sha256"), item.get("note")])
    with (root / "00_release/acceptance_matrix.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["category", "check", "status", "evidence", "responsible_role", "action"])
        for item in acceptance:
            writer.writerow([item.get("category"), item.get("check"), item.get("status"), item.get("evidence"), item.get("responsibleRole"), item.get("action")])


def _write_index(root: Path, project: Project, manifest: dict[str, Any]) -> None:
    rows = "".join(
        f"<tr><td>{html.escape(str(x.get('role')))}</td><td><a href='../{html.escape(str(x.get('file')))}'>{html.escape(str(x.get('file')))}</a></td><td>{html.escape(str(x.get('issueLevel')))}</td><td>{html.escape(str(x.get('status')))}</td></tr>"
        for x in manifest.get("artifacts", [])
    )
    checks = "".join(
        f"<tr><td>{html.escape(str(x.get('category')))}</td><td>{html.escape(str(x.get('check')))}</td><td>{html.escape(str(x.get('status')))}</td><td>{html.escape(str(x.get('action')))}</td></tr>"
        for x in manifest.get("acceptance", [])
    )
    content = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>PitGuard交付索引</title>
<style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#1f2937}}table{{border-collapse:collapse;width:100%;margin:14px 0 28px}}th,td{{border:1px solid #cbd5e1;padding:8px;text-align:left}}th{{background:#e2e8f0}}.status{{font-size:20px;font-weight:700}}code{{background:#f1f5f9;padding:2px 5px}}</style></head><body>
<h1>{html.escape(project.name)} · PitGuard V{SOFTWARE_VERSION} 协同成果交付包</h1>
<p class='status'>发行等级：{html.escape(str(manifest.get('releaseGrade')))}；工程状态：{html.escape(str(manifest.get('engineeringStatus')))}</p>
<p>快照：<code>{html.escape(str(manifest.get('snapshotHash')))}</code>；生成时间：{html.escape(str(manifest.get('generatedAt')))}</p>
<h2>成果文件</h2><table><thead><tr><th>用途</th><th>文件</th><th>发行层级</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>
<h2>验收矩阵</h2><table><thead><tr><th>类别</th><th>检查</th><th>状态</th><th>下一步</th></tr></thead><tbody>{checks}</tbody></table>
<p>正式施工发行必须同时满足计算、图纸、IFC、钢筋深化、审签、修订和项目现场条件。审查版不得直接用于施工。</p>
</body></html>"""
    (root / "00_release/index.html").write_text(content, encoding="utf-8")


def export_coordinated_delivery_package(
    project: Project,
    output_dir: str | Path,
    *,
    issue_mode: str = "review",
    rebar_mode: str = "balanced",
    include_ifc_profiles: bool = True,
) -> Path:
    if issue_mode not in {"review", "construction"}:
        raise ValueError(f"Unsupported issue mode: {issue_mode}")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    root = out / f"{project.id}_coordinated_delivery_{issue_mode}_v{SOFTWARE_VERSION.replace('.', '_')}"
    if root.exists():
        shutil.rmtree(root)
    for folder in ("00_release", "10_drawings", "20_bim", "30_reports", "40_rebar", "50_data", "90_audit"):
        (root / folder).mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def generate(name: str, role: str, audience: str, issue_level: str, relative: str, fn: Callable[[], Path], note: str = "") -> Path | None:
        try:
            source = fn()
            return _copy_artifact(root, source, relative, role, audience, issue_level, artifacts, note)
        except Exception as exc:
            failures.append({"artifact": name, "status": "fail", "message": str(exc)})
            if issue_mode == "construction":
                raise
            return None

    formal = generate(
        "formal_drawings", "施工图发行包", "设计/校核/施工/BIM", issue_mode,
        "10_drawings/PitGuard_formal_drawing_package.zip",
        lambda: export_formal_drawing_package(project, out, issue_mode=issue_mode, rebar_mode=rebar_mode),
        "含DXF、批量PDF、修订台账、逐图质量与出图门禁。",
    )
    if formal:
        _extract_selected_members(
            formal,
            root,
            {
                "batch_plot.pdf": "10_drawings/quick_review/PitGuard_batch_plot.pdf",
                "drawing_register.csv": "10_drawings/quick_review/drawing_register.csv",
                "drawing_sheet_quality.json": "10_drawings/quick_review/drawing_sheet_quality.json",
                "drawing_completeness.json": "10_drawings/quick_review/drawing_completeness.json",
                "construction_issue_gate.json": "10_drawings/quick_review/construction_issue_gate.json",
                "drawing_model_calculation_standard_matrix.csv": "10_drawings/quick_review/drawing_model_calculation_standard_matrix.csv",
                "support_junction_schedule.csv": "10_drawings/quick_review/support_junction_schedule.csv",
                "wall_panel_cage_traceability.csv": "10_drawings/quick_review/wall_panel_cage_traceability.csv",
                "cross_artifact_traceability.json": "10_drawings/quick_review/cross_artifact_traceability.json",
            },
            artifacts,
        )

    report = generate(
        "calculation_report", "计算书", "设计/校核/审查", "review" if issue_mode == "review" else "construction",
        "30_reports/PitGuard_calculation_report.docx", lambda: export_docx_report(project, out),
        "含计算过程、规范映射、控制结果和质量门禁。",
    )
    rebar = generate(
        "rebar_detailing", "钢筋加工深化包", "翻样/加工/施工/审查", "fabrication_review",
        "40_rebar/PitGuard_rebar_detailing_package.zip", lambda: export_rebar_detailing_package(project, out, mode=rebar_mode),
        "XLSX为人工主表，CSV/JSON为完整机器数据；须先处理失败和人工复核项。",
    )

    if include_ifc_profiles:
        for mode, filename, role in (
            ("coordination_light", "coordination_light.ifc", "IFC轻量协调模型"),
            ("analysis_model", "analysis_model.ifc", "IFC分析交换模型"),
            ("construction_visual", "construction_visual.ifc", "IFC施工可视化模型"),
            ("design_detailed", "design_detailed.ifc", "IFC详细语义模型"),
        ):
            def make_ifc(mode: str = mode) -> Path:
                pre = evaluate_ifc_model_compatibility(project)
                path = export_simplified_ifc(project, out, export_mode=mode)
                check = validate_ifc_file(path, base=pre)
                sidecar = path.with_suffix(".ifc_check.json")
                sidecar.write_text(json.dumps(check.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
                return path
            target = generate(mode, role, "BIM/计算/协调", "coordination", f"20_bim/{filename}", make_ifc)
            if target:
                source_sidecar = out / f"{project.id}_{mode}.ifc_check.json"
                if source_sidecar.exists():
                    _copy_artifact(root, source_sidecar, f"20_bim/{filename}.check.json", "IFC兼容性检查", "BIM/审查", "audit", artifacts)
                source_manifest = out / f"{project.id}_{mode}.ifc_manifest.json"
                if source_manifest.exists():
                    _copy_artifact(root, source_manifest, f"20_bim/{filename}.manifest.json", "IFC对象与优化追溯清单", "BIM/设计/审查", "audit", artifacts)

    generate("design_scheme_ledger", "方案与交付台账", "设计/校核/项目管理", "audit", "50_data/design_scheme_ledger.json", lambda: export_design_scheme_ledger(project, out, mode=rebar_mode))
    generate("wall_length_redundancy", "围护墙长度优化报告", "设计/校核", "audit", "50_data/wall_length_redundancy.json", lambda: export_wall_length_redundancy_report(project, out, mode=rebar_mode))

    project_json = root / "50_data/project_snapshot.json"
    project_json.write_text(json.dumps(project.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts.append(_artifact_record(root, project_json, "项目完整快照", "归档/迁移/二次开发", "archive"))

    drawing_gate = _read_json_from_zip(formal, "construction_issue_gate.json") if formal else {}
    sheet_quality = _read_json_from_zip(formal, "drawing_sheet_quality.json") if formal else {}
    drawing_completeness = _read_json_from_zip(formal, "drawing_completeness.json") if formal else {}
    assurance = evaluate_project_assurance(project)
    review = review_status(project)

    acceptance = [
        {"category": "计算", "check": "工程计算无硬失败", "status": "pass" if assurance.get("engineeringCheckStatus") == "pass" else str(assurance.get("engineeringCheckStatus")), "evidence": "计算书/项目快照", "responsibleRole": "设计/校核", "action": "处理fail、warning和人工复核项"},
        {"category": "图纸", "check": "图种和逐图表达质量", "status": str(sheet_quality.get("status") or "missing"), "evidence": "drawing_sheet_quality.json", "responsibleRole": "设计/校核", "action": "修复失败图纸的图层、尺寸、图签和内容深度"},
        {"category": "图纸", "check": "施工图完整性", "status": str(drawing_completeness.get("status") or "missing"), "evidence": "drawing_completeness.json", "responsibleRole": "专业负责人", "action": "补齐必要图种、表格和节点大样"},
        {"category": "发行", "check": "施工图发行门禁", "status": str(drawing_gate.get("status") or "missing"), "evidence": "construction_issue_gate.json", "responsibleRole": "审核/批准", "action": "完成四级审签和当前快照修订"},
        {"category": "审签", "check": "当前快照审批有效", "status": "pass" if review.get("approvalValid") else "pending", "evidence": "review_workflow.json", "responsibleRole": "设计/校核/审核/批准", "action": "按岗位分离完成审签"},
        {"category": "钢筋", "check": "加工与净距门禁", "status": "generated" if rebar else "fail", "evidence": "钢筋深化ZIP/检查表", "responsibleRole": "结构设计/翻样", "action": "处理加工硬失败、截断和人工复核"},
        {"category": "BIM", "check": "IFC多配置及兼容性", "status": "generated" if include_ifc_profiles else "skipped", "evidence": "20_bim", "responsibleRole": "BIM协调", "action": "在目标软件中执行导入回归"},
        {"category": "现场", "check": "周边环境、施工组织和监测条件", "status": "manual_review", "evidence": "项目输入/专项方案", "responsibleRole": "项目总工/施工单位", "action": "核实坡道、出土口、管线、邻建、吊装和监测"},
        {"category": "追溯", "check": "图纸—模型—计算—规范映射", "status": "generated" if formal else "fail", "evidence": "10_drawings/quick_review/drawing_model_calculation_standard_matrix.csv", "responsibleRole": "设计/校核", "action": "抽查控制构件和图纸索引是否引用同一快照"},
        {"category": "拓扑", "check": "支撑交点与墙上汇交节点追溯", "status": "generated" if formal else "fail", "evidence": "10_drawings/quick_review/support_junction_schedule.csv", "responsibleRole": "设计/校核", "action": "确认非法穿越为零，并复核墙上多杆汇交节点的承压和施工空间"},
        {"category": "围护墙", "check": "计算墙—施工槽段—钢筋笼一致性", "status": "generated" if formal else "fail", "evidence": "10_drawings/quick_review/wall_panel_cage_traceability.csv", "responsibleRole": "结构/BIM/翻样", "action": "核对设计长度、槽段分幅、墙趾分区、钢筋笼和IFC对象ID"},
        {"category": "完整性", "check": "交付文件哈希与离线校验", "status": "generated", "evidence": "90_audit/SHA256SUMS.txt + verify_delivery_package.py", "responsibleRole": "项目管理/归档", "action": "移交后运行校验脚本确认文件未被修改"},
    ]
    if failures:
        acceptance.append({"category": "生成", "check": "成果生成失败项", "status": "fail", "evidence": json.dumps(failures, ensure_ascii=False), "responsibleRole": "系统管理员/设计", "action": "修复生成错误后重新导出"})

    hard_failed = any(str(item.get("status")) == "fail" for item in acceptance)
    release_grade = "construction_issued" if issue_mode == "construction" and not hard_failed and review.get("approvalValid") else "review_complete" if not hard_failed else "development_only"
    _write_relationship_matrix(root, artifacts)
    transmittal = root / "00_release/issue_transmittal.csv"
    with transmittal.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["project_id", "project_name", "issue_mode", "release_grade", "snapshot_hash", "generated_at", "drawing_status", "engineering_status", "approval_valid"])
        writer.writerow([project.id, project.name, issue_mode, release_grade, project_snapshot_hash(project), datetime.now(timezone.utc).isoformat(), sheet_quality.get("status"), assurance.get("engineeringCheckStatus"), review.get("approvalValid")])

    manifest = {
        "packageType": "PitGuard coordinated engineering deliverables",
        "projectId": project.id,
        "projectName": project.name,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "issueMode": issue_mode,
        "rebarMode": rebar_mode,
        "releaseGrade": release_grade,
        "snapshotHash": project_snapshot_hash(project),
        "engineeringStatus": assurance.get("engineeringCheckStatus"),
        "officialIssueGateAllowed": assurance.get("officialIssueGateAllowed"),
        "review": review,
        "drawingIssueGate": drawing_gate,
        "drawingSheetQuality": {k: v for k, v in sheet_quality.items() if k != "sheets"},
        "drawingCompleteness": {k: v for k, v in drawing_completeness.items() if k != "checks"},
        "failures": failures,
        "outputCategories": {
            "humanReview": ["10_drawings", "30_reports", "40_rebar/rebar_detailing_schedules.xlsx"],
            "machineExchange": ["20_bim", "40_rebar", "50_data/project_snapshot.json"],
            "releaseControl": ["00_release", "90_audit"],
        },
        "artifactCount": len(artifacts),
        "artifacts": artifacts,
        "acceptance": acceptance,
        **version_manifest(),
        "boundary": "交付包统一组织图纸、计算书、IFC、钢筋深化和项目数据。正式施工效力仍取决于当前快照审签、项目级规范、专项施工方案和注册工程师复核。",
    }
    (root / "00_release/release_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_registers(root, artifacts, acceptance)
    readme = root / "00_release/README_交付包使用说明.md"
    readme.write_text(
        f"# {project.name} · PitGuard V{SOFTWARE_VERSION} 协同成果交付包\n\n"
        f"- 发行模式：{issue_mode}\n- 发行等级：{release_grade}\n- 设计快照：`{manifest['snapshotHash']}`\n\n"
        "## 使用顺序\n\n1. 打开 `00_release/index.html` 查看成果和验收矩阵。\n"
        "2. 在 `10_drawings` 审查CAD/PDF、修订、逐图质量和施工图门禁。\n"
        "3. 在 `30_reports` 复核计算书、规范映射和控制结果。\n"
        "4. 在 `20_bim` 使用对应IFC配置进行协调、分析交换或施工可视化。\n"
        "5. 在 `40_rebar` 处理钢筋加工、净距、套筒、吊装和人工复核项。\n"
        "6. `50_data/project_snapshot.json` 用于归档、迁移和二次开发。\n"
        "7. `90_audit/verify_delivery_package.py` 用于离线校验全部文件哈希。\n\n"
        "审查版不得直接用于施工。施工版必须完成四级审签、当前快照修订以及现场条件复核。\n",
        encoding="utf-8",
    )
    _write_index(root, project, manifest)

    _write_verifier(root)
    checksums = root / "90_audit/SHA256SUMS.txt"
    hashes = []
    for file in sorted(root.rglob("*")):
        if file.is_file() and file != checksums:
            hashes.append(f"{_sha256(file)}  {file.relative_to(root).as_posix()}")
    checksums.write_text("\n".join(hashes) + "\n", encoding="utf-8")

    zip_path = out / f"{project.id}_coordinated_delivery_{issue_mode}_v{SOFTWARE_VERSION.replace('.', '_')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(root).as_posix())
    return zip_path
