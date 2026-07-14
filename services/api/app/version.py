from __future__ import annotations

SOFTWARE_VERSION = "3.23.0"
ALGORITHM_VERSION = "3.23.0-joint-clean-ifc-rebar-login"
RULE_SET_VERSION = "2026.07-v3.23-joint-clean-ifc-rebar-session"
EXPORT_SCHEMA_VERSION = "3.23"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
