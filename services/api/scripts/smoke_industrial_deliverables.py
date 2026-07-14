from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from app.ifc.exporter import export_simplified_ifc
from app.reports.docx_report import export_docx_report
from app.services.benchmark_cases import BENCHMARK_CASES, build_benchmark_project
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.rebar_export import export_rebar_detailing_package


def main() -> int:
    project = build_benchmark_project(BENCHMARK_CASES[0])
    latest = project.calculation_results[-1]
    project.design_settings.reinforcement_full_geometry_max_bars = 12000
    if not verify_current_calculation_contract(project, latest)["current"]:
        raise RuntimeError("Export-only LOD setting unexpectedly invalidated calculation contract")

    output_dir = Path(tempfile.mkdtemp(prefix="pitguard-v324-delivery-smoke-"))
    ifc_path = export_simplified_ifc(project, output_dir, "design_detailed")
    ifc_manifest = json.loads(ifc_path.with_suffix(".ifc_manifest.json").read_text(encoding="utf-8"))
    if ifc_manifest["calculationBaseline"]["resultHash"] != latest.result_hash:
        raise RuntimeError("IFC result hash does not match calculation baseline")

    report_path = export_docx_report(project, output_dir)
    with zipfile.ZipFile(report_path) as report_zip:
        document_xml = report_zip.read("word/document.xml").decode("utf-8")
    if "工业计算质量包" not in document_xml or str(latest.calculation_contract_id) not in document_xml:
        raise RuntimeError("Calculation report does not contain the industrial calculation baseline")

    rebar_path = export_rebar_detailing_package(project, output_dir, mode="balanced")
    with zipfile.ZipFile(rebar_path) as package:
        manifest_name = next(name for name in package.namelist() if name.endswith("package_manifest.json"))
        compact_name = next(name for name in package.namelist() if name.endswith("00_machine_data/rebar_detailing_full.json"))
        geometry_name = next(name for name in package.namelist() if name.endswith("00_machine_data/individual_rebar_geometry.json"))
        manifest = json.loads(package.read(manifest_name))
        compact = json.loads(package.read(compact_name))
        if package.getinfo(geometry_name).file_size <= 0:
            raise RuntimeError("Individual rebar geometry file is empty")
    if manifest["calculationBaseline"]["calculationContractId"] != latest.calculation_contract_id:
        raise RuntimeError("Rebar package contract ID does not match calculation baseline")
    if manifest["calculationBaseline"]["resultHash"] != latest.result_hash:
        raise RuntimeError("Rebar package result hash does not match calculation baseline")
    if "individualBars" in compact:
        raise RuntimeError("Compact rebar payload still duplicates the individual-bar array")

    print(json.dumps({
        "status": "pass",
        "calculationContractId": latest.calculation_contract_id,
        "resultHash": latest.result_hash,
        "ifc": ifc_path.name,
        "report": report_path.name,
        "rebar": rebar_path.name,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
