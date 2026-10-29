"""A controlled domain-agent planner for selectable normative calculation tools.

No language-model verdict is trusted. Tool selection is deterministic by goal;
the caller can later connect an LLM to propose goals, but it cannot produce
numbers or change parameters. Every calculation is performed by `engine`.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .contracts import CalcRequest
from .engine import calculate
from .registry import METHODS

GOAL_MODULES = {
    'earth_pressure_check': ('JGJ120_3_4_2_RANKINE', 'JGJ120_4_1_5_SOIL_KH', 'JGJ120_4_1_4_SOIL_REACTION'),
    'pile_width_check': ('JGJ120_4_1_7_PILE_WIDTH',),
    'structural_limit_check': ('GB55003_7_2_1_ULS','GB55003_7_2_2_SLS'),
    'stage_completeness_check': ('JGJ120_4_1_2_STAGES',),
    'full_pit_design': tuple(m['id'] for m in METHODS),
}

class AgentTask(BaseModel):
    model_config=ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal['pitguard.norm-agent-request/1']
    goal: Literal['earth_pressure_check','pile_width_check','structural_limit_check',
                  'stage_completeness_check','full_pit_design']
    context: CalcRequest
    execute_available: bool = False
    instruction: str = Field(default='',max_length=1000)


def run_task(task:AgentTask) -> dict:
    methods=GOAL_MODULES[task.goal]
    scoped=task.context.model_copy(update={'method_ids':methods})
    result=calculate(scoped) if task.execute_available else None
    statuses={r['methodId']:r for r in (result or {}).get('methodResults',[])}
    plan=[]
    for method_id in methods:
        spec=next(m for m in METHODS if m['id']==method_id)
        item=statuses.get(method_id)
        plan.append({'tool':method_id,'standard':spec['standard'],'clause':spec['clause'],
                     'status': item['status'] if item else ('available' if spec['status']!='integration_pending' else 'not_implemented'),
                     'requiredInputs':spec['required'],'blocking':item.get('missing') if item else []})
    return {'schemaVersion':'pitguard.norm-agent-response/1','goal':task.goal,
            'projectId':task.context.project_id,'readOnly':True,
            'toolPlan':plan,'calculation':result,
            'humanApprovalRequired':True, 'formalIssueReady':False,
            'explanation':'仅调用列明的标准工具；未实现、无证据和未确认参数均保留阻断。智能体不能自行改变安全系数或构造取值。'}
