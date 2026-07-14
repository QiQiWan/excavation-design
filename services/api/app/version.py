from __future__ import annotations

SOFTWARE_VERSION = "3.21.0"
ALGORITHM_VERSION = "3.21.0-clean-support-topology-primary-objective"
RULE_SET_VERSION = "2026.07-v3.21-crossing-junction-cleanliness-priority"
EXPORT_SCHEMA_VERSION = "3.21"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
