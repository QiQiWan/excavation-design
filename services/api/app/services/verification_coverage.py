from __future__ import annotations

from typing import Any

from app.schemas.domain import Project


def _input(
    code: str,
    label: str,
    stage: str,
    provider: str,
    action: str,
    *,
    design_stage_available: bool = True,
    target: str = "工程输入",
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "stage": stage,
        "stageLabel": {
            "survey": "勘察阶段",
            "design": "设计阶段",
            "calculation": "软件计算生成",
            "construction": "施工准备/施工阶段",
            "review": "校审发行阶段",
        }.get(stage, stage),
        "provider": provider,
        "designStageAvailable": design_stage_available,
        "action": action,
        "target": target,
    }


INPUT_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "design_basis": _input("design_basis", "已确认的设计基准、工程等级与荷载组合", "design", "设计负责人", "在“设计基准”中确认安全等级、规范、材料和荷载组合。", target="设计基准"),
    "excavation": _input("excavation", "闭合基坑轮廓、开挖标高与局部深坑", "design", "建筑/基坑设计", "补齐基坑轮廓、坑顶坑底标高和局部深坑。"),
    "surcharge": _input("surcharge", "坑边荷载、道路/堆载与施工荷载", "design", "结构及施工设计", "在设计基准或各施工工况中确认坑边附加荷载。"),
    "soil_strength": _input("soil_strength", "各设计地层重度、黏聚力 c 与内摩擦角 φ", "survey", "岩土勘察", "在统一地层表补齐强度参数及其试验/经验来源。", target="地质与钻孔"),
    "soil_stiffness": _input("soil_stiffness", "各设计地层压缩/变形模量及水平地基反力参数", "survey", "岩土勘察", "补齐压缩模量、弹性模量或水平地基反力系数。", target="地质与钻孔"),
    "permeability": _input("permeability", "各含水层渗透系数及渗流方向性", "survey", "水文地质勘察", "补齐 kx/ky/kz 或项目采用的等效渗透系数。", target="地质与钻孔"),
    "groundwater": _input("groundwater", "潜水水位、坑内控制水位及变化范围", "survey", "水文地质勘察/降水设计", "录入钻孔水位记录并确认坑内外设计水位。", target="地质与钻孔"),
    "confined_head": _input("confined_head", "承压含水层顶板、隔水层厚度和承压水头（或无承压水结论）", "survey", "水文地质勘察", "录入承压水头；无承压水时也应形成明确勘察结论。", target="地质与钻孔"),
    "bearing_capacity": _input("bearing_capacity", "立柱基础持力层地基承载力特征值", "survey", "岩土勘察", "在设计基准中录入地基承载力特征值。", target="设计基准"),
    "adjacent_environment": _input("adjacent_environment", "周边建筑、管线、道路及变形控制等级", "design", "基坑设计/业主", "确认周边环境等级、保护对象距离和变形控制值。", target="设计基准"),
    "seismic": _input("seismic", "抗震设防、临时结构地震作用取舍及专项要求", "design", "结构设计", "确认抗震等级及临时支护是否计入地震组合。", target="设计基准"),
    "durability": _input("durability", "环境类别、设计使用年限、保护层与材料耐久性参数", "design", "结构设计", "确认环境类别、使用年限、混凝土等级和保护层。", target="设计基准"),
    "wall": _input("wall", "围护墙计算段、墙厚、标高、材料和施工分幅", "design", "围护设计", "生成或完善每个围护墙对象及施工槽段。", target="围护方案"),
    "wall_joint": _input("wall_joint", "槽段接头、止水构造与接缝传力设计", "design", "围护/施工图设计", "补齐施工槽段、接头型式、止水与接缝传力大样。", target="配筋深化"),
    "wale": _input("wale", "冠梁/围檩截面、材料、跨度与节点位置", "design", "结构设计", "生成围檩并确认截面、支承跨和节点位置。", target="围护方案"),
    "support": _input("support", "支撑体系、截面、材料、安装阶段与预加轴力", "design", "结构设计", "完善支撑截面、安装/拆除工况和预加轴力。", target="围护方案"),
    "support_node": _input("support_node", "支撑—围檩节点、承压板、锚固与局部构造", "design", "结构/节点设计", "生成节点并补齐承压板、锚固、加劲和局部配筋。", target="配筋深化"),
    "column": _input("column", "临时立柱截面、计算长度、支承关系与基础", "design", "结构设计", "补齐立柱及其基础、支撑关系和计算长度。", target="围护方案"),
    "rebar": _input("rebar", "墙、梁、支撑与节点配筋方案", "design", "结构设计", "生成并应用配筋方案；截面变化后重新计算。", target="配筋深化"),
    "calculation": _input("calculation", "当前设计快照的完整施工阶段计算结果", "calculation", "PitGuard 计算任务", "重新运行“计算当前方案”，生成与当前拓扑及输入一致的结果。", target="计算验算"),
    "calculation_assurance": _input("calculation_assurance", "计算合同、阶段覆盖、数值质量与独立复核证据", "calculation", "PitGuard 计算任务/复核人", "运行完整计算并关闭计算质量包中的硬失败。", target="计算验算"),
    "wall_internal_force": _input("wall_internal_force", "逐墙、逐工况弯矩/剪力/位移包络", "calculation", "PitGuard 计算任务", "按当前墙对象和施工工况重新计算。", target="计算验算"),
    "support_force": _input("support_force", "逐支撑轴力及施工效应包络", "calculation", "PitGuard 计算任务", "按当前支撑体系重新计算。", target="计算验算"),
    "dewatering_plan": _input("dewatering_plan", "降水/减压井布置、能力、备用电源及停泵水位", "design", "降水专项设计", "补充降水与减压专项参数并运行不利工况复算。", target="不利工况"),
    "monitoring_limits": _input("monitoring_limits", "墙体位移、沉降、轴力和水位报警控制值", "design", "基坑设计/监测方案", "确认项目监测报警值；设计阶段可先提供控制值。", target="监测与交付"),
    "cage_lifting_plan": _input("cage_lifting_plan", "钢筋笼分节、重量、吊机工况、吊点及临时加强", "construction", "施工单位/吊装专项", "补充吊机性能和场地工况后完成专项吊装验算。", design_stage_available=False, target="配筋深化"),
    "coupler_product": _input("coupler_product", "机械连接套筒产品、接头等级、工艺检验与抽检批次", "construction", "施工单位/供应商", "选定套筒产品并录入等级、错开比例和检验要求。", design_stage_available=False, target="配筋深化"),
    "approval": _input("approval", "设计、校核、审核、批准及当前施工版修订", "review", "项目校审链", "完成岗位分离校审并创建绑定当前快照的施工版修订。", target="成果交付"),
}


def _spec(
    category: str,
    rule_id: str,
    label: str,
    standard: str,
    scope: str,
    requires: list[str],
    *,
    implementation: str = "implemented",
    applicability: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "category": category,
        "ruleId": rule_id,
        "label": label,
        "standard": standard,
        "scope": scope,
        "requires": requires,
        "implementation": implementation,
        "implemented": implementation in {"implemented", "screening"},
        "applicability": applicability or [],
        "note": note,
    }


# V3.52 design-stage verification programme.  The catalogue lists numerical
# checks, screening checks and mandatory specialist reviews separately.  A row
# stays visible when evidence is missing; the UI must never infer a pass from an
# absent numerical record.
VERIFICATION_CATALOG: list[dict[str, Any]] = [
    # Strength and member capacity
    _spec("strength", "WALL_FLEXURE", "围护墙抗弯承载力", "GB/T 50010-2010（2024年局部修订）", "wall", ["calculation", "wall_internal_force", "wall", "rebar"]),
    _spec("strength", "WALL_SHEAR", "围护墙斜截面抗剪承载力", "GB/T 50010-2010（2024年局部修订）", "wall", ["calculation", "wall_internal_force", "wall", "rebar"]),
    _spec("strength", "WALL_AXIAL_FLEXURE", "围护墙轴力—弯矩组合承载力", "GB/T 50010-2010（2024年局部修订）", "wall", ["calculation", "wall_internal_force", "wall", "rebar"], implementation="screening"),
    _spec("strength", "WALL_JOINT_SHEAR", "地下连续墙槽段接头抗剪与传力", "JGJ 120-2012 / GB/T 50010", "wall", ["wall", "wall_joint"], implementation="specialist_review"),
    _spec("strength", "WALL_MIN_REBAR", "围护墙最小配筋与构造钢筋", "GB/T 50010-2010（2024年局部修订）", "wall", ["wall", "rebar"], implementation="screening"),
    _spec("strength", "CROWN_BEAM_FLEXURE", "冠梁抗弯承载力", "GB/T 50010-2010（2024年局部修订）", "crown_beam", ["calculation", "wale", "rebar"], applicability=["wale"]),
    _spec("strength", "CROWN_BEAM_SHEAR", "冠梁抗剪承载力", "GB/T 50010-2010（2024年局部修订）", "crown_beam", ["calculation", "wale", "rebar"], applicability=["wale"]),
    _spec("strength", "WALE_FLEXURE", "围檩抗弯承载力", "GB/T 50010-2010（2024年局部修订）", "wale", ["calculation", "wale", "rebar"], applicability=["wale"]),
    _spec("strength", "WALE_SHEAR", "围檩抗剪承载力", "GB/T 50010-2010（2024年局部修订）", "wale", ["calculation", "wale", "rebar"], applicability=["wale"]),
    _spec("strength", "WALE_TORSION", "围檩扭转及弯剪扭组合", "GB/T 50010-2010（2024年局部修订）", "wale", ["calculation", "wale", "support_node", "rebar"], implementation="screening", applicability=["wale"]),
    _spec("strength", "WALE_BEARING", "围檩及节点局部承压", "GB/T 50010-2010（2024年局部修订）", "node", ["calculation", "wale", "support_node"], applicability=["support_node"]),
    _spec("strength", "SUPPORT_AXIAL", "水平支撑轴压承载力", "JGJ 120-2012 / GB/T 50010 / GB 50017", "support", ["calculation", "support_force", "support"], applicability=["support"]),
    _spec("strength", "SUPPORT_COMBINED", "水平支撑压弯、偏心与施工效应组合", "JGJ 120-2012 / GB/T 50010 / GB 50017", "support", ["calculation", "support_force", "support"], implementation="screening", applicability=["support"]),
    _spec("strength", "SUPPORT_NODE", "支撑连接节点承载力与锚固", "JGJ 120-2012 / GB/T 50010 / GB 50017", "node", ["calculation", "support_force", "support_node"], applicability=["support_node"]),
    _spec("strength", "COLUMN_AXIAL", "临时立柱轴压承载力", "JGJ 120-2012 / GB 50017", "column", ["calculation", "column"], applicability=["column"]),
    _spec("strength", "COLUMN_FOUNDATION", "立柱基础承载力、偏心与冲切", "GB 50007-2011 / GB/T 50010", "foundation", ["calculation", "column", "bearing_capacity"], implementation="screening", applicability=["column"]),
    _spec("strength", "LOCAL_NODE_SUBMODEL", "关键节点局部承压、劈裂与锚固子模型", "GB/T 50010 / GB 50017 专项复核", "node", ["calculation", "support_node"], implementation="screening", applicability=["support_node"]),

    # Stiffness and serviceability
    _spec("stiffness", "WALL_DISPLACEMENT", "围护墙最大水平位移", "JGJ 120-2012 / GB 50497-2019", "wall", ["calculation", "wall_internal_force", "adjacent_environment"]),
    _spec("stiffness", "WALL_DEFLECTION_PROFILE", "围护墙逐深度变形曲线与控制工况", "JGJ 120-2012", "wall", ["calculation", "wall_internal_force", "soil_stiffness"]),
    _spec("stiffness", "GROUND_SETTLEMENT", "坑外地表沉降", "JGJ 120-2012 / GB 50497-2019", "system", ["calculation", "soil_stiffness", "adjacent_environment"], implementation="screening"),
    _spec("stiffness", "ADJACENT_DEFORMATION", "邻近建筑、道路和管线变形", "JGJ 120-2012 / GB 50497-2019", "environment", ["calculation", "soil_stiffness", "adjacent_environment"], implementation="specialist_review"),
    _spec("stiffness", "WALE_DEFLECTION", "围檩挠度", "JGJ 120-2012 / GB/T 50010", "wale", ["calculation", "wale"], applicability=["wale"]),
    _spec("stiffness", "SUPPORT_DEFORMATION", "水平支撑轴向变形与节点侧移", "JGJ 120-2012", "support", ["calculation", "support_force", "support"], implementation="screening", applicability=["support"]),
    _spec("stiffness", "WALL_CRACK_CONTROL", "围护墙裂缝宽度与防水控制", "GB/T 50010-2010（2024年局部修订）", "wall", ["calculation", "wall_internal_force", "wall", "rebar", "durability"], implementation="screening"),
    _spec("stiffness", "BEAM_SUPPORT_CRACK_CONTROL", "冠梁、围檩和混凝土支撑裂缝控制", "GB/T 50010-2010（2024年局部修订）", "member", ["calculation", "rebar", "durability"], implementation="screening"),
    _spec("stiffness", "LONG_TERM_DEFORMATION", "长期效应、温度与收缩徐变变形", "GB/T 50010-2010（2024年局部修订）", "system", ["calculation", "durability"], implementation="screening"),

    # Geotechnical and structural stability
    _spec("stability", "SUPPORT_STABILITY", "水平支撑整体与局部稳定", "JGJ 120-2012 / GB 50017", "support", ["calculation", "support_force", "support"], applicability=["support"]),
    _spec("stability", "COLUMN_STABILITY", "临时立柱整体稳定与计算长度", "JGJ 120-2012 / GB 50017", "column", ["calculation", "column"], applicability=["column"]),
    _spec("stability", "EMBEDMENT_STABILITY", "围护墙嵌固稳定", "JGJ 120-2012 / GB 55003-2021", "wall", ["calculation", "wall", "soil_strength", "groundwater"]),
    _spec("stability", "BOTTOM_HEAVE", "坑底抗隆起稳定", "JGJ 120-2012 / GB 55003-2021", "pit_bottom", ["calculation", "soil_strength", "groundwater"]),
    _spec("stability", "GLOBAL_STABILITY", "支护体系整体圆弧滑动稳定", "JGJ 120-2012 / GB 55003-2021", "system", ["calculation", "soil_strength", "groundwater"]),
    _spec("stability", "LOCAL_WEAK_LAYER", "坑底软弱下卧层与局部隆起", "JGJ 120-2012", "pit_bottom", ["calculation", "soil_strength"], implementation="screening"),
    _spec("stability", "WALL_ROTATIONAL_STABILITY", "围护墙底部踢脚、转动和平衡稳定", "JGJ 120-2012", "wall", ["calculation", "wall", "soil_strength"], implementation="screening"),
    _spec("stability", "BEARING_CAPACITY", "立柱基础地基承载与稳定", "GB 50007-2011", "foundation", ["calculation", "column", "bearing_capacity"], applicability=["column"]),
    _spec("stability", "CONSTRUCTION_STAGE_STABILITY", "各开挖、换撑与拆撑工况体系稳定", "JGJ 120-2012", "system", ["calculation", "support"], implementation="screening"),
    _spec("stability", "REPLACEMENT_PATH_STABILITY", "换撑、拆撑与地下结构传力路径稳定", "JGJ 120-2012", "system", ["calculation", "support"], implementation="screening"),

    # Groundwater and dewatering
    _spec("hydraulic", "SEEPAGE", "坑底渗流稳定与出口坡降", "JGJ 120-2012 / GB 55003-2021", "pit_bottom", ["calculation", "groundwater", "permeability"]),
    _spec("hydraulic", "PIPING", "承压水突涌与隔水层抗浮稳定", "JGJ 120-2012 / GB 55003-2021", "pit_bottom", ["calculation", "confined_head", "soil_strength"]),
    _spec("hydraulic", "WATERPROOF_CUTOFF", "围护墙止水帷幕深度与接缝防渗", "JGJ 120-2012", "wall", ["wall", "wall_joint", "groundwater", "permeability"], implementation="specialist_review"),
    _spec("hydraulic", "DEWATERING_CAPACITY", "降水/减压能力与备用能力", "JGJ 120-2012 / JGJ/T 111", "system", ["groundwater", "permeability", "dewatering_plan"], implementation="specialist_review"),
    _spec("hydraulic", "DEWATERING_FAILURE", "停泵、断电和水位回升不利工况", "JGJ 120-2012 / GB 50497-2019", "system", ["calculation", "dewatering_plan"], implementation="screening"),
    _spec("hydraulic", "DRAWDOWN_INFLUENCE", "坑外降深、地层损失与环境影响", "JGJ 120-2012 / GB 50497-2019", "environment", ["groundwater", "permeability", "dewatering_plan", "adjacent_environment"], implementation="specialist_review"),

    # Constructability, durability and controlled issue
    _spec("constructability", "DIAPHRAGM_WALL_THICKNESS", "地下连续墙墙厚、垂直度与成槽控制", "JGJ 120-2012 / GB 50299", "wall", ["wall"], implementation="screening"),
    _spec("constructability", "WALL_PANEL_JOINT", "施工槽段分幅、接头与止水构造", "JGJ 120-2012 / GB 50299", "wall", ["wall", "wall_joint"], implementation="specialist_review"),
    _spec("constructability", "CONCRETE_DURABILITY", "混凝土强度、耐久性与保护层", "GB 55008-2021 / GB/T 50010", "member", ["design_basis", "durability", "wall"], implementation="screening"),
    _spec("constructability", "REBAR_CONGESTION", "钢筋净距、层数、可穿入性与节点拥挤", "GB/T 50010 / 施工深化要求", "member", ["rebar", "wall", "support_node"], implementation="screening"),
    _spec("constructability", "CAGE_HOISTING", "钢筋笼分节、吊点、吊装强度与变形", "GB 50299 / 吊装专项", "wall", ["rebar", "wall_joint", "cage_lifting_plan"], implementation="screening"),
    _spec("constructability", "MECHANICAL_COUPLER", "机械连接等级、错开、丝头与检验批", "JGJ 107 / GB 55008", "member", ["rebar", "coupler_product"], implementation="specialist_review"),
    _spec("constructability", "SUPPORT_PRELOAD", "支撑预加轴力、温度与安装偏差", "JGJ 120-2012", "support", ["calculation", "support"], implementation="screening", applicability=["support"]),
    _spec("constructability", "SUPPORT_REMOVAL", "支撑安装、换撑、拆除顺序与监测条件", "JGJ 120-2012 / GB 50497-2019", "system", ["calculation", "support", "monitoring_limits"], implementation="screening"),
    _spec("constructability", "MONITORING_PLAN", "监测项目、测点、频率与报警闭环", "GB 50497-2019", "system", ["monitoring_limits", "adjacent_environment"], implementation="specialist_review"),
]


def _has_stage_results(project: Project) -> bool:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest is None or not latest.stage_results:
        return False
    state = dict((project.advanced_engineering or {}).get("calculationState") or {})
    return not bool(state.get("requiresRecalculation"))


def _availability(project: Project) -> dict[str, bool]:
    ret = project.retaining_system
    latest = project.calculation_results[-1] if project.calculation_results else None
    strata = list(project.strata or [])
    soil_strength = bool(strata) and all(
        getattr(layer.parameters, "cohesion", None) is not None
        and getattr(layer.parameters, "friction_angle", None) is not None
        and getattr(layer.parameters, "unit_weight", None) is not None
        for layer in strata
    )
    soil_stiffness = bool(strata) and all(
        any(
            getattr(layer.parameters, key, None) is not None
            for key in ("horizontal_subgrade_modulus", "elastic_modulus", "compression_modulus")
        )
        for layer in strata
    )
    permeability = bool(strata) and all(
        any(getattr(layer.parameters, key, None) is not None for key in ("permeability_x", "permeability_y", "permeability_z"))
        for layer in strata
    )
    groundwater_records = any(bool(getattr(borehole, "water_levels", None)) for borehole in project.boreholes)
    has_groundwater = groundwater_records or (
        bool(project.design_settings.design_basis_confirmed)
        and project.design_settings.groundwater_level is not None
    )
    walls = list(ret.diaphragm_walls or []) if ret else []
    wall_joint = bool(walls) and all(bool(getattr(wall, "construction_panels", None)) for wall in walls)
    current_calculation = _has_stage_results(project)
    stage_results = list(latest.stage_results or []) if latest and current_calculation else []
    assurance = dict(getattr(latest, "calculation_assurance", None) or {}) if latest else {}
    calculation_assurance = current_calculation and bool(assurance) and str(assurance.get("status") or "manual_review") != "fail"
    advanced = dict(project.advanced_engineering or {})
    detailed = getattr(latest, "stability_detailed_result", None) if latest else None
    has_dewatering_plan = bool(advanced.get("dewateringPlan")) or bool(getattr(detailed, "dewatering_wells", None)) or bool(getattr(detailed, "depressurization_wells", None))
    return {
        "design_basis": bool(project.design_settings.design_basis_confirmed),
        "excavation": bool(project.excavation and project.excavation.outline and len(project.excavation.outline.points) >= 3),
        "surcharge": project.design_settings.surcharge is not None,
        "soil_strength": soil_strength,
        "soil_stiffness": soil_stiffness,
        "permeability": permeability,
        "groundwater": has_groundwater,
        "confined_head": project.design_settings.confined_water_head_elevation is not None,
        "bearing_capacity": project.design_settings.bearing_capacity_kpa is not None,
        "adjacent_environment": bool(project.design_settings.design_basis_confirmed and project.design_settings.surrounding_environment_level),
        "seismic": bool(project.design_settings.design_basis_confirmed and project.design_settings.seismic_grade),
        "durability": bool(project.design_settings.design_basis_confirmed and project.design_settings.default_cover_mm and project.design_settings.service_life_years),
        "wall": bool(walls),
        "wall_joint": wall_joint,
        "wale": bool(ret and (ret.crown_beams or ret.wale_beams or ret.ring_beams)),
        "support": bool(ret and ret.supports),
        "support_node": bool(ret and ret.support_nodes),
        "column": bool(ret and ret.columns),
        "rebar": bool(ret and ret.rebar_design_scheme and ret.rebar_design_scheme.get("wallZones")),
        "calculation": current_calculation,
        "calculation_assurance": calculation_assurance,
        "wall_internal_force": any(getattr(stage, "wall_internal_force", None) is not None for stage in stage_results),
        "support_force": any(bool(getattr(stage, "support_forces", None)) for stage in stage_results) or bool(ret and any(item.design_axial_force is not None for item in ret.supports)),
        "dewatering_plan": has_dewatering_plan,
        "monitoring_limits": bool(
            project.design_settings.monitoring_threshold_source == "auto_screening"
            or (
                project.design_settings.monitoring_wall_displacement_warning_mm is not None
                and project.design_settings.monitoring_wall_displacement_alarm_mm is not None
            )
        ),
        "cage_lifting_plan": bool(advanced.get("cageLiftingPlan") or advanced.get("craneLogistics")),
        "coupler_product": bool(advanced.get("couplerProductSelection")),
        "approval": bool(project.review_workflow.status == "approved"),
        "critical_node_geometry": bool(ret and ret.support_nodes and any(getattr(node, "bearing_plate", None) for node in ret.support_nodes)),
    }


def input_availability(project: Project) -> dict[str, bool]:
    """Return one reusable input-availability snapshot for presentation builders."""
    return _availability(project)


def input_requirement_details(
    project: Project,
    codes: list[str] | None = None,
    *,
    availability: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    available_map = availability or _availability(project)
    selected = codes if codes is not None else list(INPUT_REQUIREMENTS)
    rows: list[dict[str, Any]] = []
    for code in selected:
        metadata = dict(INPUT_REQUIREMENTS.get(str(code)) or {
            "code": str(code), "label": str(code), "stage": "design", "stageLabel": "设计阶段",
            "provider": "项目设计", "designStageAvailable": True,
            "action": "补充项目输入。", "target": "工程输入",
        })
        metadata["available"] = bool(available_map.get(str(code), False))
        rows.append(metadata)
    return rows


def missing_evidence_record(
    project: Project,
    spec: dict[str, Any],
    *,
    availability: dict[str, bool] | None = None,
) -> dict[str, Any]:
    available = availability or _availability(project)
    applicability = [str(code) for code in spec.get("applicability", [])]
    if applicability and not any(available.get(code, False) for code in applicability):
        return {
            "evidenceState": "not_applicable",
            "implementationState": str(spec.get("implementation") or "implemented"),
            "missingInputs": [],
            "missingInputDetails": [],
            "canCompleteAtDesignStage": True,
            "message": "当前支护体系没有该类对象，本项目记为不适用；对象新增后将自动纳入。",
            "nextAction": "无需处理；若后续增加相应构件，重新运行计算。",
        }

    missing = [str(name) for name in spec.get("requires", []) if not available.get(str(name), False)]
    implementation = str(spec.get("implementation") or ("implemented" if spec.get("implemented") else "specialist_review"))
    details = [row for row in input_requirement_details(project, missing, availability=available)]
    if missing:
        state = "missing_input"
        labels = "、".join(str(row.get("label")) for row in details)
        message = "缺少：" + labels
        next_action = "；".join(dict.fromkeys(str(row.get("action")) for row in details))
    elif implementation == "specialist_review":
        state = "manual_review"
        message = "输入已具备，该项需项目专项设计或人工校审形成正式结论。"
        next_action = str(spec.get("note") or "上传专项验算/校审结论并绑定当前设计快照。")
    else:
        state = "not_calculated"
        message = "所需输入已具备，当前结果未形成该项可追溯记录。"
        next_action = "重新运行当前方案计算；若仍无记录，按该项规范执行专项复核。"
    return {
        "evidenceState": state,
        "implementationState": implementation,
        "missingInputs": missing,
        "missingInputDetails": details,
        "canCompleteAtDesignStage": all(bool(row.get("designStageAvailable")) for row in details) if details else True,
        "message": message,
        "nextAction": next_action,
    }


def coverage_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    states = {
        "calculated": 0,
        "missing_input": 0,
        "not_calculated": 0,
        "not_implemented": 0,
        "manual_review": 0,
        "not_applicable": 0,
    }
    for row in records:
        key = str(row.get("evidenceState") or "manual_review")
        states[key] = states.get(key, 0) + 1
    applicable_total = max(len(records) - states.get("not_applicable", 0), 1)
    unresolved = (
        states.get("missing_input", 0)
        + states.get("not_calculated", 0)
        + states.get("not_implemented", 0)
        + states.get("manual_review", 0)
    )
    return {
        "counts": states,
        "applicableCount": applicable_total,
        "calculatedCoverageRatio": round(states.get("calculated", 0) / applicable_total, 4),
        "complete": unresolved == 0,
    }
