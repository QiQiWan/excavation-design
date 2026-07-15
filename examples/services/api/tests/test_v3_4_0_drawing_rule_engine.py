from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.drawing_rules import (
    build_drawing_plan,
    evaluate_drawing_issue_gate,
    get_effective_drawing_rule_set,
    normalize_drawing_rule_set,
    optimize_drawing_rule_set,
    validate_drawing_rule_set,
)
from app.drawings.cad_export import export_construction_cad_package
from app.schemas.domain import Project
from app.services.benchmark_cases import run_benchmark_case_isolated
from app.services.review_workflow import review_status


@pytest.fixture(scope="module")
def benchmark_project() -> Project:
    result = run_benchmark_case_isolated("URBAN-TOPDOWN-32M-WALL-5SUPPORT", persist=False)
    return Project.model_validate(result["project"])


def test_v3_4_rule_set_expands_levels_walls_and_selects_scales(benchmark_project: Project) -> None:
    rules = get_effective_drawing_rule_set(benchmark_project)
    plan = build_drawing_plan(benchmark_project, rules)
    levels = sorted({s.level_index for s in benchmark_project.retaining_system.supports})
    walls = benchmark_project.retaining_system.diaphragm_walls
    assert len([s for s in plan["sheets"] if s["renderer"] == "support_level_plan"]) == len(levels)
    assert len([s for s in plan["sheets"] if s["renderer"] == "single_wall_rebar_elevation"]) == len(walls)
    master = next(s for s in plan["sheets"] if s["sheetNo"] == "S-00")
    assert master["scale"] in {"1:100", "1:150", "1:200", "1:250", "1:300", "1:400", "1:500"}
    assert master["scaleDecision"]["mode"] == "auto-fit"
    assert plan["drawingRuleSetHash"] == rules["ruleSetHash"]
    assert plan["planHash"]


def test_v3_4_compact_and_construction_presets_produce_different_drawing_sets(benchmark_project: Project) -> None:
    compact = build_drawing_plan(benchmark_project, normalize_drawing_rule_set({"preset": "compact"}))
    construction = build_drawing_plan(benchmark_project, normalize_drawing_rule_set({"preset": "construction"}))
    assert compact["sheetCount"] < construction["sheetCount"]
    assert not any(s["renderer"] == "single_wall_rebar_elevation" for s in compact["sheets"])
    assert any(s["renderer"] == "single_wall_rebar_elevation" for s in construction["sheets"])
    compact_renderers = {s["renderer"] for s in compact["sheets"]}
    construction_renderers = {s["renderer"] for s in construction["sheets"]}
    assert "legacy_support_plan" not in compact_renderers
    assert "rebar_geometry_plan" not in compact_renderers
    assert {"legacy_support_plan", "rebar_geometry_plan"}.issubset(construction_renderers)


def test_v3_4_validation_rejects_unknown_renderer_unsafe_path_and_condition(benchmark_project: Project) -> None:
    rules = normalize_drawing_rule_set({"preset": "balanced"})
    broken = rules["sheetRules"][0]
    broken["renderer"] = "exec_arbitrary_script"
    broken["file"] = "../unsafe.dxf"
    broken["trigger"] = {"path": "facts.wallCount", "op": "python_eval", "value": "__import__('os')"}
    result = validate_drawing_rule_set(benchmark_project, rules)
    messages = " | ".join(x["message"] for x in result["errors"])
    assert result["valid"] is False
    assert "unknown renderer" in messages
    assert "relative safe path" in messages
    assert "unsupported operator" in messages


def test_v3_4_optimizer_ranks_reproducible_candidates(benchmark_project: Project) -> None:
    result = optimize_drawing_rule_set(benchmark_project)
    assert result["candidateCount"] >= 4
    assert result["recommendedCandidateId"] == result["candidates"][0]["candidateId"]
    assert [x["rank"] for x in result["candidates"]] == list(range(1, result["candidateCount"] + 1))
    assert all(0 <= x["score"] <= 100 for x in result["candidates"])
    assert all(x["ruleSetMeta"]["ruleSetHash"] for x in result["candidates"])
    assert result["candidatePayloadMode"] == "metadata-only"


def test_v3_4_issue_policy_can_tighten_but_not_bypass_engineering_gate(benchmark_project: Project) -> None:
    project = benchmark_project.model_copy(deep=True)
    project.drawing_rule_set = normalize_drawing_rule_set({
        "preset": "balanced",
        "issuePolicy": {"construction": {"requireApproval": False, "requireCurrentRevision": False, "requireCalculation": False}},
    })
    approval = review_status(project)
    blocked = evaluate_drawing_issue_gate(project, issue_mode="construction", engineering_gate_allowed=False, approval=approval, current_revision_valid=False)
    assert blocked["allowed"] is False
    assert any(x["code"] == "ENGINEERING_GATE_BLOCKED" for x in blocked["reasons"])
    relaxed = evaluate_drawing_issue_gate(project, issue_mode="construction", engineering_gate_allowed=True, approval=approval, current_revision_valid=False)
    assert relaxed["allowed"] is True
    project.design_settings.require_formal_approval_for_construction = True
    tightened = evaluate_drawing_issue_gate(project, issue_mode="construction", engineering_gate_allowed=True, approval=approval, current_revision_valid=False)
    assert tightened["allowed"] is False
    assert any(x["code"] == "APPROVAL_REQUIRED" for x in tightened["reasons"])


def test_v3_4_cad_package_contains_rule_set_decisions_and_respects_compact_preset(benchmark_project: Project, tmp_path: Path) -> None:
    project = benchmark_project.model_copy(deep=True)
    project.drawing_rule_set = normalize_drawing_rule_set({"preset": "compact"})
    path = export_construction_cad_package(project, tmp_path, scope="full", rebar_mode="balanced", issue_mode="review")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "drawing_rule_set.json" in names
        assert "90_schedules/drawing_rule_decisions.csv" in names
        assert "drawing_set_manifest.json" in names
        assert "S-08_individual_rebar_geometry.dxf" not in names
        manifest = json.loads(zf.read("drawing_set_manifest.json"))
        rules = json.loads(zf.read("drawing_rule_set.json"))
        package = json.loads(zf.read("drawing_package_manifest.json"))
        assert manifest["preset"] == "compact"
        assert manifest["drawingRuleSetHash"] == rules["ruleSetHash"]
        assert package["drawingRuleSet"]["hash"] == rules["ruleSetHash"]
        assert package["sheetCount"] == manifest["includedSheetCount"]


def test_v3_4_wall_elevations_can_be_grouped_by_config(benchmark_project: Project) -> None:
    import math
    rules = normalize_drawing_rule_set({"preset": "balanced", "parameters": {"wallSheetsPerDrawing": 2}})
    plan = build_drawing_plan(benchmark_project, rules)
    wall_sheets = [s for s in plan["sheets"] if s["renderer"] == "single_wall_rebar_elevation"]
    wall_count = len(benchmark_project.retaining_system.diaphragm_walls)
    assert len(wall_sheets) == math.ceil(wall_count / 2)
    assert all(1 <= len(sheet["variables"]["wall_ids"]) <= 2 for sheet in wall_sheets)
    assert sum(len(sheet["variables"]["wall_ids"]) for sheet in wall_sheets) == wall_count


def test_v3_4_optimizer_preserves_project_specific_rule_edits(benchmark_project: Project) -> None:
    rules = normalize_drawing_rule_set({"preset": "balanced"})
    target = next(item for item in rules["sheetRules"] if item["id"] == "R05")
    target["enabled"] = False
    result = optimize_drawing_rule_set(benchmark_project, {"ruleSet": rules, "paperSizes": ["A1"], "wallSheetsPerDrawing": [1, 2], "includeRuleSets": True})
    custom = [candidate for candidate in result["candidates"] if candidate.get("source") == "project-current"]
    assert custom
    assert all(next(item for item in candidate["ruleSet"]["sheetRules"] if item["id"] == "R05")["enabled"] is False for candidate in custom)
    assert {candidate.get("wallSheetsPerDrawing") for candidate in custom} == {1, 2}


def test_v3_4_external_rule_pack_can_add_enterprise_preset(benchmark_project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.drawing_rules.engine import get_preset_rule_set, list_drawing_rule_presets
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    rules = normalize_drawing_rule_set({"preset": "balanced", "id": "enterprise-standard", "name": "Enterprise Standard"})
    rules["preset"] = "enterprise-standard"
    (preset_dir / "enterprise-standard.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"packageId": "enterprise-rules"}), encoding="utf-8")
    monkeypatch.setenv("PITGUARD_DRAWING_RULE_DIR", str(tmp_path))
    loaded = get_preset_rule_set("enterprise-standard")
    assert loaded["id"] == "enterprise-standard"
    assert loaded["sourcePackageId"] == "enterprise-rules"
    assert any(item["id"] == "enterprise-standard" for item in list_drawing_rule_presets())


def test_v3_4_validation_rejects_unknown_schema_and_context_root(benchmark_project: Project) -> None:
    rules = normalize_drawing_rule_set({"preset": "balanced"})
    rules["schemaVersion"] = "9.9"
    rules["sheetRules"][0]["trigger"] = {"path": "os.environ", "op": "exists"}
    result = validate_drawing_rule_set(benchmark_project, rules)
    messages = " | ".join(item["message"] for item in result["errors"])
    assert "unsupported schema version" in messages
    assert "unsupported context root" in messages


def test_v3_4_required_modules_cannot_be_disabled(benchmark_project: Project) -> None:
    rules = normalize_drawing_rule_set({"preset": "balanced"})
    rules["modules"]["general"]["enabled"] = False
    result = validate_drawing_rule_set(benchmark_project, rules)
    assert result["valid"] is False
    assert any("required drawing module" in item["message"] for item in result["errors"])
