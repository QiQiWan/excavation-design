"""Agent responsibilities are logical roles; only A5/A6 are executable in V3.88.1."""
from __future__ import annotations

AGENTS = (
    ('A1','资料与参数','document_parameters','planned'),
    ('A2','地质与场景','geotechnical_scenarios','planned'),
    ('A3','支护方案','support_layout','durable_m3_physical_pilot'),
    ('A4','施工阶段','stage_planning','planned'),
    ('A5','计算与验算','calculation_diagnostics','pilot'),
    ('A6','优化闭环','bounded_candidate_search','pilot'),
    ('A7','构件深化','member_detailing','planned'),
    ('A8','成果与校审','delivery_review','planned'),
)
TOOLS = (
    {'name':'read_frozen_one_strut_result','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'diagnose_frozen_result','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'propose_bounded_changes','permission':'L1','sideEffects':False,'scope':'one_strut'},
    {'name':'calculate_isolated_candidate','permission':'L1','sideEffects':'immutable_new_result','scope':'one_strut'},
    {'name':'locate_member_stage_findings','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'reconfirm_candidate_parameter_source','permission':'L2','sideEffects':'checker_verified_byte_receipt','scope':'one_strut'},
    {'name':'read_normative_check_evidence','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'evaluate_multi_constraint_feasibility','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'rank_pareto_trials','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'compare_calculated_candidates','permission':'L0','sideEffects':False,'scope':'one_strut'},
    {'name':'adopt_working_selection','permission':'L2','sideEffects':'checker_approved_selection_only','scope':'one_strut'},
)

M3_REFERENCE_TOOLS = (
    {'name':'m3_evaluate_three_spatial_candidates','permission':'L1','sideEffects':False,'scope':'m3_spatial_reference'},
    {'name':'m3_bounded_layout_search','permission':'L1','sideEffects':False,'scope':'m3_spatial_reference'},
    {'name':'m3_verify_pressure_and_soil_source_bytes','permission':'L0','sideEffects':False,'scope':'m3_physical_reference'},
    {'name':'m3_run_durable_physical_candidate','permission':'L1','sideEffects':'immutable_candidate_in_sqlite','scope':'m3_physical_reference'},
    {'name':'m3_m7_independent_node_action_compare','permission':'L2','sideEffects':'checker_numerical_review_record_only','scope':'m3_physical_reference'},
    {'name':'m3_export_same_source_work_bundle','permission':'L0','sideEffects':False,'scope':'m3_physical_reference'},
)


def catalog()->dict:
    return {'schemaVersion':'pitguard.agents/1','softwareVersion':'3.89.7',
            'roles':[{'id':n,'name':name,'capability':cap,'implementation':stage}
                     for n,name,cap,stage in AGENTS],
            'tools':list(TOOLS),'m3ReferenceTools':list(M3_REFERENCE_TOOLS), 'formalConstructionIssue':False,
            'pilotScope':'single_row_pile_one_strut_plus_durable_m3_physical_reference',
            'limitations':['A5/A6支持逐条规范合同、分项约束与Pareto多轮试算；A1-A4/A7-A8尚未形成自治执行链',
                           '人工采用仅更改单剖面Agent工作选择，不修改主项目或签发施工图']}
