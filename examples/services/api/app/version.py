from __future__ import annotations

SOFTWARE_VERSION = "3.30.0"
ALGORITHM_VERSION = "3.30.0-safe-project-workspace-storage"
RULE_SET_VERSION = "2026.07-v3.30-project-open-memory-safety"
EXPORT_SCHEMA_VERSION = "3.30"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
