from app.schemas.domain import Project
from app.services.benchmark_cases import list_benchmark_cases, run_benchmark_case_isolated as run_benchmark_case
from app.services.rebar_detailing import build_rebar_detailing


def test_v2_3_0_benchmark_catalog_has_public_sources():
    cases = list_benchmark_cases()
    assert len(cases) >= 5
    assert all(item["sourceUrl"].startswith("http") for item in cases)
    assert all(item["publicDataBasis"] for item in cases)


def test_v2_3_0_normative_benchmark_case_runs_end_to_end():
    result = run_benchmark_case("SH-56M-CIRCULAR-DOUBLE-WALL", persist=False)
    assert result["supportCount"] > 0
    assert result["traceCount"] > 0
    assert result["project"]["calculationResults"]


def test_v2_3_0_rebar_detailing_schedule_from_benchmark():
    result = run_benchmark_case("URBAN-TOPDOWN-32M-WALL-5SUPPORT", persist=False)
    project = Project.model_validate(result["project"])
    detailing = build_rebar_detailing(project)
    assert detailing["summary"]["barMarkCount"] > 0
    assert detailing["summary"]["totalWeightKg"] > 0
