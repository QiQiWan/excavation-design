"""精简工作区的“当前性”判定：有明确输入差异时不得沿用当前。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.services.deepening_readiness import projection_contract_allows_current  # noqa: E402


BASE = dict(state_current=True, case_exists=True, contract_id_present=True,
            topology_matches=True, versions_match=True)


def test_projection_allows_current_when_no_component_mismatch() -> None:
    assert projection_contract_allows_current({"inputComponentMismatches": []}, **BASE) is True


def test_projection_refuses_current_when_input_components_changed() -> None:
    contract = {"inputComponentMismatches": ["reinforcementInputs", "retainingSystem"]}
    assert projection_contract_allows_current(contract, **BASE) is False


def test_projection_refuses_current_when_state_or_version_drifted() -> None:
    contract = {"inputComponentMismatches": []}
    assert projection_contract_allows_current(contract, **{**BASE, "state_current": False}) is False
    assert projection_contract_allows_current(contract, **{**BASE, "versions_match": False}) is False
    assert projection_contract_allows_current(contract, **{**BASE, "contract_id_present": False}) is False
