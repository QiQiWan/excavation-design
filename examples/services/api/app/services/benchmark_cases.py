from __future__ import annotations

import json
import math
import zipfile
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project
from app.drawings.cad_export import export_construction_cad_package, export_construction_svg_package
from app.geology.model_builder import build_geological_model_from_boreholes, ensure_geological_model_covers_excavation
from app.ifc.exporter import export_simplified_ifc
from app.reports.docx_report import export_docx_report
from app.schemas.domain import (
    Borehole, BoreholeLayer, DesignSettings, ExcavationModel, Point2D, Polyline2D, Project, SoilParameters, Stratum, SupportLayoutRepairSummary,
)
from app.services.calculation_trace import build_calculation_trace
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.issue_center import build_issue_center
from app.services.support_layout_repair import auto_repair_support_layout
from app.storage.repository import ProjectRepository


@dataclass(frozen=True)
class BenchmarkCaseSpec:
    case_id: str
    name: str
    source_title: str
    source_url: str
    public_data_basis: str
    length_m: float
    width_m: float
    depth_m: float
    wall_depth_m: float | None
    support_levels: int | None
    soil_profile: str
    groundwater_m: float
    surcharge_kpa: float
    geometry: str = "rectangular"
    notes: str = ""


BENCHMARK_CASES: list[BenchmarkCaseSpec] = [
    BenchmarkCaseSpec(
        case_id="HZ-30M-SOFT-CLAY-9310",
        name="杭州软土 30.2m 深大型地下室基坑规范算法回归算例",
        source_title="Observed performance / Performance of diaphragm walls in a 30.2 m deep Hangzhou soft-clay basement excavation",
        source_url="https://link.springer.com/article/10.1007/s11204-022-09797-5",
        public_data_basis="公开摘要给出：约 9310 m2、开挖深度 30.2 m、地连墙约 51/52 m、六道混凝土支撑。",
        length_m=122.0,
        width_m=76.0,
        depth_m=30.2,
        wall_depth_m=52.0,
        support_levels=6,
        soil_profile="hangzhou_soft_clay",
        groundwater_m=-1.2,
        surcharge_kpa=25.0,
    ),
    BenchmarkCaseSpec(
        case_id="SH-ULTRA-LARGE-ZONED-70500",
        name="上海软土超大面积分区开挖基坑规范算法回归算例",
        source_title="Observed Performance of an Ultra Large Deep Excavation in Shanghai Soft Clay",
        source_url="https://link.springer.com/chapter/10.1007/978-981-13-0011-0_18",
        public_data_basis="公开摘要给出：约 70500 m2、约 340 m × 200 m、开挖深度 10.3–15.9 m、三分区开挖。",
        length_m=340.0,
        width_m=200.0,
        depth_m=15.9,
        wall_depth_m=None,
        support_levels=3,
        soil_profile="shanghai_soft_clay",
        groundwater_m=-1.0,
        surcharge_kpa=20.0,
        notes="以最大公开深度 15.9 m 作为保守回归算例；系统按矩形主坑 + 出土口障碍建模。",
    ),
    BenchmarkCaseSpec(
        case_id="SH-31P5-PASSAGEWAY-DEWATERING",
        name="上海 31.5m 超深通道基坑降水耦合作用回归算例",
        source_title="Field tests on performance of diaphragm wall for a 31.5 m-deep passageway excavation in Shanghai downtown",
        source_url="https://cgejournal.com/en/article/doi/10.11779/CJGE20230760",
        public_data_basis="公开摘要给出：31.5 m 深通道工程，长深比和长宽比较小，坑角效应和超前预降水影响显著。",
        length_m=72.0,
        width_m=28.0,
        depth_m=31.5,
        wall_depth_m=None,
        support_levels=7,
        soil_profile="shanghai_soft_clay_dewatering",
        groundwater_m=-0.8,
        surcharge_kpa=30.0,
        notes="平面尺寸为基于公开描述构建的通道型归一化算例，不代表原工程精确图纸。",
    ),
    BenchmarkCaseSpec(
        case_id="SH-56M-CIRCULAR-DOUBLE-WALL",
        name="上海 56m 深圆形/多边形竖井双墙体系规范算法回归算例",
        source_title="Performance of a 56 m deep circular excavation supported by diaphragm and cut-off double-wall system in Shanghai soft ground",
        source_url="https://www.sciencedirect.com/org/science/article/abs/pii/S0008367422000555",
        public_data_basis="公开摘要给出：56 m 深圆形基坑，内圆形地下连续墙 + 外矩形止水墙双墙体系，上海软土。",
        length_m=76.0,
        width_m=76.0,
        depth_m=56.0,
        wall_depth_m=None,
        support_levels=9,
        soil_profile="shanghai_deep_soft_clay_confined_water",
        groundwater_m=-0.5,
        surcharge_kpa=35.0,
        geometry="octagonal",
        notes="因当前规范算法主流程面向多边形基坑，圆形竖井采用 八边形近似。",
    ),
    BenchmarkCaseSpec(
        case_id="URBAN-TOPDOWN-32M-WALL-5SUPPORT",
        name="邻近建筑影响的 32m 地连墙五道支撑顶顺结合基坑算例",
        source_title="Evaluating the Effects of Deep Excavation on Nearby Structures",
        source_url="https://www.mdpi.com/2076-3417/14/21/10002",
        public_data_basis="公开摘要给出：32 m 深、0.8 m 厚地下连续墙，顶顺结合法，六次开挖五道支撑。",
        length_m=96.0,
        width_m=48.0,
        depth_m=26.0,
        wall_depth_m=32.0,
        support_levels=5,
        soil_profile="urban_adjacent_buildings",
        groundwater_m=-2.0,
        surcharge_kpa=45.0,
        notes="深度按 32m 墙深和五道支撑构建规范算法测试坑，包含邻近建筑附加堆载。",
    ),
]


def _case_spec_json(spec: BenchmarkCaseSpec) -> dict[str, Any]:
    return {
        "caseId": spec.case_id,
        "name": spec.name,
        "sourceTitle": spec.source_title,
        "sourceUrl": spec.source_url,
        "publicDataBasis": spec.public_data_basis,
        "lengthM": spec.length_m,
        "widthM": spec.width_m,
        "depthM": spec.depth_m,
        "calculationDepthM": _calculation_depth(spec),
        "wallDepthM": spec.wall_depth_m,
        "supportLevels": spec.support_levels,
        "soilProfile": spec.soil_profile,
        "groundwaterM": spec.groundwater_m,
        "surchargeKpa": spec.surcharge_kpa,
        "geometry": spec.geometry,
        "notes": spec.notes,
    }


def list_benchmark_cases() -> list[dict[str, Any]]:
    return [_case_spec_json(spec) for spec in BENCHMARK_CASES]




def _calculation_dimensions(spec: BenchmarkCaseSpec) -> tuple[float, float, float]:
    """Return bounded regression dimensions while preserving published aspect ratio.

    Public papers often describe very large pits.  The benchmark library is used as
    an automated normative-algorithm regression suite, so long-span pits are scaled
    to a computationally bounded model but keep source dimensions in the metadata.
    """
    max_dim = 90.0
    scale = min(1.0, max_dim / max(spec.length_m, spec.width_m))
    return round(spec.length_m * scale, 3), round(spec.width_m * scale, 3), round(scale, 4)



def _calculation_depth(spec: BenchmarkCaseSpec) -> float:
    """Return bounded excavation depth for fast normative-regression runs.

    The public case metadata keeps the original reported depth, while the
    automated benchmark uses a capped depth to avoid excessive rule-based
    member enumeration and very long CI/runtime cycles.
    """
    cap = 30.0 if spec.geometry == "octagonal" else 28.0
    return round(min(float(spec.depth_m), cap), 3)


def _soil_layers(profile: str) -> list[tuple[str, str, float, SoilParameters]]:
    if profile.startswith("shanghai"):
        return [
            ("1", "填土/粉质黏土", 3.0, SoilParameters(unit_weight=18.2, saturated_unit_weight=19.0, cohesion=12, friction_angle=18, elastic_modulus=8, permeability_x=1e-6, horizontal_subgrade_modulus=8000)),
            ("2", "淤泥质黏土", 10.0, SoilParameters(unit_weight=17.4, saturated_unit_weight=18.2, cohesion=9, friction_angle=12, elastic_modulus=5, permeability_x=5e-7, horizontal_subgrade_modulus=4500)),
            ("3", "软塑黏土", 15.0, SoilParameters(unit_weight=17.8, saturated_unit_weight=18.8, cohesion=14, friction_angle=15, elastic_modulus=7, permeability_x=7e-7, horizontal_subgrade_modulus=6500)),
            ("4", "粉砂夹粉土", 18.0, SoilParameters(unit_weight=19.2, saturated_unit_weight=20.0, cohesion=3, friction_angle=28, elastic_modulus=18, permeability_x=2e-5, horizontal_subgrade_modulus=16000)),
            ("5", "密实砂土/承压含水层", 35.0, SoilParameters(unit_weight=19.8, saturated_unit_weight=20.5, cohesion=0, friction_angle=32, elastic_modulus=30, permeability_x=8e-5, horizontal_subgrade_modulus=30000)),
        ]
    if profile == "hangzhou_soft_clay":
        return [
            ("1", "杂填土", 2.5, SoilParameters(unit_weight=18.0, saturated_unit_weight=18.8, cohesion=10, friction_angle=18, elastic_modulus=8, permeability_x=1e-6, horizontal_subgrade_modulus=7000)),
            ("2", "淤泥质粉质黏土", 8.0, SoilParameters(unit_weight=17.2, saturated_unit_weight=18.0, cohesion=8, friction_angle=11, elastic_modulus=4.5, permeability_x=4e-7, horizontal_subgrade_modulus=3800)),
            ("3", "淤泥质黏土", 14.0, SoilParameters(unit_weight=17.0, saturated_unit_weight=17.8, cohesion=9, friction_angle=10, elastic_modulus=4.0, permeability_x=3e-7, horizontal_subgrade_modulus=3600)),
            ("4", "粉质黏土", 16.0, SoilParameters(unit_weight=18.4, saturated_unit_weight=19.2, cohesion=18, friction_angle=18, elastic_modulus=10, permeability_x=1e-6, horizontal_subgrade_modulus=10000)),
            ("5", "粉砂/圆砾", 35.0, SoilParameters(unit_weight=19.6, saturated_unit_weight=20.2, cohesion=2, friction_angle=31, elastic_modulus=35, permeability_x=6e-5, horizontal_subgrade_modulus=36000)),
        ]
    return [
        ("1", "填土", 3.0, SoilParameters(unit_weight=18.0, saturated_unit_weight=18.8, cohesion=10, friction_angle=18, elastic_modulus=8, horizontal_subgrade_modulus=7000)),
        ("2", "粉质黏土", 10.0, SoilParameters(unit_weight=18.5, saturated_unit_weight=19.2, cohesion=20, friction_angle=20, elastic_modulus=12, horizontal_subgrade_modulus=11000)),
        ("3", "砂土", 40.0, SoilParameters(unit_weight=19.5, saturated_unit_weight=20.2, cohesion=2, friction_angle=32, elastic_modulus=30, horizontal_subgrade_modulus=30000)),
    ]


def _make_strata(profile: str) -> list[Stratum]:
    return [Stratum(code=code, name=name, parameters=params, confidence="medium", parameter_source="empirical") for code, name, _, params in _soil_layers(profile)]


def _make_boreholes(spec: BenchmarkCaseSpec) -> list[Borehole]:
    layers_def = _soil_layers(spec.soil_profile)
    length_m, width_m, _scale = _calculation_dimensions(spec)
    pts = [(0.0, 0.0), (length_m, 0.0), (length_m, width_m), (0.0, width_m), (length_m / 2, width_m / 2)]
    boreholes: list[Borehole] = []
    for idx, (x, y) in enumerate(pts, start=1):
        top_depth = 0.0
        layers: list[BoreholeLayer] = []
        for code, name, thick, _params in layers_def:
            bottom_depth = min(top_depth + thick, max(_calculation_depth(spec) + 30.0, 60.0))
            layers.append(BoreholeLayer(stratum_code=code, stratum_name=name, top_depth=top_depth, bottom_depth=bottom_depth, top_elevation=-top_depth, bottom_elevation=-bottom_depth, description=f"benchmark {spec.case_id}"))
            top_depth = bottom_depth
            if top_depth >= max(_calculation_depth(spec) + 30.0, 60.0):
                break
        boreholes.append(Borehole(code=f"BH-{idx:02d}", x=x, y=y, collar_elevation=0.0, depth=max(_calculation_depth(spec) + 30.0, 60.0), layers=layers, source_file=f"benchmark:{spec.case_id}"))
    return boreholes


def _outline(spec: BenchmarkCaseSpec) -> Polyline2D:
    length_m, width_m, _scale = _calculation_dimensions(spec)
    if spec.geometry == "octagonal":
        cx, cy = length_m / 2.0, width_m / 2.0
        r = min(length_m, width_m) / 2.0
        pts = [Point2D(x=cx + r * math.cos(2 * math.pi * i / 8), y=cy + r * math.sin(2 * math.pi * i / 8)) for i in range(8)]
    else:
        pts = [Point2D(x=0, y=0), Point2D(x=length_m, y=0), Point2D(x=length_m, y=width_m), Point2D(x=0, y=width_m)]
    return Polyline2D(points=pts, closed=True)


def build_benchmark_project(spec: BenchmarkCaseSpec) -> Project:
    project = Project(
        name=spec.name,
        location="public-paper-derived benchmark",
        design_settings=DesignSettings(groundwater_level=spec.groundwater_m, surcharge=spec.surcharge_kpa, default_support_spacing=5.0),
    )
    project.boreholes = _make_boreholes(spec)
    project.strata = _make_strata(spec.soil_profile)
    project.geological_model = build_geological_model_from_boreholes(project.boreholes, grid_size=20.0)
    calc_depth = _calculation_depth(spec)
    excavation = make_excavation_model("Main benchmark excavation", _outline(spec), 0.0, -calc_depth, 0.5)
    # Add construction access/adjacent-building proxies for workflow coverage.
    length_m, width_m, scale = _calculation_dimensions(spec)
    if spec.case_id.startswith("SH-ULTRA"):
        from app.schemas.domain import ConstructionObstacle
        excavation.obstacles.append(ConstructionObstacle(name="Zone-I/II muck-out corridor", obstacle_type="muck_out_opening", center=Point2D(x=length_m*0.5, y=width_m*0.52), width=max(8.0, 28.0*scale), length=width_m*0.72, clearance=2.0, active=True, note="zoned excavation access path"))
    if "ADJACENT" in spec.case_id or "URBAN" in spec.case_id:
        from app.schemas.domain import ConstructionObstacle
        excavation.obstacles.append(ConstructionObstacle(name="adjacent-building protection zone", obstacle_type="protected_zone", center=Point2D(x=length_m*0.5, y=-8.0), width=length_m*0.8, length=10.0, clearance=3.0, active=True, note="neighboring structure influence proxy"))
    if scale < 0.999:
        project.messages.append(f"Benchmark geometry scaled from public plan {spec.length_m}x{spec.width_m}m to {length_m}x{width_m}m for automated normative regression; source dimensions remain recorded.")
    if calc_depth < spec.depth_m:
        project.messages.append(f"Benchmark depth capped from public depth {spec.depth_m}m to {calc_depth}m for fast normative regression; source depth remains recorded.")
    project.excavation = excavation
    ensure_geological_model_covers_excavation(project)
    project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system)
    if spec.wall_depth_m and project.retaining_system:
        effective_wall_depth = min(float(spec.wall_depth_m), calc_depth + 24.0)
        for wall in project.retaining_system.diaphragm_walls:
            wall.bottom_elevation = -effective_wall_depth
    project.retaining_system = auto_supports(project.excavation, project.retaining_system)
    if spec.support_levels and project.retaining_system:
        # Keep algorithmic geometry; record public support-level target in summary for traceability.
        project.retaining_system.layout_summary["publicSupportLevelTarget"] = spec.support_levels
        project.retaining_system.layout_summary["publicDataBasis"] = spec.public_data_basis
    if project.retaining_system:
        project.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
            optimization_method="deterministic_normative_benchmark_layout",
            optimization_phase="V2.3.0 public-paper-derived regression suite",
            status="manual_review",
            summary="Benchmark mode uses deterministic normative layout for fast regression. Interactive candidate optimization remains available in normal project workflows.",
            actions=[{
                "action": "benchmark_deterministic_layout",
                "description": "Skipped expensive candidate optimization in benchmark generation; calculation, trace, issue center, IFC, CAD, SVG and DOCX export still run end to end.",
            }],
        )
    project.calculation_cases = build_default_construction_cases(project)
    result = run_calculation(project, project.calculation_cases[-1] if project.calculation_cases else None)
    project.calculation_results.append(result)
    # Candidate A/B/C complete comparison is intentionally skipped in the benchmark
    # library to keep automated normative regression fast and deterministic.
    # The main project workflow still supports candidate comparison through tasks.
    project.messages.append("candidate comparison skipped in benchmark mode for speed; main workflow remains enabled")
    project.messages.append(json.dumps({"benchmarkCaseId": spec.case_id, "sourceTitle": spec.source_title, "sourceUrl": spec.source_url, "publicDataBasis": spec.public_data_basis, "notes": spec.notes}, ensure_ascii=False))
    return project


def run_benchmark_case(case_id: str, repo: ProjectRepository | None = None, persist: bool = True) -> dict[str, Any]:
    spec = next((c for c in BENCHMARK_CASES if c.case_id == case_id), None)
    if spec is None:
        raise ValueError(f"Unknown benchmark case: {case_id}")
    project = build_benchmark_project(spec)
    if persist and repo is not None:
        repo.create(project)
    issues = build_issue_center(project)
    trace = build_calculation_trace(project)
    latest = project.calculation_results[-1] if project.calculation_results else None
    return {
        "caseId": spec.case_id,
        "projectId": project.id,
        "name": spec.name,
        "sourceTitle": spec.source_title,
        "sourceUrl": spec.source_url,
        "publicDataBasis": spec.public_data_basis,
        "depthM": spec.depth_m,
        "calculationDepthM": _calculation_depth(spec),
        "sourcePlanSizeM": [spec.length_m, spec.width_m],
        "planSizeM": list(_calculation_dimensions(spec)[:2]),
        "geometryScale": _calculation_dimensions(spec)[2],
        "supportCount": len(project.retaining_system.supports) if project.retaining_system else 0,
        "columnCount": len(project.retaining_system.columns) if project.retaining_system else 0,
        "maxSupportAxialForce": latest.governing_values.max_support_axial_force if latest else None,
        "maxWallMoment": latest.governing_values.max_wall_moment if latest else None,
        "maxDisplacement": latest.governing_values.max_displacement if latest else None,
        "checkSummary": latest.check_summary if latest else {},
        "issueSummary": issues.get("summary", {}),
        "traceCount": trace.get("summary", {}).get("traceCount", 0),
        "officialIssueAllowed": issues.get("officialIssueAllowed", False),
        "project": project.model_dump(mode="json", by_alias=True),
    }


def _run_benchmark_case_isolated(case_id: str, timeout_s: int = 180) -> dict[str, Any]:
    """Run a benchmark case in a clean Python process.

    Some large generated projects stress Pydantic object graphs and rule-based
    support enumeration when many cases are run sequentially in one interpreter.
    Isolation keeps the regression library deterministic and prevents global
    state from one public-paper case affecting the next case.
    """
    root = Path(__file__).resolve().parents[3]
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from app.services.benchmark_cases import run_benchmark_case\n"
        "result = run_benchmark_case(sys.argv[1], repo=None, persist=False)\n"
        "Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')\n"
    )
    env = os.environ.copy()
    api_root = str(root)
    env["PYTHONPATH"] = api_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with tempfile.NamedTemporaryFile(prefix=f"pitguard_{case_id}_", suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with tempfile.NamedTemporaryFile(prefix=f"pitguard_{case_id}_", suffix=".err", delete=False) as err:
        err_path = Path(err.name)
    try:
        with err_path.open("w", encoding="utf-8") as err_file:
            completed = subprocess.run([sys.executable, "-c", code, case_id, str(tmp_path)], stdout=subprocess.DEVNULL, stderr=err_file, timeout=timeout_s, env=env)
        if completed.returncode != 0:
            stderr_text = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
            raise RuntimeError(f"Benchmark case {case_id} failed in isolated runner: {stderr_text[-2000:]}")
        return json.loads(tmp_path.read_text(encoding="utf-8"))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            err_path.unlink(missing_ok=True)
        except Exception:
            pass



def run_benchmark_case_isolated(
    case_id: str,
    repo: ProjectRepository | None = None,
    persist: bool = False,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Public deterministic runner for repeated regression and CI execution.

    Persistence is intentionally rejected because the isolated child has no
    access to the caller's repository object. Call ``run_benchmark_case`` when
    persistence is required.
    """
    if persist or repo is not None:
        raise ValueError("Isolated benchmark execution does not support persistence.")
    return _run_benchmark_case_isolated(case_id, timeout_s=timeout_s)


def _run_benchmark_case_pool_worker(case_id: str) -> dict[str, Any]:
    return run_benchmark_case(case_id, repo=None, persist=False)


def run_all_benchmarks(repo: ProjectRepository | None = None, persist: bool = False) -> dict[str, Any]:
    if repo is None and not persist:
        # Run public-paper-derived cases in parallel isolated processes.  This is
        # faster and avoids accumulated object-graph state from one large
        # generated case affecting the next case.
        cases_by_id: dict[str, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=min(5, len(BENCHMARK_CASES))) as executor:
            future_map = {executor.submit(_run_benchmark_case_pool_worker, spec.case_id): spec.case_id for spec in BENCHMARK_CASES}
            for future in as_completed(future_map):
                result = future.result()
                cases_by_id[str(result["caseId"])] = result
        cases = [cases_by_id[spec.case_id] for spec in BENCHMARK_CASES]
    else:
        cases = [run_benchmark_case(spec.case_id, repo=repo, persist=persist) for spec in BENCHMARK_CASES]
    return {
        "benchmarkVersion": "V2.3.0-public-paper-derived-normative-regression",
        "caseCount": len(cases),
        "cases": cases,
        "notes": [
            "Cases are public-paper-derived regression examples for normative-algorithm workflow coverage; they are not original project drawings.",
            "Finite-element solvers are intentionally not used in this benchmark set.",
            "Multi-case runs use an isolated runner to keep regression timing deterministic.",
        ],
    }


def _export_single_benchmark_case_worker(args: tuple[str, str]) -> dict[str, Any]:
    case_id, package_dir_raw = args
    package_dir = Path(package_dir_raw)
    case = run_benchmark_case(case_id, repo=None, persist=False)
    case_dir = package_dir / case["caseId"]
    case_dir.mkdir(parents=True, exist_ok=True)
    project = Project.model_validate(case["project"])
    (case_dir / "project.json").write_text(json.dumps(case["project"], ensure_ascii=False, indent=2), encoding="utf-8")
    (case_dir / "issues.json").write_text(json.dumps(build_issue_center(project), ensure_ascii=False, indent=2), encoding="utf-8")
    (case_dir / "calculation_trace.json").write_text(json.dumps(build_calculation_trace(project), ensure_ascii=False, indent=2), encoding="utf-8")
    export_construction_cad_package(project, case_dir)
    export_construction_svg_package(project, case_dir)
    export_docx_report(project, case_dir)
    export_simplified_ifc(project, case_dir, export_mode="construction_visual")
    summary = {k: v for k, v in case.items() if k != "project"}
    (case_dir / "case_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def export_benchmark_package(output_dir: str | Path, repo: ProjectRepository | None = None, persist: bool = False) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    package_dir = out / "pitguard_v2_3_0_benchmark_cases"
    if package_dir.exists():
        import shutil
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    if repo is None and not persist:
        case_summaries_by_id: dict[str, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=min(3, len(BENCHMARK_CASES))) as executor:
            future_map = {executor.submit(_export_single_benchmark_case_worker, (spec.case_id, str(package_dir))): spec.case_id for spec in BENCHMARK_CASES}
            for future in as_completed(future_map):
                summary_case = future.result()
                case_summaries_by_id[str(summary_case["caseId"])] = summary_case
        cases = [case_summaries_by_id[spec.case_id] for spec in BENCHMARK_CASES]
    else:
        cases = []
        for spec in BENCHMARK_CASES:
            case = run_benchmark_case(spec.case_id, repo=repo, persist=persist)
            case_dir = package_dir / case["caseId"]
            case_dir.mkdir(exist_ok=True)
            project = Project.model_validate(case["project"])
            (case_dir / "project.json").write_text(json.dumps(case["project"], ensure_ascii=False, indent=2), encoding="utf-8")
            (case_dir / "issues.json").write_text(json.dumps(build_issue_center(project), ensure_ascii=False, indent=2), encoding="utf-8")
            (case_dir / "calculation_trace.json").write_text(json.dumps(build_calculation_trace(project), ensure_ascii=False, indent=2), encoding="utf-8")
            export_construction_cad_package(project, case_dir)
            export_construction_svg_package(project, case_dir)
            export_docx_report(project, case_dir)
            export_simplified_ifc(project, case_dir, export_mode="construction_visual")
            summary_case = {k: v for k, v in case.items() if k != "project"}
            (case_dir / "case_summary.json").write_text(json.dumps(summary_case, ensure_ascii=False, indent=2), encoding="utf-8")
            cases.append(summary_case)

    summary = {
        "benchmarkVersion": "V2.3.0-public-paper-derived-normative-regression",
        "caseCount": len(cases),
        "cases": cases,
        "notes": [
            "Cases are public-paper-derived regression examples for normative-algorithm workflow coverage; they are not original project drawings.",
            "Finite-element solvers are intentionally not used in this benchmark set.",
            "Geometry/depth may be normalized or capped for repeatable automated regression; source dimensions remain in metadata.",
        ],
    }
    (package_dir / "benchmark_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = ["case_id,name,source_depth_m,calculation_depth_m,length_m,width_m,support_count,column_count,max_support_axial_force,max_wall_moment,max_displacement,source_url"]
    for case in cases:
        rows.append(",".join(str(x).replace(",", ";") for x in [case["caseId"], case["name"], case["depthM"], case.get("calculationDepthM"), case["planSizeM"][0], case["planSizeM"][1], case["supportCount"], case["columnCount"], case["maxSupportAxialForce"], case["maxWallMoment"], case["maxDisplacement"], case["sourceUrl"]]))
    (package_dir / "benchmark_summary.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    readme = package_dir / "README.md"
    readme.write_text("# PitGuard V2.3.0 public-paper-derived benchmark cases\n\nThis package contains normative-algorithm regression cases derived from publicly described excavation projects. It intentionally avoids finite-element solvers. Each case includes project JSON, issue report, calculation trace, CAD/SVG drawings, DOCX report and construction-visual IFC. Geometry/depth may be normalized or capped for repeatable automated regression; source dimensions remain recorded in benchmark_summary.json.\n", encoding="utf-8")
    zip_path = out / "pitguard_v2_3_0_public_benchmark_cases.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in package_dir.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=str(file.relative_to(package_dir.parent)))
    return zip_path
