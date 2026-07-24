from __future__ import annotations

SOFTWARE_VERSION = "3.87.11"
ALGORITHM_VERSION = "3.87.10-canonical-wall-path-rebar-cage-geometry"
RULE_SET_VERSION = "2026.07-v3.87.0-clause-parameter-and-design-delivery-evidence"
EXPORT_SCHEMA_VERSION = "3.87"
STRUCTURAL_KERNEL_VERSION = "3.87.0-planar-6dof-member-envelope-rebar-feedback-kernel"
RESULT_PIPELINE_VERSION = "3.87.10-single-primary-design-flow-canonical-wall-path-rebar-cage"


def version_manifest() -> dict[str, str]:
    return {
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
        "structuralKernelVersion": STRUCTURAL_KERNEL_VERSION,
        "resultPipelineVersion": RESULT_PIPELINE_VERSION,
    }
