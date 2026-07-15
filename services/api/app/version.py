from __future__ import annotations

SOFTWARE_VERSION = "3.32.0"
ALGORITHM_VERSION = "3.32.0-fast-interaction-progress"
RULE_SET_VERSION = "2026.07-v3.32-fast-interaction"
EXPORT_SCHEMA_VERSION = "3.32"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
