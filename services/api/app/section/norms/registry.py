"""Verified normative scopes and explicit unimplemented engineering modules.

Catalogue availability means the named equation is implemented and independently
unit tested. It never implies that the whole pit design has passed its checks.
"""
from __future__ import annotations

from copy import deepcopy

SOURCES = {
    "GB55001-2021": {
        "name": "工程结构通用规范", "effective": "2022-01-01",
        "authority": "强制性通用规范；作用组合与结构可靠度总体要求",
        "url": "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2021/art_17339_761192.html",
    },
    "GB55008-2021": {
        "name": "混凝土结构通用规范", "effective": "2022-04-01",
        "authority": "强制性通用规范；混凝土结构材料、承载力及构造要求",
        "url": "https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/202204/t20220408_2669587.html",
    },
    "GB50497-2019": {
        "name": "建筑基坑工程监测技术标准", "effective": "2020-06-01",
        "authority": "基坑支护及环境监测技术标准；工程监测控制值需项目确定",
        "url": "https://www.ndls.org.cn/standard/detail/7396db42856858bed79627d6c619ccdb",
    },
    "GB55003-2021": {
        "name": "建筑与市政地基基础通用规范", "effective": "2022-01-01",
        "authority": "强制性工程建设规范", "url": "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2024/art_17339_761185.html",
    },
    "JGJ120-2012": {
        "name": "建筑基坑支护技术规程", "effective": "2012-10-01",
        "authority": "行业标准，应用时受现行强制性通用规范约束", "url": "https://ebook.chinabuilding.com.cn/zbooklib/book/detail/show?SiteID=1&bookID=61014&isEnterprise=Y",
    },
    "DB33/T1096-2014": {
        "name": "浙江省建筑基坑工程技术规程", "effective": "2014-04-01",
        "authority": "适用浙江项目的地方工程建设标准，应与上位强制性规范核对",
        "url": "https://www.slgcjc.cn/STAND/DetailInfo?pstid=a4b2ee114fef6767957b2a68d76015fc",
    },
    "GB/T50010-2010(2024)": {
        "name": "混凝土结构设计标准（2024年局部修订）", "effective": "2024-08-01",
        "authority": "混凝土结构设计标准及局部修订", "url": "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2024/art_17339_778180.html",
    },
}

# The applicability gate is deliberately narrower than the standard itself.
METHODS = [
    dict(id="JGJ120_3_4_2_RANKINE", label="主动/被动土压力", standard="JGJ120-2012", clause="3.4.2",
         status="equation_verified", scope="水平地面、竖直墙、有效应力分算或总应力合算；输入竖向应力已求得",
         required=["sigma_total", "pore_pressure", "cohesion", "phi", "stress_mode", "strength_basis"], output=["pa", "pp", "ka", "kp"]),
    dict(id="JGJ120_4_1_7_PILE_WIDTH", label="排桩土反力计算宽度", standard="JGJ120-2012", clause="4.1.7",
         status="equation_verified", scope="单排圆形或矩形支护桩；b0不得大于排桩中心距",
         required=["pile_width", "pile_spacing", "pile_section"], output=["soil_reaction_width"]),
    dict(id="JGJ120_4_1_6_M_ESTIMATE", label="缺少实测m时的规范经验公式", standard="JGJ120-2012", clause="4.1.6",
         status="equation_verified", scope="缺水平荷载试验及地区经验时的备选估算；需提供c、φ与坑底位移vb，vb<=10mm时可按10mm取值；所得m须审核",
         required=["cohesion", "phi", "vb"], output=["m_candidate", "formula_terms", "approval_required"]),
    dict(id="JGJ120_4_1_5_SOIL_KH", label="m法水平反力系数", standard="JGJ120-2012", clause="4.1.5",
         status="equation_verified", scope="坑内嵌固段；m须由勘察、试验、地区经验或经审核的4.1.6方法给定",
         required=["m", "depth", "excavation_depth"], output=["kh"]),
    dict(id="JGJ120_4_1_4_SOIL_REACTION", label="坑内侧土反力及被动上限", standard="JGJ120-2012", clause="4.1.4",
         status="equation_verified", scope="仅坑内嵌固段的局部反力点；σ_total与孔压必须采用当前开挖面以下坑内侧应力；合力上限仍需整段验算",
         required=["sigma_total", "pore_pressure", "cohesion", "phi", "stress_mode", "strength_basis", "m", "depth", "excavation_depth", "displacement", "stress_side"],
         output=["ps_initial", "ps_unbounded", "ps_limited", "passive_limit_reached"]),
    dict(id="GB55003_7_2_1_ULS", label="承载力极限状态表达式", standard="GB55003-2021", clause="7.2.1",
         status="equation_verified", scope="设计效应及设计抗力已由适用分项组合、构件规范独立计算",
         required=["gamma0", "effect_type", "design_effect", "design_resistance"], output=["utilization", "satisfied"]),
    dict(id="GB55003_7_2_2_SLS", label="正常使用极限状态表达式", standard="GB55003-2021", clause="7.2.2",
         status="equation_verified", scope="位移/沉降标准组合效应和工程控制限值已核准",
         required=["service_effect", "service_limit"], output=["utilization", "satisfied"]),
    dict(id="JGJ120_4_1_2_STAGES", label="开挖、支撑与换撑工况完整性", standard="JGJ120-2012", clause="4.1.2",
         status="rule_verified", scope="施工事件与全部控制工况已建立；仅检查工况覆盖不替代内力求解",
         required=["stages"], output=["missing_events", "stage_coverage_complete"]),
    dict(id="DB33_1096_LOCAL_REQUIREMENTS", label="浙江地方基坑规范核对", standard="DB33/T1096-2014", clause="按项目体系与条文选择",
         status="integration_pending", scope="浙江项目强制执行适用性及地方增补要求审核；当前未建设逐条地方验算", required=[], output=[]),
    dict(id="JGJ120_3_4_COULOMB_WEDGE", label="库仑有限滑动楔体土压力", standard="JGJ120-2012", clause="3.4.2", status="integration_pending",
         scope="稳定界面、有限滑楔及坡台需按适用条件独立建立公式与验证", required=[], output=[]),
    dict(id="JGJ120_4_1_3_ELASTIC_SUPPORT", label="平面杆系弹性支点法", standard="JGJ120-2012", clause="4.1.3-4.1.10",
         status="integration_pending", scope="需统一嵌固段初始土反力、作用宽度、合力上限、支撑刚度及阶段历史", required=[], output=[]),
    dict(id="JGJ120_4_2_STABILITY", label="支护结构稳定验算", standard="JGJ120-2012", clause="4.2",
         status="integration_pending", scope="按支护型式选择抗倾覆、整体稳定、坑底隆起、渗透稳定及其他验算", required=[], output=[]),
    dict(id="JGJ120_4_9_SUPPORT_SYSTEM",label="水平钢筋混凝土/钢支撑内力与稳定",standard="JGJ120-2012",clause="4.9",status="integration_pending",
         scope="包括支撑节点、围檩、立柱和支承体系的承载力与稳定", required=[],output=[]),
    dict(id="JGJ120_5_SOIL_NAIL",label="土钉墙整体稳定和土钉承载力", standard="JGJ120-2012",clause="5章",status="integration_pending",
         scope="土钉墙适用边界、极限平衡、土钉抗拔及构造",required=[],output=[]),
    dict(id="JGJ120_6_GRAVITY_WALL",label="水泥土重力墙稳定和承载力",standard="JGJ120-2012",clause="6章",status="integration_pending",
         scope="重力墙抗滑、抗倾覆、地基承载和构件强度",required=[],output=[]),
    dict(id="JGJ120_4_12_DOUBLE_ROW", label="双排桩框架分析", standard="JGJ120-2012", clause="4.12",
         status="integration_pending", scope="前后排桩与连梁真实刚架和桩间土耦合", required=[], output=[]),
    dict(id="JGJ120_B_RC_ROUND", label="圆桩正截面受弯", standard="JGJ120-2012", clause="附录B",
         status="integration_pending", scope="与GB/T50010-2010(2024)适用条文及轴力组合共同核验", required=[], output=[]),
    dict(id="GB50010_2024_REBAR", label="配筋、抗剪、裂缝、构造", standard="GB/T50010-2010(2024)", clause="6、7、8、9章及局部修订",
         status="integration_pending", scope="分截面、受力、使用组合与构造条件逐项验算", required=[], output=[]),
    dict(id="GB55001_ACTION_COMBINATIONS",label="结构作用组合和设计状况",standard="GB55001-2021",clause="3.1",status="integration_pending",
         scope="持久、短暂、偶然、地震设计状况的适用作用组合及符号/系数核对",required=[],output=[]),
    dict(id="GB55008_CONCRETE_GENERAL",label="混凝土构件强制性要求",standard="GB55008-2021",clause="材料、承载力、构造",status="integration_pending",
         scope="与GB/T50010结合的混凝土构件强制性检查",required=[],output=[]),
    dict(id="GB50497_MONITORING",label="基坑监测项目与控制值校核",standard="GB50497-2019",clause="4-8章",status="integration_pending",
         scope="项目安全等级、环境、监测指标/控制值和报警阈值的匹配",required=[],output=[]),
    dict(id="JGJ120_7_GROUNDWATER", label="地下水控制和渗透稳定", standard="JGJ120-2012", clause="7章、附录C/E",
         status="integration_pending", scope="截水、降水、涌水量与渗透稳定专项", required=[], output=[]),
]

def catalog() -> dict:
    return {"schemaVersion": "pitguard.norm-catalog/1", "standards": deepcopy(SOURCES),
            "methods": deepcopy(METHODS), "releaseRule": "只有所有必需模块和项目条件通过，才可能进入工程校审。"}


def method(identifier: str) -> dict:
    for item in METHODS:
        if item['id'] == identifier:
            return deepcopy(item)
    raise ValueError(f"Unknown normative method: {identifier}")
