from __future__ import annotations

from collections import Counter
from typing import Any

from app.rules.registry import list_rules
from app.schemas.domain import Project
from app.version import RULE_SET_VERSION, SOFTWARE_VERSION


STANDARD_CATALOG: list[dict[str, Any]] = [
    {
        "id": "GB55003-2021",
        "code": "GB 55003-2021",
        "name": "建筑与市政地基基础通用规范",
        "level": "mandatory_all",
        "levelLabel": "全文强制",
        "effectiveDate": "2022-01-01",
        "priority": 1,
        "appliesTo": ["boreholes", "geology", "excavation", "retaining", "calculation", "assurance"],
        "implementedScope": "地基基础、基坑支护、地下水、环境影响与安全控制的强制性总入口和人工复核门禁。",
        "boundary": "系统只实现可参数化子集；与其他标准不一致时应优先执行通用规范。",
        "sourceUrl": "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2021/art_17339_761185.html",
    },
    {
        "id": "JGJ120-2012",
        "code": "JGJ 120-2012",
        "name": "建筑基坑支护技术规程",
        "level": "primary_design",
        "levelLabel": "基坑主控规程",
        "effectiveDate": "2012-10-01",
        "priority": 2,
        "appliesTo": ["settings", "boreholes", "geology", "excavation", "retaining", "calculation", "assurance", "export"],
        "implementedScope": "安全等级、水平荷载、土水压力、弹性支点法、地下连续墙、内支撑、嵌固、变形、抗隆起、渗流、整体稳定和地下水控制子集。",
        "boundary": "条文适用条件、地区标准、复杂空间效应和专项有限元分析仍需专业复核。",
        "sourceUrl": "https://www.mohurd.gov.cn/gongkai/fdzdgknr/bzgg/index.html",
    },
    {
        "id": "GB50007-2011",
        "code": "GB 50007-2011",
        "name": "建筑地基基础设计规范",
        "level": "supporting_design",
        "levelLabel": "地基基础设计",
        "effectiveDate": "2012-08-01",
        "priority": 3,
        "appliesTo": ["boreholes", "geology", "retaining", "calculation", "assurance"],
        "implementedScope": "地基承载力、立柱基础、软弱下卧层、变形和邻近基础影响的筛查入口。",
        "boundary": "沉降、偏心、群桩、负摩阻、抗浮和复杂地基处理需专项计算。",
        "sourceUrl": "https://openstd.samr.gov.cn/bzgk/std/index",
    },
    {
        "id": "GB50009-2012",
        "code": "GB 50009-2012",
        "name": "建筑结构荷载规范",
        "level": "supporting_design",
        "levelLabel": "作用与组合",
        "effectiveDate": "2012-10-01",
        "priority": 4,
        "appliesTo": ["settings", "excavation", "retaining", "calculation"],
        "implementedScope": "地面超载、施工荷载、作用代表值与基本组合参数的记录和设计效应接口。",
        "boundary": "项目专用施工荷载、车辆荷载、堆载和偶然作用应由设计人员确认。",
        "sourceUrl": "https://openstd.samr.gov.cn/bzgk/std/index",
    },
    {
        "id": "GBT50010-2010-2024",
        "code": "GB 50010-2010（2015年版，2024年局部修订）",
        "name": "混凝土结构设计规范",
        "level": "supporting_design",
        "levelLabel": "混凝土设计",
        "effectiveDate": "2024局部修订",
        "priority": 5,
        "appliesTo": ["retaining", "calculation", "assurance", "export"],
        "implementedScope": "地连墙、冠梁、围檩、混凝土支撑和节点的受弯、受剪、轴压、最小配筋、裂缝、锚固与搭接筛查。",
        "boundary": "复杂受力、抗震、疲劳、节点非线性、施工阶段裂缝和完整构造应按正式标准复核。",
        "sourceUrl": "https://www.mohurd.gov.cn/cms_files/filemanager/1150240553/attach/202411/0b5a540a-06fa-4bb2-ba99-e38d235c75e1.pdf",
    },
    {
        "id": "GB55008-2021",
        "code": "GB 55008-2021",
        "name": "混凝土结构通用规范",
        "level": "mandatory_all",
        "levelLabel": "全文强制",
        "effectiveDate": "2022-04-01",
        "priority": 1,
        "appliesTo": ["retaining", "calculation", "assurance", "export"],
        "implementedScope": "混凝土结构安全、耐久、承载能力和正常使用的强制性门禁。",
        "boundary": "系统仅将已实现的计算和构造子集映射到门禁，未覆盖条文须人工复核。",
        "sourceUrl": "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2021/art_17339_762454.html09",
    },
    {
        "id": "GB50017-2017",
        "code": "GB 50017-2017",
        "name": "钢结构设计标准",
        "level": "supporting_design",
        "levelLabel": "钢支撑设计",
        "effectiveDate": "2018-07-01",
        "priority": 5,
        "appliesTo": ["retaining", "calculation", "assurance", "export"],
        "implementedScope": "钢管/型钢支撑轴压强度、稳定、长细比和节点审查接口。",
        "boundary": "连接、焊缝、局部稳定、初弯曲、残余应力和施工温度效应需完整复核。",
        "sourceUrl": "https://openstd.samr.gov.cn/bzgk/std/index",
    },
    {
        "id": "GB50068-2018",
        "code": "GB 50068-2018",
        "name": "建筑结构可靠性设计统一标准",
        "level": "supporting_design",
        "levelLabel": "可靠性与极限状态",
        "effectiveDate": "2019-04-01",
        "priority": 3,
        "appliesTo": ["settings", "calculation", "assurance"],
        "implementedScope": "安全等级、设计使用年限、极限状态和重要性系数的统一入口。",
        "boundary": "概率校准和完整可靠指标未在当前版本内实现。",
        "sourceUrl": "https://openstd.samr.gov.cn/bzgk/std/index",
    },
    {
        "id": "GB50497-2019",
        "code": "GB 50497-2019",
        "name": "建筑基坑工程监测技术标准",
        "level": "supporting_design",
        "levelLabel": "监测与反馈",
        "effectiveDate": "2020-06-01",
        "priority": 4,
        "appliesTo": ["calculation", "assurance", "export"],
        "implementedScope": "监测项目、预警阈值、数据导入、趋势识别和参数反演接口。",
        "boundary": "监测方案、测点布置、频率、报警值与应急响应应按项目条件和地方规定确定。",
        "sourceUrl": "https://openstd.samr.gov.cn/bzgk/std/index",
    },
]


PROCESS_MATRIX: list[dict[str, Any]] = [
    {
        "workflowStep": "settings",
        "index": 1,
        "title": "项目设定与设计等级",
        "keyCalculations": ["安全等级与重要性系数", "地下水位与地面超载", "设计参数和单位完整性", "规则集版本锁定"],
        "standardIds": ["GB55003-2021", "JGJ120-2012", "GB50009-2012", "GB50068-2018"],
        "clauseFocus": ["JGJ 120 基本规定与安全等级", "GB 50009 荷载分类和组合", "GB 50068 极限状态和可靠性"],
        "outputs": ["设计设置快照", "规则集版本", "项目输入完整性清单"],
        "implementationLevel": "implemented_with_manual_review",
    },
    {
        "workflowStep": "boreholes",
        "index": 2,
        "title": "勘察资料与岩土参数",
        "keyCalculations": ["钻孔与地层合并", "重度、黏聚力、内摩擦角、模量和渗透参数检查", "地下水类型与水位识别", "参数缺失与异常值诊断"],
        "standardIds": ["GB55003-2021", "GB50007-2011", "JGJ120-2012"],
        "clauseFocus": ["地基基础通用规范的勘察与设计资料要求", "GB 50007 岩土参数与地基基础设计资料", "JGJ 120 支护设计资料要求"],
        "outputs": ["标准化钻孔", "地层参数表", "参数质量问题清单"],
        "implementationLevel": "implemented_with_manual_review",
    },
    {
        "workflowStep": "geology",
        "index": 3,
        "title": "三维地质与水文地质模型",
        "keyCalculations": ["地层面插值", "场地覆盖范围检查", "代表性剖面提取", "水位与承压层识别", "软弱夹层识别"],
        "standardIds": ["GB55003-2021", "GB50007-2011", "JGJ120-2012"],
        "clauseFocus": ["地基与地下水控制通用要求", "工程地质和水文地质条件", "基坑周边环境和不利地层识别"],
        "outputs": ["三维地层面", "计算剖面", "地下水和软弱层风险"],
        "implementationLevel": "screening_model",
    },
    {
        "workflowStep": "excavation",
        "index": 4,
        "title": "基坑几何、边段与外部作用",
        "keyCalculations": ["轮廓闭合与自交检查", "开挖深度和边段法向", "周边超载影响区", "出土口、坡道、保护区和邻近建构筑物约束"],
        "standardIds": ["GB55003-2021", "JGJ120-2012", "GB50009-2012"],
        "clauseFocus": ["基坑支护总体设计", "水平荷载与地面超载", "周边环境安全"],
        "outputs": ["闭合基坑轮廓", "设计边段", "荷载与障碍物模型"],
        "implementationLevel": "implemented",
    },
    {
        "workflowStep": "retaining",
        "index": 5,
        "title": "围护、围檩、支撑与立柱方案",
        "keyCalculations": ["墙厚和墙深初选", "支撑层数与标高", "短边优先支撑拓扑", "角撑、立柱和通道避让", "A/B/C 多目标候选", "构件截面初选"],
        "standardIds": ["GB55003-2021", "JGJ120-2012", "GBT50010-2010-2024", "GB55008-2021", "GB50017-2017", "GB50007-2011"],
        "clauseFocus": ["JGJ 120 第4章支护结构、地下连续墙与内支撑", "混凝土/钢构件设计与构造", "立柱基础承载力"],
        "outputs": ["围护结构模型", "候选方案", "支撑布置质量评分", "初选截面与配筋"],
        "implementationLevel": "preliminary_design",
    },
    {
        "workflowStep": "calculation",
        "index": 6,
        "title": "分阶段计算、构件设计与稳定性",
        "keyCalculations": ["主动/被动土压力和水压力", "施工阶段激活/失活", "墙体弹性地基梁", "围檩连续梁与支撑反力", "墙-围檩-支撑全局耦合", "支撑轴力及施工效应", "受弯/受剪/轴压和配筋", "嵌固、抗隆起、渗流、突涌和整体稳定", "力与弯矩平衡诊断"],
        "standardIds": ["GB55003-2021", "JGJ120-2012", "GB50009-2012", "GB50068-2018", "GBT50010-2010-2024", "GB55008-2021", "GB50017-2017", "GB50007-2011"],
        "clauseFocus": ["JGJ 120 水平荷载、弹性支点法、稳定与地下水控制", "荷载组合与设计效应", "混凝土和钢构件极限状态", "地基承载力"],
        "outputs": ["工况结果", "内力与位移包络", "构件利用率", "稳定安全系数", "规范检查台账", "计算追溯链"],
        "implementationLevel": "calculation_subset",
    },
    {
        "workflowStep": "assurance",
        "index": 7,
        "title": "规范审查、风险闭环与监测反馈",
        "keyCalculations": ["规则结果聚合", "Fail/Warning/人工复核归并", "设计输入—公式—结果—图纸追溯", "监测阈值与趋势", "参数反演", "正式发行闸门"],
        "standardIds": ["GB55003-2021", "JGJ120-2012", "GB50497-2019", "GB55008-2021", "GB50068-2018"],
        "clauseFocus": ["强制性通用规范优先", "基坑监测与信息化施工", "承载能力和正常使用状态", "危大工程专项复核"],
        "outputs": ["规范流程矩阵", "问题中心", "人工复核清单", "监测反馈", "发行门禁状态"],
        "implementationLevel": "implemented_with_signoff",
    },
    {
        "workflowStep": "export",
        "index": 8,
        "title": "计算书、施工图、BIM 与钢筋加工交付",
        "keyCalculations": ["结果与图纸几何一致性", "钢筋编号/BBS/分段/套筒/净距", "图纸完整性和审签", "IFC 语义和单位", "修订、哈希和交付清单"],
        "standardIds": ["JGJ120-2012", "GBT50010-2010-2024", "GB55008-2021", "GB50017-2017", "GB50497-2019"],
        "clauseFocus": ["支护结构施工图表达", "混凝土和钢结构构造", "监测和施工阶段要求", "企业图层/图签/审签规则"],
        "outputs": ["DOCX 计算书", "CAD/PDF 图纸包", "IFC", "钢筋深化 ZIP", "规范与计算追溯 JSON"],
        "implementationLevel": "delivery_with_gate",
    },
]


def _flatten_checks(project: Project | None) -> list[dict[str, Any]]:
    if project is None or not project.calculation_results:
        return []
    latest = project.calculation_results[-1]
    checks = list(latest.checks or [])
    for stage in latest.stage_results:
        checks.extend(stage.checks or [])
    return [item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True) for item in checks]


def _rule_matches_standard(rule_id: str, standard_id: str) -> bool:
    token = rule_id.upper().replace(" ", "")
    mapping = {
        "GB55003-2021": ("GB55003",),
        "JGJ120-2012": ("JGJ120",),
        "GB50007-2011": ("GB50007",),
        "GB50009-2012": ("GB50009",),
        "GBT50010-2010-2024": ("GB50010", "GBT50010"),
        "GB55008-2021": ("GB55008",),
        "GB50017-2017": ("GB50017",),
        "GB50068-2018": ("GB50068",),
        "GB50497-2019": ("GB50497", "MONITOR"),
    }
    return any(prefix in token for prefix in mapping.get(standard_id, (standard_id.replace("-", ""),)))


STEP_APPLICABLE_TARGETS: dict[str, set[str]] = {
    "settings": {"project", "calculationcase", "calculationresult"},
    "boreholes": {"project", "geologicallayer", "excavationmodel"},
    "geology": {"project", "geologicallayer", "excavationmodel", "retainingsystem"},
    "excavation": {"project", "excavationmodel", "calculationcase"},
    "retaining": {"retainingsystem", "diaphragmwallpanel", "supportelement", "columnelement", "supportfoundation"},
    "calculation": {"calculationcase", "calculationresult", "excavationmodel", "diaphragmwallpanel", "supportelement", "columnelement", "supportfoundation", "project", "retainingsystem"},
    "assurance": {"calculationresult", "project", "excavationmodel", "diaphragmwallpanel", "supportelement", "columnelement", "supportfoundation", "retainingsystem"},
    "export": {"diaphragmwallpanel", "supportelement", "columnelement", "supportfoundation", "retainingsystem", "calculationresult"},
}


def _rule_relevant_to_step(rule: dict[str, Any], workflow_step: str) -> bool:
    targets = {str(item).lower() for item in rule.get("applicableTo", [])}
    expected = STEP_APPLICABLE_TARGETS.get(workflow_step, set())
    return bool(targets & expected)


def _step_status(checks: list[dict[str, Any]], standard_ids: list[str], project: Project | None) -> tuple[str, dict[str, int]]:
    matched = [c for c in checks if any(_rule_matches_standard(str(c.get("ruleId") or c.get("rule_id") or ""), sid) for sid in standard_ids)]
    counts = Counter(str(c.get("status") or "manual_review") for c in matched)
    if counts.get("fail"):
        status = "fail"
    elif counts.get("warning"):
        status = "warning"
    elif counts.get("manual_review"):
        status = "manual_review"
    elif matched:
        status = "pass"
    elif project and project.calculation_results:
        status = "not_covered"
    else:
        status = "not_run"
    return status, dict(counts)


def build_standards_process_matrix(project: Project | None = None) -> dict[str, Any]:
    checks = _flatten_checks(project)
    catalogue = {item["id"]: item for item in STANDARD_CATALOG}
    steps: list[dict[str, Any]] = []
    for item in PROCESS_MATRIX:
        status, counts = _step_status(checks, item["standardIds"], project)
        rules = [
            rule for rule in list_rules()
            if any(_rule_matches_standard(str(rule.get("ruleId") or ""), sid) for sid in item["standardIds"])
            and _rule_relevant_to_step(rule, item["workflowStep"])
        ]
        standards = [catalogue[sid] for sid in item["standardIds"] if sid in catalogue]
        steps.append({
            **item,
            "status": status,
            "checkSummary": counts,
            "standardRefs": standards,
            "ruleCount": len(rules),
            "rules": rules,
            "highlight": "critical" if any(std["level"] == "mandatory_all" for std in standards) else "primary",
        })
    return {
        "schemaVersion": "1.0",
        "softwareVersion": SOFTWARE_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "projectId": project.id if project else None,
        "catalog": STANDARD_CATALOG,
        "steps": steps,
        "precedence": [
            "GB 55003-2021、GB 55008-2021 等全文强制性工程建设规范优先。",
            "JGJ 120-2012 作为建筑基坑支护的主要专业规程，与通用规范不一致时执行通用规范。",
            "GB 50007、GB 50009、GB 50010、GB 50017、GB 50068 等用于相应专业计算与构件设计。",
            "地方标准、勘察报告、审图意见、专家论证和企业标准应在项目级规则集中补充。",
        ],
        "usageNote": "矩阵展示软件已实现的规则子集和人工复核边界，不构成对标准全文的替代。",
    }


def build_online_documentation() -> dict[str, Any]:
    matrix = build_standards_process_matrix()
    return {
        "title": "PitGuard 在线设计与计算文档",
        "version": SOFTWARE_VERSION,
        "chapters": [
            {"id": "workflow", "title": "操作流程", "summary": "从项目资料、地质模型、基坑几何、方案设计、分阶段计算、闭环审查到成果发行的完整工作流。"},
            {"id": "principles", "title": "计算原理", "summary": "说明土水压力、弹性地基梁、围檩连续梁、全局耦合、构件承载力、稳定性和钢筋深化的输入、公式、输出及边界。"},
            {"id": "standards", "title": "流程—规范矩阵", "summary": "每个设计步骤对应主控规范、条文关注点、已实现规则和人工复核项。"},
            {"id": "deliverables", "title": "成果与文件使用", "summary": "说明 CAD/PDF、IFC、DOCX、项目 JSON 和钢筋深化 ZIP 的用途、单位与审签要求。"},
        ],
        "calculationPrinciples": [
            {
                "name": "土压力与水压力",
                "inputs": "分层重度、黏聚力 c、内摩擦角 φ、地下水位、地面超载 q、开挖标高和水土分算设置",
                "method": "按分层有效应力积分形成主动/被动土压力与静水压力；阶段变化后重新积分并传递到墙体和围檩。",
                "equations": ["K_a=tan²(45°-φ/2)", "K_p=tan²(45°+φ/2)", "σ_h,a=K_a·σ'_v-2c√K_a+u", "σ_h,p=K_p·σ'_v+2c√K_p+u"],
                "assumptions": ["朗肯型极限土压力子集", "土层按水平分层离散", "复杂墙土界面、拱效应和空间效应进入人工复核"],
                "outputs": "压力—深度曲线、分层合力、作用点、墙面线荷载和阶段荷载快照",
                "verification": "核对分层积分、总水平力、作用点及水压力连续性；异常负压力按规则截断并记录。",
                "standards": ["JGJ 120-2012 3.4", "GB 55003-2021", "GB 50009-2012"]
            },
            {
                "name": "墙体内力与变形",
                "inputs": "侧压力、墙体截面 EI、坑内外土弹簧、嵌固深度、支撑标高和支撑刚度",
                "method": "按施工阶段组装弹性地基梁方程，考虑开挖卸载、支撑激活/拆除和坑内被动区变化。",
                "equations": ["[K_beam+K_soil+K_support]{u}={F}", "M=EI·κ", "V=dM/dz"],
                "assumptions": ["一维平面应变条带模型", "土弹簧采用参数化水平地基反力", "复杂三维效应需外部有限元或专项复核"],
                "outputs": "墙体位移、弯矩、剪力、支点反力、控制深度和阶段包络",
                "verification": "检查位移形态、反力和外荷载平衡、矩阵条件数、网格敏感性及相邻阶段连续性。",
                "standards": ["JGJ 120-2012 4.2", "GB 55003-2021"]
            },
            {
                "name": "围檩、支撑与全局耦合",
                "inputs": "墙面反力、围檩节点、支撑方向与长度、EA/EI、端部约束、预加轴力、温度和安装间隙",
                "method": "围檩按连续梁离散，支撑按轴向弹簧/杆单元，必要时与墙体水平自由度统一组装。",
                "equations": ["[K_wall+K_wale+K_strut]{u}={F}", "N=k_a·Δ+N_0+EA·α·ΔT/L", "M_w=EI_w·κ_w"],
                "assumptions": ["节点连接刚度按项目设置", "支撑初弯曲、偏心和局部节点通过施工效应或人工复核补充"],
                "outputs": "围檩 M/V/挠度、支撑轴力、节点反力、传力路径和平衡残差",
                "verification": "强制输出水平力与弯矩平衡、支撑反力总和、数量级审计和局部异常定位。",
                "standards": ["JGJ 120-2012", "GB 50017-2017", "GB 50010-2010（2015年版，2024年局部修订）"]
            },
            {
                "name": "数值求解质量与平衡诊断",
                "inputs": "全局刚度矩阵 K、荷载向量 F、位移向量 u、矩阵条件数、正则化参数和构件反力",
                "method": "对 K·u=F 计算有效矩阵残差、原始矩阵残差、矩阵对称误差和条件数；将软件数值质量与工程规范验算分开记录。",
                "equations": ["r=K·u-F", "η_r=||r||₂/max(||F||₂,1)", "η_s=||K-Kᵀ||_F/max(||K||_F,1)"],
                "assumptions": ["残差合格仅证明线性方程求解一致性", "正则化求解一律进入人工复核", "条件数阈值属于软件质量控制参数，不冒充规范条文"],
                "outputs": "相对残差、最大残差、矩阵对称误差、条件数、正则化状态和数值质量门禁结论",
                "verification": "数值质量门禁通过后仍需逐项执行土压力、稳定、承载力、变形和构造等规范验算。",
                "standards": ["PitGuard numerical quality gate（软件质量证据）"]
            },
            {
                "name": "混凝土与钢构件承载力",
                "inputs": "设计内力、截面尺寸、材料强度、保护层、有效高度、计算长度和连接条件",
                "method": "对墙、冠梁、围檩、混凝土支撑和钢支撑执行受弯、受剪、轴压、稳定和最小配筋筛查。",
                "equations": ["M≤α₁f_cbx(h₀-x/2)", "A_s=α₁f_cbx/f_y", "V≤V_Rd", "N≤φAf"],
                "assumptions": ["常规截面承载力子集", "复杂双向受力、节点非线性、疲劳和抗震按专项设计复核"],
                "outputs": "所需配筋、提供配筋、构件利用率、控制组合和构造复核项",
                "verification": "核对设计值单位、截面有效高度、材料强度、最小/最大配筋和控制工况追溯。",
                "standards": ["GB 55008-2021", "GB 50010-2010（2015年版，2024年局部修订）", "GB 50017-2017"]
            },
            {
                "name": "稳定性、地基与地下水",
                "inputs": "坑深、嵌固深度、土层强度、地基承载力、水头、帷幕深度、降水阶段和软弱层",
                "method": "对嵌固、抗隆起、渗流、承压水突涌、整体稳定和立柱基础承载力执行控制剖面搜索与筛查。",
                "equations": ["F_s=R/S", "p=N/A±M/W≤f_a", "F_heave=R_heave/S_heave", "F_piping=R_seep/S_head"],
                "assumptions": ["二维控制剖面与简化圆弧/水力路径", "复杂渗流场和土体非线性需专项数值分析"],
                "outputs": "各模式安全系数、控制剖面、控制土层、风险等级和处置建议",
                "verification": "至少复核一个不利剖面、软弱夹层、水位敏感性和降水失效工况。",
                "standards": ["GB 55003-2021", "JGJ 120-2012 4.2/7", "GB 50007-2011"]
            },
            {
                "name": "钢筋分区、逐根几何与加工深化",
                "inputs": "构件包络内力、截面、材料、保护层、分区边界、运输长度、接头和弯曲规则",
                "method": "由配筋需求生成分区钢筋组，再展开为逐根中心线、BBS、分段、套筒、钢筋笼和吊装单元。",
                "equations": ["A_s,prov≥A_s,req", "s_clear≥s_clear,min", "l_cut=l_center+l_anchor+l_lap+l_hook"],
                "assumptions": ["加工字段按通用格式输出", "企业弯曲表、接头工艺和设备字段需在导入前确认"],
                "outputs": "钢筋编号表、BBS、逐根几何、分段/接头/吊装计划、净距与碰撞检查、ZIP 成套包",
                "verification": "以 XLSX 为人工交接主文件，CSV 用于映射，JSON 用于机器语义；正式施工表达以 CAD/PDF 图纸包为准。",
                "standards": ["GB 55008-2021", "GB 50010-2010（2015年版，2024年局部修订）", "JGJ 120-2012 4.5"]
            },
        ],
        "fileGuide": [
            {"file": "正式图纸发行包 ZIP", "use": "CAD/PDF 图纸、目录、图签、审签、修订和质量门禁；面向正式审查和施工交付。"},
            {"file": "钢筋加工深化包 ZIP", "use": "XLSX/CSV 供翻样、加工和复核；JSON 供 BIM、设备接口和追溯；施工图需配合 CAD 图纸包。"},
            {"file": "DOCX 计算书", "use": "计算假定、公式、规范矩阵、工况、控制结果、问题清单和方案比选。"},
            {"file": "IFC", "use": "三维协调、构件语义和外部 BIM 查看；不同 profile 对应轻量、分析、详细和施工可视化。"},
            {"file": "项目完整 JSON", "use": "项目归档、迁移、二次开发和全量追溯，不作为直接施工文件。"},
        ],
        "standardsMatrix": matrix,
    }
