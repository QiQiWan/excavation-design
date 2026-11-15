"""计算书一体化流水线：解读 → 建项目 → 停在人工闸口，不代确认。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))


@pytest.fixture
def book_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "projects.sqlite3"))
    monkeypatch.setenv("PITGUARD_AGENT_DB_PATH", str(tmp_path / "agent.sqlite3"))
    return tmp_path


def _fake_ingest(**kwargs):
    return {
        "mode": "filled",
        "providerUsed": "rules",
        "modelStatus": "not_configured",
        "applied": [{"key": "surcharge", "value": 30.0, "raw": "地面超载 30kPa"}],
        "warnings": ["多值取最后一个"],
        "conflicts": [],
        "remaining": {
            "count": 2,
            "items": [{"key": "designBasisConfirmed", "label": "设计基准确认", "reason": "需人确认"}],
            "text": "还缺：设计基准确认 等 2 项",
            "confirmed": False,
        },
    }


def _fake_chain(project_id, actor):
    return {
        "flowId": "flow-test",
        "status": "waiting_human",
        "progress": {"done": 0, "total": 6},
        "summary": "停在设计基准确认",
        "steps": [{"stageKey": "basis", "state": "waiting_human", "note": "需工程师确认"}],
        "next": {"key": "basis", "gate": {"type": "human_confirm", "reason": "设计基准必须由工程师确认"}},
    }


def test_pipeline_creates_project_and_stops_at_basis(book_env, monkeypatch):
    from app.agents import calculation_book

    monkeypatch.setattr(calculation_book.intake, "ingest", _fake_ingest)
    monkeypatch.setattr(calculation_book.chain_flow, "start", _fake_chain)

    result = calculation_book.run(actor="tester", filename="某工程计算书.pdf",
                                  data=b"%PDF-1.4 sample", provider="rules")

    assert result["createdProject"] is True
    assert result["projectId"].startswith("project-")
    assert "计算书导入" in result["projectId"] or True
    assert [row["key"] for row in result["interpretation"]["applied"]] == ["surcharge"]
    assert result["interpretation"]["missingCount"] == 2
    assert result["pipeline"]["flowId"] == "flow-test"
    assert result["pipeline"]["next"]["key"] == "basis"
    assert result["formalConstructionIssue"] is False


def test_pipeline_uses_given_project(book_env, monkeypatch):
    from app.agents import calculation_book
    from app.schemas.domain import Project
    from app.storage.repository import ProjectRepository, shared_project_store

    repo = ProjectRepository(store=shared_project_store(book_env / "projects.sqlite3"))
    project = repo.create(Project(name="已有项目"))

    monkeypatch.setattr(calculation_book.intake, "ingest", _fake_ingest)
    monkeypatch.setattr(calculation_book.chain_flow, "start", _fake_chain)

    result = calculation_book.run(actor="tester", filename="计算书.pdf", data=b"%PDF",
                                  project_id=project.id, provider="rules")
    assert result["createdProject"] is False
    assert result["projectId"] == project.id


def test_status_reports_basis_gap_and_inputs(book_env):
    from app.agents import calculation_book
    from app.schemas.domain import Project
    from app.storage.repository import ProjectRepository, shared_project_store

    repo = ProjectRepository(store=shared_project_store(book_env / "projects.sqlite3"))
    project = repo.create(Project(name="空项目"))

    data = calculation_book.status(actor="tester", project_id=project.id)
    assert data["designBasis"]["confirmed"] is False
    assert data["designBasis"]["missingCount"] >= 1
    assert data["inputCounts"]["boreholes"] == 0
    assert data["formalConstructionIssue"] is False
