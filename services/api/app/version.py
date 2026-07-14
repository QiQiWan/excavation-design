from __future__ import annotations

SOFTWARE_VERSION = "3.29.0"
ALGORITHM_VERSION = "3.29.0-resilient-scheme-designer-resource-guard"
RULE_SET_VERSION = "2026.07-v3.29-scheme-designer-audit-and-runtime-resilience"
EXPORT_SCHEMA_VERSION = "3.29"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
