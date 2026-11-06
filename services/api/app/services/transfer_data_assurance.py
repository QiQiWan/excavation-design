from __future__ import annotations

import re
from typing import Any

from app.schemas.domain import Project
from app.services.engineering_evidence_verification import (
    engineering_evidence_verification_status,
    source_artifact_current,
)
from app.services.support_topology_contract import support_topology_hash

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").strip()))


_REQUIRED_SOIL_PARAMETERS = (
    "unit_weight",
    "cohesion",
    "friction_angle",
    "elastic_modulus",
    "poisson_ratio",
    "horizontal_subgrade_modulus",
    "permeability_x",
)


def evaluate_transfer_engineering_data(project: Project) -> dict[str, Any]:
    """Evaluate provenance and completeness of geology, water and stage data.

    The service never fabricates project data.  Missing evidence remains an
    explicit formal-issue blocker until imported from investigation reports,
    monitoring records, or an approved construction plan.
    """
    boreholes = list(project.boreholes or [])
    strata = list(project.strata or [])
    cases = list(project.calculation_cases or [])
    source_count = sum(bool(str(item.source_file or "").strip()) for item in boreholes)
    source_hash_count = sum(_valid_sha256(getattr(item, "source_file_sha256", None)) for item in boreholes)
    source_artifact_count = sum(
        source_artifact_current(project, getattr(item, "source_artifact_id", None), getattr(item, "source_file_sha256", None))
        for item in boreholes
    )
    source_verified_count = sum(
        bool(getattr(item, "source_verified", False))
        and engineering_evidence_verification_status(project, "borehole", item).get("verified")
        for item in boreholes
    )
    water_records = [record for item in boreholes for record in (item.water_levels or [])]
    water_record_count = len(water_records)
    water_provenance_count = sum(
        bool(str(getattr(record, "source_file", None) or "").strip())
        and _valid_sha256(getattr(record, "source_file_sha256", None))
        and bool(str(getattr(record, "observed_at", None) or "").strip())
        and str(getattr(record, "quality", "")).lower() == "verified"
        and bool(str(getattr(record, "verified_by", None) or "").strip())
        and source_artifact_current(project, getattr(record, "source_artifact_id", None), getattr(record, "source_file_sha256", None))
        and engineering_evidence_verification_status(project, "groundwater", record).get("verified")
        for record in water_records
    )
    parameter_total = len(strata) * len(_REQUIRED_SOIL_PARAMETERS)
    parameter_present = sum(
        getattr(item.parameters, parameter, None) is not None
        for item in strata
        for parameter in _REQUIRED_SOIL_PARAMETERS
    )
    parameter_ratio = parameter_present / parameter_total if parameter_total else 0.0
    coverage = dict(getattr(project.geological_model, "coverage_audit", {}) or {}) if project.geological_model else {}
    coverage_status = str(coverage.get("status") or "missing")

    all_stages = [stage for case in cases for stage in case.stages]
    stage_types = {str(stage.stage_type) for stage in all_stages}
    explicit_water_stage_count = sum(
        stage.groundwater_level_inside is not None and stage.groundwater_level_outside is not None
        for stage in all_stages
    )
    topology_hash = support_topology_hash(project) if project.retaining_system else None
    topology_current_count = sum(
        bool(stage.support_topology_hash and topology_hash and stage.support_topology_hash == topology_hash)
        for stage in all_stages
    )
    required_stage_types = {"excavation", "support_installation", "bottom_slab", "support_removal", "final"}
    missing_stage_types = sorted(required_stage_types - stage_types)
    stage_provenance_count = sum(
        bool(str(getattr(stage, "source_document", None) or "").strip())
        and _valid_sha256(getattr(stage, "source_document_sha256", None))
        and bool(str(getattr(stage, "approved_by", None) or "").strip())
        and bool(str(getattr(stage, "approved_at", None) or "").strip())
        and str(getattr(stage, "data_status", "")).lower() == "verified"
        and source_artifact_current(project, getattr(stage, "source_artifact_id", None), getattr(stage, "source_document_sha256", None))
        and engineering_evidence_verification_status(project, "construction_stage", stage).get("verified")
        for stage in all_stages
    )

    checks = [
        {
            "key": "borehole_density",
            "status": "pass" if len(boreholes) >= 3 else "fail",
            "value": len(boreholes),
            "requirement": ">=3 boreholes for the transfer-system design domain",
            "message": "钻孔数量满足异形支撑工程数据筛查。" if len(boreholes) >= 3 else "钻孔数量不足，无法证明异形支撑范围内的地层代表性。",
        },
        {
            "key": "investigation_provenance",
            "status": "pass" if boreholes and source_count == source_hash_count == source_artifact_count == source_verified_count == len(boreholes) else "fail",
            "value": {"sourceFile": source_count, "sha256": source_hash_count, "artifact": source_artifact_count, "authorizedVerification": source_verified_count},
            "requirement": "every borehole has an immutable source artifact, SHA-256 and current authorized verification record",
            "message": "钻孔均关联不可变源文件、校验哈希和当前授权核验记录。" if boreholes and source_count == source_hash_count == source_artifact_count == source_verified_count == len(boreholes) else "部分钻孔缺少不可变源文件、SHA-256 或经执业资格核验的当前签署记录，数据不可追溯。",
        },
        {
            "key": "soil_parameter_completeness",
            "status": "pass" if parameter_ratio >= 0.90 else "warning" if parameter_ratio >= 0.70 else "fail",
            "value": round(parameter_ratio, 4),
            "requirement": ">=0.90",
            "message": "关键土参数完整率满足正式计算数据门禁。" if parameter_ratio >= 0.90 else "关键土参数存在缺项，需补充试验值或经审定的设计参数。",
        },
        {
            "key": "geological_model_coverage",
            "status": "pass" if coverage_status == "pass" else "fail",
            "value": coverage_status,
            "requirement": "coverage audit pass",
            "message": "地质模型覆盖围护结构和施工影响区。" if coverage_status == "pass" else "地质模型覆盖审计未通过或缺失。",
        },
        {
            "key": "groundwater_observation",
            "status": "pass" if water_record_count >= 2 and water_provenance_count == water_record_count else "fail",
            "value": {"recordCount": water_record_count, "verifiedProvenanceCount": water_provenance_count},
            "requirement": ">=2 groundwater records and every record has source, SHA-256, timestamp and verifier",
            "message": "地下水观测数量和来源链满足筛查。" if water_record_count >= 2 and water_provenance_count == water_record_count else "地下水观测数量不足或记录缺少来源、哈希、时间与核验人。",
        },
        {
            "key": "stage_groundwater_definition",
            "status": "pass" if all_stages and explicit_water_stage_count == len(all_stages) else "fail",
            "value": explicit_water_stage_count,
            "requirement": f"{len(all_stages)} stages explicitly define inside/outside groundwater",
            "message": "各施工阶段均明确坑内外水位。" if all_stages and explicit_water_stage_count == len(all_stages) else "部分施工阶段沿用默认水位，正式计算需明确坑内外水位过程。",
        },
        {
            "key": "construction_stage_completeness",
            "status": "pass" if not missing_stage_types else "fail",
            "value": sorted(stage_types),
            "requirement": sorted(required_stage_types),
            "message": "安装、开挖、底板、拆撑和最终阶段完整。" if not missing_stage_types else "施工阶段缺少：" + "、".join(missing_stage_types),
        },
        {
            "key": "construction_stage_provenance",
            "status": "pass" if all_stages and stage_provenance_count == len(all_stages) else "fail",
            "value": stage_provenance_count,
            "requirement": f"{len(all_stages)} stages have approved source document and SHA-256",
            "message": "施工阶段均绑定经批准的施工资料与文件哈希。" if all_stages and stage_provenance_count == len(all_stages) else "部分施工阶段缺少批准文件、SHA-256、批准人或批准时间。",
        },
        {
            "key": "stage_topology_traceability",
            "status": "pass" if all_stages and topology_current_count == len(all_stages) else "fail",
            "value": topology_current_count,
            "requirement": f"{len(all_stages)} stages reference current topology hash",
            "message": "施工阶段均绑定当前支撑拓扑。" if all_stages and topology_current_count == len(all_stages) else "部分施工阶段未绑定当前支撑拓扑或引用已过期。",
        },
    ]
    fail_count = sum(item["status"] == "fail" for item in checks)
    warning_count = sum(item["status"] == "warning" for item in checks)
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    result = {
        "schema": "pitguard-transfer-engineering-data-assurance-v1",
        "status": status,
        "formalDataReady": status == "pass",
        "supportTopologyHash": topology_hash,
        "metrics": {
            "boreholeCount": len(boreholes),
            "boreholeSourceCount": source_count,
            "boreholeSourceHashCount": source_hash_count,
            "boreholeSourceArtifactCount": source_artifact_count,
            "boreholeSourceVerifiedCount": source_verified_count,
            "groundwaterRecordCount": water_record_count,
            "groundwaterVerifiedProvenanceCount": water_provenance_count,
            "soilParameterCompleteness": round(parameter_ratio, 4),
            "constructionStageCount": len(all_stages),
            "explicitWaterStageCount": explicit_water_stage_count,
            "currentTopologyStageCount": topology_current_count,
            "approvedStageProvenanceCount": stage_provenance_count,
            "failCount": fail_count,
            "warningCount": warning_count,
        },
        "checks": checks,
        "missingInputs": [item["key"] for item in checks if item["status"] != "pass"],
        "message": (
            "真实地质、水位和施工期资料通过来源、覆盖、完整性和拓扑一致性门禁。"
            if status == "pass"
            else "真实工程数据仍有缺项；系统未填充任何假定数据，正式发行保持阻断。"
        ),
    }
    project.advanced_engineering["transferEngineeringDataAssurance"] = result
    return result
