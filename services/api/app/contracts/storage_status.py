from __future__ import annotations

from typing import Literal, cast

StorageStatus = Literal["normal", "elevated", "large", "workspace_only"]
STORAGE_STATUS_VALUES: frozenset[str] = frozenset({"normal", "elevated", "large", "workspace_only"})


def normalize_storage_status(value: object, *, fallback: StorageStatus = "elevated") -> StorageStatus:
    normalized = str(value or "normal").strip().lower()
    aliases = {
        "workspace-only": "workspace_only",
        "workspace": "workspace_only",
        "oversized": "workspace_only",
        "high": "large",
        "warning": "elevated",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in STORAGE_STATUS_VALUES:
        return fallback
    return cast(StorageStatus, normalized)


def classify_storage_status(payload_bytes: int, full_load_limit_bytes: int) -> StorageStatus:
    limit = max(1, int(full_load_limit_bytes))
    ratio = max(0, int(payload_bytes)) / limit
    if ratio >= 1.0:
        return "workspace_only"
    if ratio >= 0.7:
        return "elevated"
    return "normal"
