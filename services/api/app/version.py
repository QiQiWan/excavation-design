from __future__ import annotations

SOFTWARE_VERSION = "3.37.0"
ALGORITHM_VERSION = "3.37.0-progressive-adaptive-runtime"
RULE_SET_VERSION = "2026.07-v3.37-progressive-adaptive-runtime"
EXPORT_SCHEMA_VERSION = "3.37"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
