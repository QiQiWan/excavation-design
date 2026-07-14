from __future__ import annotations

SOFTWARE_VERSION = "3.22.0"
ALGORITHM_VERSION = "3.22.0-p0-p3-industrial-closure"
RULE_SET_VERSION = "2026.07-v3.22-qualification-clean-topology-audit-monitoring"
EXPORT_SCHEMA_VERSION = "3.22"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
