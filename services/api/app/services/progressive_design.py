from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import Project
from app.services.design_qualification import build_design_qualification, build_support_system_options
from app.services.runtime_resource_policy import adaptive_resource_policy

SCHEMA_VERSION = "1.1"
STAGE_ORDER = [
    "geometry_context",
    "engineering_context",
    "retaining_wall_strategy",
    "support_system_strategy",
    "topology_search",
    "candidate_screening",
    "stage_calculation",
    "detailing_release",
]
DECISION_STAGE = {
    "coordinateMode": "geometry_context",
    "geologyPolicy": "geometry_context",
    "constructionMethod": "engineering_context",
    "retainingWallFamily": "retaining_wall_strategy",
    "wallVerticalStrategy": "retaining_wall_strategy",
    "supportSystemFamily": "support_system_strategy",
    "cornerTreatment": "support_system_strategy",
    "transitionTreatment": "support_system_strategy",
    "objectivePreset": "topology_search",
    "candidateCount": "topology_search",
    "fullCalculationCount": "stage_calculation",
    "calculationMode": "stage_calculation",
    "detailLevel": "detailing_release",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_config(project: Project, systems: dict[str, Any]) -> dict[str, Any]:
    options = list(systems.get("options") or [])
    recommended = next((item for item in options if item.get("recommended")), options[0] if options else {})
    support_family = str(recommended.get("family") or "auto")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "currentStage": "geometry_context",
        "decisions": {
            "coordinateMode": "confirm_before_formal_issue",
            "geologyPolicy": "expand_with_extrapolation_gate",
            "constructionMethod": "internal_support",
            "retainingWallFamily": "auto",
            "wallVerticalStrategy": "uniform_by_zone",
            "supportSystemFamily": "auto",
            "recommendedSupportSystemFamily": support_family,
            "cornerTreatment": "auto_by_topology",
            "transitionTreatment": "explicit_transfer_zone",
            "objectivePreset": "balanced",
            "candidateCount": 3,
            "fullCalculationCount": 1,
            "calculationMode": "adaptive_safe",
            "detailLevel": "engineering_review",
        },
        "constraints": {
            "supportSpacingMinM": 3.0,
            "supportSpacingMaxM": 6.0,
            "preferredSupportSpacingM": 5.0,
            "columnServiceSpanMaxM": 18.0,
            "preserveMuckPath": True,
            "avoidObstacleBoundaries": True,
            "requireIndependentWallNodes": True,
            "allowSupportToSupportTerminal": False,
            "lockedZones": [],
        },
        "resourcePolicy": {
            "mode": "adaptive",
            "workspaceFirst": True,
            "candidateExecution": "auto_serial_or_parallel",
            "fullProjectHydration": "worker_only",
            "keepOnlyCurrentFullResultInWorkspace": True,
        },
        "completedStages": [],
        "confirmedStages": [],
        "dirtyFromStage": "geometry_context",
        "history": [],
    }


def normalize_progressive_config(project: Project, persisted: dict[str, Any] | None = None) -> dict[str, Any]:
    systems = build_support_system_options(project)
    base = _default_config(project, systems)
    raw = deepcopy(persisted or {})
    for section in ("decisions", "constraints", "resourcePolicy"):
        if isinstance(raw.get(section), dict):
            base[section].update(raw[section])
    if raw.get("currentStage"):
        base["currentStage"] = str(raw["currentStage"])
    if isinstance(raw.get("completedStages"), list):
        base["completedStages"] = [str(item) for item in raw["completedStages"]]
    if isinstance(raw.get("confirmedStages"), list):
        base["confirmedStages"] = [str(item) for item in raw["confirmedStages"] if str(item) in STAGE_ORDER]
    if raw.get("dirtyFromStage") in STAGE_ORDER:
        base["dirtyFromStage"] = str(raw["dirtyFromStage"])
    if isinstance(raw.get("history"), list):
        base["history"] = list(raw["history"])[-80:]
    if raw.get("sessionVersion") is not None:
        base["sessionVersion"] = int(raw["sessionVersion"])
    if raw.get("updatedAt"):
        base["updatedAt"] = raw["updatedAt"]

    decisions = base["decisions"]
    constraints = base["constraints"]
    decisions["candidateCount"] = max(1, min(8, int(decisions.get("candidateCount") or 3)))
    decisions["fullCalculationCount"] = max(1, min(3, int(decisions.get("fullCalculationCount") or 1)))
    constraints["supportSpacingMinM"] = max(2.0, min(8.0, float(constraints.get("supportSpacingMinM") or 3.0)))
    constraints["supportSpacingMaxM"] = max(
        constraints["supportSpacingMinM"], min(12.0, float(constraints.get("supportSpacingMaxM") or 6.0))
    )
    constraints["preferredSupportSpacingM"] = max(
        constraints["supportSpacingMinM"],
        min(constraints["supportSpacingMaxM"], float(constraints.get("preferredSupportSpacingM") or 5.0)),
    )
    constraints["columnServiceSpanMaxM"] = max(6.0, min(30.0, float(constraints.get("columnServiceSpanMaxM") or 18.0)))
    available_families = {str(item.get("family")) for item in systems.get("options") or []}
    selected_family = str(decisions.get("supportSystemFamily") or "auto")
    if selected_family != "auto" and selected_family not in available_families:
        decisions["supportSystemFamily"] = str((systems.get("options") or [{}])[0].get("family") or "auto")
    return base


def _stage(
    code: str,
    index: int,
    title: str,
    purpose: str,
    status: str,
    summary: str,
    *,
    choices: list[dict[str, Any]] | None = None,
    required_inputs: list[str] | None = None,
    next_action: str | None = None,
    blocks_next: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "index": index,
        "title": title,
        "purpose": purpose,
        "status": status,
        "summary": summary,
        "choices": choices or [],
        "requiredInputs": required_inputs or [],
        "nextAction": next_action,
        "blocksNext": bool(blocks_next),
    }


def build_progressive_design_session(
    project: Project,
    *,
    persisted: dict[str, Any] | None = None,
    storage_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = normalize_progressive_config(project, persisted)
    qualification = build_design_qualification(project, storage_info=storage_info)
    systems = build_support_system_options(project)
    decisions = config["decisions"]
    constraints = config["constraints"]
    gates = {str(item.get("code")): item for item in qualification.get("gates") or []}

    geometry_ok = str((gates.get("Q-GEOMETRY") or {}).get("status")) == "pass"
    coordinate_status = str((gates.get("Q-COORD-GEO") or {}).get("status") or "manual_review")
    has_excavation = project.excavation is not None
    has_geology = bool(project.boreholes and project.strata)
    has_obstacles = bool(project.excavation and project.excavation.obstacles)
    has_walls = bool(project.retaining_system and project.retaining_system.diaphragm_walls)
    candidates = list(project.retaining_system.support_layout_repair.candidates or []) if project.retaining_system and project.retaining_system.support_layout_repair else []
    feasible_candidates = [row for row in candidates if bool((row.hard_constraints or {}).get("passed"))]
    has_current_calculation = bool(project.calculation_results) and not bool(
        (project.advanced_engineering.get("calculationState") or {}).get("requiresRecalculation")
    )

    system_choices = [{
        "value": "auto",
        "label": "按轮廓与工程约束自动推荐",
        "recommended": True,
        "available": True,
        "readiness": "candidate_generation_ready",
        "description": f"当前推荐体系：{decisions.get('recommendedSupportSystemFamily') or (systems.get('options') or [{}])[0].get('family') or '待识别'}；仍可在本阶段显式改选。",
    }] + [
        {
            "value": item.get("family"),
            "label": item.get("title"),
            "recommended": bool(item.get("recommended")),
            "available": bool(item.get("automaticGenerationAvailable")),
            "readiness": item.get("candidateReadiness"),
            "description": item.get("nextAction"),
        }
        for item in systems.get("options") or []
    ]
    resource = adaptive_resource_policy(role="api")
    stages = [
        _stage(
            "geometry_context", 1, "轮廓、坐标与设计域确认",
            "确认基坑轮廓含义、工程坐标、地质覆盖和局部坑/障碍边界。",
            "complete" if geometry_ok and coordinate_status == "pass" else "attention" if has_excavation else "blocked",
            "轮廓闭合且坐标关系已确认。" if geometry_ok and coordinate_status == "pass" else "轮廓可继续解析，但坐标或地质覆盖仍需确认。",
            choices=[
                {"field": "coordinateMode", "value": "use_project_coordinates", "label": "沿用工程坐标", "description": "轮廓、钻孔和障碍已位于同一测量坐标系。"},
                {"field": "coordinateMode", "value": "transform_with_control_points", "label": "通过控制点转换", "description": "保存平移、旋转和尺度转换证据后再写回设计域。"},
                {"field": "coordinateMode", "value": "confirm_before_formal_issue", "label": "方案阶段暂存，发行前确认", "recommended": True, "description": "允许概念候选，完整发行持续阻断。"},
                {"field": "coordinateMode", "value": "local_relative_design", "label": "采用独立局部坐标", "description": "明确轮廓和地质均以同一局部原点表达。"},
                {"field": "geologyPolicy", "value": "require_native_coverage", "label": "要求原始钻孔覆盖", "description": "超出勘察控制域时阻断候选计算。"},
                {"field": "geologyPolicy", "value": "expand_with_extrapolation_gate", "label": "允许受控外扩", "recommended": True, "description": "记录外推距离和置信度，正式发行按阈值控制。"},
                {"field": "geologyPolicy", "value": "concept_only", "label": "仅做概念方案", "description": "不产生正式计算和施工图结论。"},
            ],
            required_inputs=["闭合基坑轮廓", "坐标基准或转换关系", "钻孔/地层范围"],
            next_action="确认坐标与地质覆盖策略后进入工程约束配置。",
            blocks_next=not has_excavation or not geometry_ok,
        ),
        _stage(
            "engineering_context", 2, "工程约束与施工组织",
            "逐项确认开挖深度、地下水、地下室柱网、坡道、出土口、保护区和施工分区。",
            "complete" if has_geology and has_obstacles else "attention" if has_geology else "blocked",
            "已形成地质输入和施工障碍边界。" if has_geology and has_obstacles else "可先生成概念候选；缺少障碍/通道时施工避让结论保持预警。",
            choices=[
                {"field": "constructionMethod", "value": "internal_support", "label": "顺作内支撑"},
                {"field": "constructionMethod", "value": "zoned_excavation", "label": "分区开挖"},
                {"field": "constructionMethod", "value": "center_island", "label": "中心岛/留土"},
                {"field": "constructionMethod", "value": "top_down", "label": "逆作或半逆作"},
                {"field": "constructionMethod", "value": "custom", "label": "专项施工组织"},
            ],
            required_inputs=["开挖标高", "地下水位", "障碍/出土通道", "施工阶段偏好"],
            next_action="补充缺失工程约束，或明确接受概念设计预警。",
        ),
        _stage(
            "retaining_wall_strategy", 3, "围护墙体系与竖向分区",
            "选择围护墙家族、墙厚/刚度等级、墙趾策略和分区统一原则。",
            "complete" if has_walls else "ready",
            "当前围护墙已经形成，可继续优化墙长与墙趾。" if has_walls else "系统可按工程条件生成围护墙概念方案。",
            choices=[
                {"field": "retainingWallFamily", "value": "auto", "label": "自动推荐", "recommended": True},
                {"field": "retainingWallFamily", "value": "diaphragm_wall", "label": "地下连续墙"},
                {"field": "retainingWallFamily", "value": "secant_pile", "label": "咬合桩/排桩"},
                {"field": "retainingWallFamily", "value": "smw", "label": "SMW工法桩"},
                {"field": "retainingWallFamily", "value": "custom", "label": "专项围护体系"},
                {"field": "wallVerticalStrategy", "value": "uniform_all_faces", "label": "全坑统一墙趾", "description": "连续性优先，适合地层变化较小的工程。"},
                {"field": "wallVerticalStrategy", "value": "uniform_by_zone", "label": "分区统一墙趾", "recommended": True, "description": "每个工程分区保持统一，区间变化必须有地质和稳定证据。"},
                {"field": "wallVerticalStrategy", "value": "local_strengthening_only", "label": "统一墙趾+局部加强", "description": "通过厚度、配筋或局部加深处理控制段。"},
            ],
            next_action="确认围护墙体系后再选择坑内支撑体系。",
        ),
        _stage(
            "support_system_strategy", 4, "支撑结构体系选择",
            "先确定受力体系和转接机制，再生成线位，避免只对一类平面写死规则。",
            "complete" if "support_system_strategy" in config.get("confirmedStages", []) else "ready",
            (
                f"当前选择：{decisions.get('supportSystemFamily')}。"
                if str(decisions.get("supportSystemFamily") or "auto") != "auto"
                else f"当前采用自动推荐：{decisions.get('recommendedSupportSystemFamily') or '待识别'}。"
            ),
            choices=system_choices + [
                {"field": "cornerTreatment", "value": "auto_by_topology", "label": "角部构造自动匹配", "recommended": True, "description": "依据凸角、凹角、端墙和体系家族选择独立节点。"},
                {"field": "cornerTreatment", "value": "wall_to_wall_braces", "label": "墙—墙角撑", "description": "角撑两端落墙或明确围檩节点，禁止止于普通支撑中部。"},
                {"field": "cornerTreatment", "value": "frame_corner_zone", "label": "角部显式框架区", "description": "用于多向传力和节点拥挤区域。"},
                {"field": "transitionTreatment", "value": "explicit_transfer_zone", "label": "显式转接区", "recommended": True, "description": "宽度突变、分区交界和凹角必须形成可计算转接构造。"},
                {"field": "transitionTreatment", "value": "ring_or_transfer_beam", "label": "环梁/转接梁", "description": "由闭合梁或局部转接梁承担方向转换。"},
                {"field": "transitionTreatment", "value": "partition_wall", "label": "分隔墙分区", "description": "通过分隔墙和施工分仓拆分传力体系。"},
            ],
            required_inputs=["体系家族", "角部处理", "分区转接方式"],
            next_action="选定体系后配置线位搜索区间与人工锁定。",
        ),
        _stage(
            "topology_search", 5, "线位、分仓与候选多样性配置",
            "配置间距范围、立柱服务跨、角部/转接处理、障碍避让和候选多样性。",
            "complete" if feasible_candidates else "ready",
            (
                f"已有 {len(feasible_candidates)} 个拓扑硬约束通过的候选。"
                if feasible_candidates else
                f"拟生成 {decisions['candidateCount']} 个候选，间距 {constraints['supportSpacingMinM']:.1f}–{constraints['supportSpacingMaxM']:.1f} m。"
            ),
            choices=[
                {"field": "objectivePreset", "value": "balanced", "label": "综合均衡"},
                {"field": "objectivePreset", "value": "clean_support_layout", "label": "传力整洁优先"},
                {"field": "objectivePreset", "value": "fewer_columns", "label": "少立柱优先"},
                {"field": "objectivePreset", "value": "low_axial_force", "label": "低轴力峰值优先"},
                {"field": "objectivePreset", "value": "muck_path_priority", "label": "施工通道优先"},
            ],
            next_action="生成候选并在平面预览中确认传力路径与施工空间。",
        ),
        _stage(
            "candidate_screening", 6, "候选预检与体系回退",
            "对候选执行零非法穿越、端点、节点拥挤、围檩跨、障碍与传力冗余预检。",
            "complete" if feasible_candidates else "blocked" if candidates else "waiting",
            f"候选 {len(candidates)} 个，其中 {len(feasible_candidates)} 个具备计算资格。",
            next_action="无可行候选时返回体系选择或转接构造，不继续堆叠失败线位。",
            blocks_next=bool(candidates and not feasible_candidates),
        ),
        _stage(
            "stage_calculation", 7, "施工阶段完整计算与方案比选",
            "按资源策略逐方案或有限并发运行施工阶段计算，形成轴力、位移、围檩和稳定性证据。",
            "complete" if has_current_calculation else "ready" if feasible_candidates else "waiting",
            (
                "当前方案已有有效施工阶段计算。" if has_current_calculation else
                f"计划完整计算 {decisions['fullCalculationCount']} 个候选；当前建议重型并发 {resource['recommendedHeavyConcurrency']}。"
            ),
            choices=[
                {"field": "fullCalculationCount", "value": 1, "label": "先计算推荐方案"},
                {"field": "fullCalculationCount", "value": 3, "label": "完整比选前三方案"},
            ],
            next_action="资源紧张时逐方案计算并释放中间对象，不降低工程验算内容。",
        ),
        _stage(
            "detailing_release", 8, "构件深化、配筋与交付",
            "完成构件稳定、节点、配筋、施工图、IFC和正式发行门禁。",
            "complete" if qualification.get("formalIssueAllowed") else "ready" if has_current_calculation else "waiting",
            "正式发行条件已闭合。" if qualification.get("formalIssueAllowed") else "计算后继续完成构件、节点和校审证据。",
            choices=[
                {"field": "detailLevel", "value": "engineering_review", "label": "工程复核级", "recommended": True, "description": "输出完整计算证据和深化清单，保留人工校审。"},
                {"field": "detailLevel", "value": "construction_detail", "label": "施工深化级", "description": "增加节点、钢筋、分节、吊装和施工图数据。"},
                {"field": "detailLevel", "value": "formal_issue", "label": "正式发行级", "description": "要求地质、计算、节点、配筋、审签和修订证据全部闭合。"},
            ],
            next_action="仅在计算合同、节点、配筋、地质和校审证据完整后开放正式发行。",
        ),
    ]

    completed = [item["code"] for item in stages if item["status"] == "complete"]
    first_actionable = next((item for item in stages if item["status"] not in {"complete"}), stages[-1])
    config["completedStages"] = completed
    config["currentStage"] = str(config.get("currentStage") or first_actionable["code"])
    trace_payload = {
        "schemaVersion": config.get("schemaVersion"),
        "decisions": config.get("decisions"),
        "constraints": config.get("constraints"),
        "resourcePolicy": config.get("resourcePolicy"),
    }
    config_trace_hash = hashlib.sha256(
        json.dumps(trace_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "projectId": project.id,
        "schemaVersion": SCHEMA_VERSION,
        "config": config,
        "stages": stages,
        "currentStage": config["currentStage"],
        "recommendedStage": first_actionable["code"],
        "progress": round(100.0 * len(completed) / max(len(stages), 1), 1),
        "qualification": {
            "interactionMode": qualification.get("interactionMode"),
            "candidateGenerationAllowed": qualification.get("candidateGenerationAllowed"),
            "calculationAllowed": qualification.get("calculationAllowed"),
            "formalIssueAllowed": qualification.get("formalIssueAllowed"),
        },
        "resourcePolicy": resource,
        "configurationTraceHash": config_trace_hash,
        "systemOptions": systems,
        "generatedAt": _now(),
    }


def merge_progressive_config(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(current or {})
    changed_stages: list[str] = []
    for section in ("decisions", "constraints", "resourcePolicy"):
        incoming = patch.get(section)
        if not isinstance(incoming, dict):
            continue
        merged.setdefault(section, {})
        for key, value in incoming.items():
            if merged[section].get(key) == value:
                continue
            merged[section][key] = value
            if section == "decisions":
                changed_stages.append(DECISION_STAGE.get(key, "topology_search"))
            elif section == "constraints":
                changed_stages.append("topology_search")
            else:
                changed_stages.append("stage_calculation")
    if patch.get("currentStage") in STAGE_ORDER:
        merged["currentStage"] = str(patch["currentStage"])

    confirmed = [str(item) for item in merged.get("confirmedStages") or [] if str(item) in STAGE_ORDER]
    action = str(patch.get("action") or "configuration_updated")
    if action == "stage_confirmed":
        stage = str(patch.get("currentStage") or merged.get("currentStage") or "")
        if stage in STAGE_ORDER and stage not in confirmed:
            confirmed.append(stage)
        if merged.get("dirtyFromStage") == stage:
            next_index = min(STAGE_ORDER.index(stage) + 1, len(STAGE_ORDER) - 1)
            merged["dirtyFromStage"] = STAGE_ORDER[next_index]
    elif changed_stages:
        earliest = min(changed_stages, key=STAGE_ORDER.index)
        earliest_index = STAGE_ORDER.index(earliest)
        confirmed = [stage for stage in confirmed if STAGE_ORDER.index(stage) < earliest_index]
        merged["dirtyFromStage"] = earliest
    merged["confirmedStages"] = confirmed

    history = list(merged.get("history") or [])
    history.append({
        "at": _now(),
        "action": action,
        "stage": str(patch.get("currentStage") or merged.get("currentStage") or ""),
        "changes": {key: patch[key] for key in ("decisions", "constraints", "resourcePolicy") if key in patch},
        "invalidatedFromStage": merged.get("dirtyFromStage") if changed_stages else None,
    })
    merged["history"] = history[-80:]
    return merged


def task_payload_from_progressive_config(config: dict[str, Any]) -> dict[str, Any]:
    decisions = dict(config.get("decisions") or {})
    constraints = dict(config.get("constraints") or {})
    family = str(decisions.get("supportSystemFamily") or "auto")
    return {
        "preset": str(decisions.get("objectivePreset") or "balanced"),
        "topologyFamily": None if family == "auto" else family,
        "maxCandidates": int(decisions.get("candidateCount") or 3),
        "searchConfig": {
            "spacingMinM": float(constraints.get("supportSpacingMinM") or 3.0),
            "spacingMaxM": float(constraints.get("supportSpacingMaxM") or 6.0),
            "preferredSpacingM": float(constraints.get("preferredSupportSpacingM") or 5.0),
            "columnSpanMaxM": float(constraints.get("columnServiceSpanMaxM") or 18.0),
            "preserveMuckPath": bool(constraints.get("preserveMuckPath", True)),
            "avoidObstacleBoundaries": bool(constraints.get("avoidObstacleBoundaries", True)),
        },
        "constructionMethod": decisions.get("constructionMethod"),
        "cornerTreatment": decisions.get("cornerTreatment"),
        "transitionTreatment": decisions.get("transitionTreatment"),
        "calculationMode": decisions.get("calculationMode"),
        "progressiveDesignSchema": SCHEMA_VERSION,
        "configurationTraceHash": hashlib.sha256(
            json.dumps({"decisions": decisions, "constraints": constraints}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
