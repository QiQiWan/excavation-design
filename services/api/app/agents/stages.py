"""阶段注册表：把并存的几套 P 编号说清楚，并给 Agent 侧唯一来源。

仓库里同时存在三种 ``P`` 编号，语义完全不同，不能互相替代：

- **设计流水线** ``P1_DATA_BASIS`` … ``P8_REVIEW_ISSUE``（``design_pipeline.py``）
- **单桩计算质量阶段** ``P1`` … ``P4``（``single_braced_pile/assurance``）
- **软件验收项** ``P0`` … ``P12``（``compliance/assurance.py``）

Agent 侧使用 ``DESIGN.*`` / ``DELIVERY.*`` 命名空间。这里给出：
1. Agent 阶段清单（唯一来源）；
2. 每个 Agent 阶段对应到哪些流水线阶段（多对多，不强行一对一）；
3. 裸 ``P1`` 的歧义提示，避免调用方猜。
"""
from __future__ import annotations

SCHEMA = 'pitguard.stage-registry/1'

PIPELINE_STAGES: tuple[dict, ...] = (
    {'code': 'P1', 'id': 'P1_DATA_BASIS', 'label': '设计依据与设计域'},
    {'code': 'P2', 'id': 'P2_SCHEME', 'label': '支护体系与施工分幅'},
    {'code': 'P3', 'id': 'P3_SCHEME_COMPARISON', 'label': '候选方案完整计算与选型'},
    {'code': 'P4', 'id': 'P4_ANALYSIS', 'label': '分阶段计算、数值质量与独立复核'},
    {'code': 'P5', 'id': 'P5_COMPONENT_DESIGN', 'label': '构件截面、双向配筋与墙趾优化'},
    {'code': 'P6', 'id': 'P6_DETAILING', 'label': '钢筋笼、节点与施工深化'},
    {'code': 'P7', 'id': 'P7_DELIVERABLES', 'label': '施工图、IFC、计算书与加工包'},
    {'code': 'P8', 'id': 'P8_REVIEW_ISSUE', 'label': '校审、批准与受控发行'},
)

AGENT_STAGES: tuple[dict, ...] = (
    {'id': 'DESIGN.BASIS', 'role': 'A1', 'label': '设计基准',
     'pipeline': ['P1'], 'primary': True},
    {'id': 'DESIGN.INPUT', 'role': 'A2', 'label': '地质与设计输入',
     'pipeline': ['P1'], 'primary': True},
    {'id': 'DESIGN.SCHEME', 'role': 'A3', 'label': '支护方案',
     'pipeline': ['P2', 'P3'], 'primary': True},
    {'id': 'DESIGN.CONSTRUCTION', 'role': 'A4', 'label': '施工阶段',
     'pipeline': ['P2', 'P4'], 'primary': True},
    {'id': 'DESIGN.CALCULATION', 'role': 'A5', 'label': '计算与验算',
     'pipeline': ['P4'], 'primary': True},
    {'id': 'DESIGN.REINFORCEMENT', 'role': 'A7', 'label': '构件深化',
     'pipeline': ['P5', 'P6'], 'primary': True},
    {'id': 'DESIGN.DELIVERABLES', 'role': 'A8', 'label': '成果包',
     'pipeline': ['P7'], 'primary': False},
    {'id': 'DELIVERY.REVIEW', 'role': 'A8', 'label': '校审与发行',
     'pipeline': ['P8'], 'primary': True},
)

CALCULATION_PHASES: tuple[dict, ...] = (
    {'code': 'P1', 'label': '水力模型与渗透稳定'},
    {'code': 'P2', 'label': '圆桩轴弯剪、裂缝及构造'},
    {'code': 'P3', 'label': '同条件工程对照'},
    {'code': 'P4', 'label': '同源成果审查与签发'},
)

ACCEPTANCE_NOTE = 'compliance 的 P0-P12 是软件验收项，不属于工程阶段，不参与映射。'

_BY_ID = {row['id'].upper(): row for row in AGENT_STAGES}


def primary_stage_of_role() -> dict[str, str]:
    """角色 → Ticket 使用的主阶段；A8 同时拥有成果包与校审，取校审。"""
    return {row['role']: row['id'] for row in AGENT_STAGES if row['primary']}


def normalize(code: str) -> str:
    """把 Agent 阶段号规范成标准写法；裸 P 代码直接报歧义。"""
    value = str(code or '').strip()
    if not value:
        raise ValueError('阶段号不能为空')
    row = _BY_ID.get(value.upper())
    if row:
        return str(row['id'])
    if value.upper().startswith('P') and value[1:].isdigit():
        raise ValueError(
            f'{value} 属于多个命名空间（流水线 / 计算质量 / 软件验收），'
            '请使用 DESIGN.* 或 DELIVERY.* 阶段号'
        )
    raise ValueError(f'未登记的阶段号：{value}')


def describe(code: str) -> dict:
    canonical = normalize(code)
    row = next(item for item in AGENT_STAGES if item['id'] == canonical)
    return {
        'stageId': row['id'], 'role': row['role'], 'label': row['label'],
        'pipeline': list(row['pipeline']),
        'pipelineLabels': [next(p['label'] for p in PIPELINE_STAGES if p['code'] == code_)
                           for code_ in row['pipeline']],
    }


def registry() -> dict:
    return {
        'schemaVersion': SCHEMA,
        'namespaces': {
            'agent': 'DESIGN.* / DELIVERY.*（Agent 阶段，本表唯一来源）',
            'pipeline': 'P1-P8（design_pipeline 设计流水线）',
            'calculation': 'P1-P4（单桩计算质量阶段）',
            'acceptance': ACCEPTANCE_NOTE,
        },
        'agentStages': [
            {'id': row['id'], 'role': row['role'], 'label': row['label'],
             'pipeline': list(row['pipeline']), 'primary': row['primary']}
            for row in AGENT_STAGES
        ],
        'pipelineStages': [dict(row) for row in PIPELINE_STAGES],
        'calculationPhases': [dict(row) for row in CALCULATION_PHASES],
        'primaryByRole': primary_stage_of_role(),
        'note': 'Agent 阶段与流水线阶段是多对多，不做一对一改名。',
        'formalConstructionIssue': False,
    }
