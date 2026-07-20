from __future__ import annotations

SOFTWARE_VERSION = "3.60.0"
ALGORITHM_VERSION = "3.60.0-rebar-entry-self-healing"
RULE_SET_VERSION = "2026.07-v3.60-rebar-entry-closure"
EXPORT_SCHEMA_VERSION = "3.60"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
