from app.schemas.domain import FormalReportGate, Project, QualityGateIssue
from app.services.formalization_resolution import build_formalization_resolution_plan
from app.services.standards_matrix import build_online_documentation


def test_online_documentation_contains_task_oriented_sections():
    docs = build_online_documentation()
    assert docs["version"] == "3.87.13"
    assert docs["quickStart"]
    assert docs["formalizationGuide"]["automaticBoundary"]
    assert docs["formalizationGuide"]["manualBoundary"]
    assert docs["troubleshooting"]
    assert docs["deploymentGuide"]["build"]
    chapter_ids = {item["id"] for item in docs["chapters"]}
    assert {"quickstart", "formalization", "troubleshooting", "deployment"}.issubset(chapter_ids)


def test_formalization_plan_separates_automatic_and_manual_actions():
    project = Project(name="formalization-test")
    gate = FormalReportGate(
        status="fail",
        allowed_for_official_issue=False,
        headline="blocked",
        blocking_items=[
            QualityGateIssue(
                category="calculation_contract",
                severity="fail",
                message="calculation stale",
            ),
            QualityGateIssue(
                category="statutory_workflow",
                severity="fail",
                message="signature missing",
            ),
        ],
    )
    plan = build_formalization_resolution_plan(project, gate)
    automatic_codes = {item["actionCode"] for item in plan["automaticActions"]}
    manual_codes = {item["actionCode"] for item in plan["manualActions"]}
    assert "recalculate_current_design" in automatic_codes
    assert "manual:statutory_workflow" in manual_codes
    assert plan["automaticCount"] == 1
    assert plan["manualCount"] == 1
    assert plan["blockingCount"] == 2


def test_not_required_transfer_system_is_documented_as_non_blocking():
    docs = build_online_documentation()
    status_rows = docs["formalizationGuide"]["statusMeaning"]
    row = next(item for item in status_rows if item["status"] == "not_required")
    assert "不适用" in row["meaning"]


def test_documentation_endpoint_remains_public_when_access_control_enabled(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("PITGUARD_API_KEYS", '{"private-key":"admin"}')
    with TestClient(app) as client:
        response = client.get("/api/documentation")
    assert response.status_code == 200
    assert response.json()["version"] == "3.87.13"
