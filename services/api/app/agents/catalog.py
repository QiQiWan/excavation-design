"""Agent 角色目录：A1-A8 均已接入执行链，能力边界见 limitations。"""
from __future__ import annotations

AGENTS = (
    ('A1','资料与参数','document_parameters','pilot'),
    ('A2','地质与场景','geotechnical_scenarios','pilot'),
    ('A3','支护方案','support_layout','pilot'),
    ('A4','施工阶段','stage_planning','pilot'),
    ('A5','计算与验算','calculation_diagnostics','pilot'),
    ('A6','优化闭环','bounded_candidate_search','pilot'),
    ('A7','构件深化','member_detailing','pilot'),
    ('A8','成果与校审','delivery_review','pilot'),
)
TOOLS = (
    {'name':'read_frozen_one_strut_result','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'diagnose_frozen_result','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'propose_bounded_changes','permission':'L1','sideEffects':False,'scope':'one_strut'},
    {'name':'calculate_isolated_candidate','permission':'L1','sideEffects':'immutable_new_result','scope':'one_strut'},
    {'name':'compare_calculated_candidates','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'adopt_working_selection','permission':'L2','sideEffects':'checker_approved_selection_only','scope':'one_strut'},
)


def catalog()->dict:
    return {'schemaVersion':'pitguard.agents/1','softwareVersion':'3.88.0',
            'roles':[{'id':n,'name':name,'capability':cap,'implementation':stage}
                     for n,name,cap,stage in AGENTS],
            'tools':list(TOOLS), 'formalConstructionIssue':False,
            'pilotScope':'single_row_pile_one_strut',
            'limitations':['A2 只按现有钻孔建地质模型，不写钻孔、土层参数或水位；A3/A4/A7 的生成动作写工作版并可能触发复算',
                           'A1仅做文档参数提取并给出页码依据，不写入项目、不作工程判断',
                           'A8 只出评审版成果包，正式施工图签发不进入 Agent 路径',
                           '人工采用仅更改单剖面Agent工作选择，不修改主项目或签发施工图']}
