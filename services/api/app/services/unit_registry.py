from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_REGISTRY_PATH = Path(__file__).resolve().parents[4] / "packages" / "units" / "engineering-units.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    quantities = data.get("quantities") or {}
    aliases = data.get("aliases") or {}
    resolved = dict(quantities)
    for alias, canonical in aliases.items():
        if canonical in quantities:
            resolved[alias] = quantities[canonical]
    data["resolvedQuantities"] = resolved
    return data


UNIT_REGISTRY: dict[str, dict[str, Any]] = _load()["resolvedQuantities"]


def infer_unit_key(field_name: str) -> str | None:
    for rule in _load().get("fieldRules") or []:
        try:
            if re.search(str(rule.get("pattern") or ""), field_name, flags=re.IGNORECASE):
                return str(rule.get("unitKey"))
        except re.error:
            continue
    return None


def field_unit_metadata(field_names: list[str]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for field in field_names:
        key = infer_unit_key(field)
        rows[field] = {"unitKey": key, **(UNIT_REGISTRY.get(key) or {})} if key else {"unitKey": None, "symbol": None}
    return rows


def unit_registry() -> dict[str, Any]:
    data = _load()
    return {
        "schemaVersion": data.get("schemaVersion", "1.0"),
        "system": data.get("system", "SI-engineering"),
        "rules": data.get("rules") or {},
        "quantities": UNIT_REGISTRY,
        "fieldRules": data.get("fieldRules") or [],
        "source": "packages/units/engineering-units.json",
    }
