from __future__ import annotations

SOFTWARE_VERSION = "3.26.0"
ALGORITHM_VERSION = "3.26.0-wall-to-wall-load-path-memory-stability"
RULE_SET_VERSION = "2026.07-v3.26-wall-to-wall-load-path"
EXPORT_SCHEMA_VERSION = "3.26"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
