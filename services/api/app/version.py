from __future__ import annotations

SOFTWARE_VERSION = "3.55.0"
ALGORITHM_VERSION = "3.55.0-verification-strengthening-recalculation-closure"
RULE_SET_VERSION = "2026.07-v3.55-intelligent-design-closure"
EXPORT_SCHEMA_VERSION = "3.55"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
