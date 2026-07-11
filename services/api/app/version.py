from __future__ import annotations

SOFTWARE_VERSION = "3.3.0"
ALGORITHM_VERSION = "3.3.0-eight-track-engineering-monitoring-formal-issue"
RULE_SET_VERSION = "2026.07-reviewed-subsets"
EXPORT_SCHEMA_VERSION = "3.3"

def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
