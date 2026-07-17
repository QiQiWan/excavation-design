from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_steel_support_catalog() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[4]
    path = root / "packages" / "sections" / "steel_support_profiles.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"catalogVersion": "unavailable", "profiles": [], "boundary": "截面目录读取失败，需人工录入截面参数。"}


def recommend_steel_support_profiles(required_area_mm2: float = 0.0, required_inertia_mm4: float = 0.0, limit: int = 3) -> list[dict[str, Any]]:
    profiles = list(load_steel_support_catalog().get("profiles") or [])
    viable = [row for row in profiles if float(row.get("areaMm2") or 0.0) >= required_area_mm2 and float(row.get("inertiaMm4") or 0.0) >= required_inertia_mm4]
    source = viable or profiles
    source.sort(key=lambda row: (float(row.get("areaMm2") or 0.0), float(row.get("inertiaMm4") or 0.0)))
    return source[: max(1, int(limit))]
