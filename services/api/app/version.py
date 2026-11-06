from __future__ import annotations

SOFTWARE_VERSION = "3.88.0"
SECTION_KERNEL_VERSION = "2.0.0-explicit-methods-history-engineering-review"
ALGORITHM_VERSION = "3.87.16-immutable-stage-loads-final-envelopes"
RULE_SET_VERSION = "2026.10-v3.87.16-effective-stress-passive-depth-unified-actions"
EXPORT_SCHEMA_VERSION = "3.87"
STRUCTURAL_KERNEL_VERSION = "3.87.16-shared-stage-final-stiffness-solver"
RESULT_PIPELINE_VERSION = "3.87.16-immutable-verification-final-envelope-evidence"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "sectionKernelVersion": SECTION_KERNEL_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "structuralKernelVersion": STRUCTURAL_KERNEL_VERSION,
        "resultPipelineVersion": RESULT_PIPELINE_VERSION,
    }
