from __future__ import annotations

SOFTWARE_VERSION = "3.25.0"
ALGORITHM_VERSION = "3.25.0-parallel-corner-brace-login-routing"
RULE_SET_VERSION = "2026.07-v3.25-parallel-corner-brace"
EXPORT_SCHEMA_VERSION = "3.25"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
