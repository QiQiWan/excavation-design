from __future__ import annotations

SOFTWARE_VERSION = "3.27.0"
ALGORITHM_VERSION = "3.27.0-shape-aware-topology-isolated-worker"
RULE_SET_VERSION = "2026.07-v3.27-shape-topology-runtime-isolation"
EXPORT_SCHEMA_VERSION = "3.27"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
    }
