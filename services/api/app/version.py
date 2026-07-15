from __future__ import annotations

SOFTWARE_VERSION = "3.34.0"
ALGORITHM_VERSION = "3.34.0-support-deep-design-closure"
RULE_SET_VERSION = "2026.07-v3.33-idw-stepped-support"
EXPORT_SCHEMA_VERSION = "3.33"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
