from __future__ import annotations

SOFTWARE_VERSION = "3.24.1"
ALGORITHM_VERSION = "3.24.1-industrial-calculation-login-route"
RULE_SET_VERSION = "2026.07-v3.24-industrial-calculation-assurance"
EXPORT_SCHEMA_VERSION = "3.24"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
