from __future__ import annotations

SOFTWARE_VERSION = "3.11.0"
ALGORITHM_VERSION = "3.11.0-standards-traceability-rebar-package-online-engineering-docs-assurance-consistency"
RULE_SET_VERSION = "2026.07-v3.11-process-standard-matrix-reviewed-subsets"
EXPORT_SCHEMA_VERSION = "3.11"

def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
