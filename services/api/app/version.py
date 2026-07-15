from __future__ import annotations

SOFTWARE_VERSION = "3.31.0"
ALGORITHM_VERSION = "3.31.0-external-dataset-working-set"
RULE_SET_VERSION = "2026.07-v3.31-hot-cold-data-separation"
EXPORT_SCHEMA_VERSION = "3.31"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
