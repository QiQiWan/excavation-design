"""总控 Agent：确定性六步调度、依赖检查、人工闸口与异常恢复。

设计边界（对应重构方案第一、七、九节）：

- 只做调度，不做工程判断。每一步能不能做，都读自项目投影与任务表。
- 不调用大模型。模型接进来时替换的是「谁来选下一步」，判定仍走这里。
- 不越过闸口。设计基准确认、候选采用、正式施工图签发一律留给人工。
- 不伪造状态。缺少冻结结果、缺少输入、上游未完成时明确说缺什么。

每次派单判定都写入 ``orchestrations`` 表，用于回答「系统当时为什么建议
这一步、为什么没有派单」。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from .catalog import catalog
from .stages import normalize as _normalize_stage
from .store import AgentStore, now

SCHEMA = 'pitguard.orchestration/1'

# 六步与专业角色的对应，按重构方案第二节。stageId 采用 DESIGN.* 命名空间，
# 与既有 design_pipeline 的 P1-P8、assurance 的 P1-P4 明确区分。
STAGE_SPECS: dict[str, dict[str, Any]] = {
    'basis': {
        'stageId': 'DESIGN.BASIS',
        'owner': 'A1',
        'purpose': '确认工程等级、场地标准、荷载组合与设计基准',
        'tools': ['get_project_context'],
        'humanAction': {
            'type': 'confirm_design_basis',
            'endpoint': 'PATCH /api/projects/{projectId}/workspace',
            'note': '在设计基准面板核对后保存确认状态；总控不代为确认。',
        },
    },
    'input': {
        'stageId': 'DESIGN.INPUT',
        'owner': 'A2',
        'purpose': '补齐钻孔、地层、地下水位与闭合基坑轮廓',
        'tools': ['get_project_context'],
        'humanAction': {
            'type': 'complete_engineering_input',
            'endpoint': 'PATCH /api/projects/{projectId}/workspace',
            'note': '钻孔、地层与轮廓属于工程原始资料，必须由工程师提供或确认。',
        },
    },
    'scheme': {
        'stageId': 'DESIGN.SCHEME',
        'owner': 'A3',
        'purpose': '生成围护墙、水平支撑与候选布置',
        'tools': ['get_project_context', 'generate_scheme_candidates'],
        'humanAction': {
            'type': 'generate_scheme',
            'endpoint': 'POST /api/agents/chain/{flowId}/advance',
            'note': '生成方案会写工作版并使现有计算结果失效，必须由工程师确认后执行。',
            'impact': 'writes_working_copy_and_invalidates_calculation',
        },
    },
    'calculation': {
        'stageId': 'DESIGN.CALCULATION',
        'owner': 'A5',
        'purpose': '对冻结结果执行诊断、候选复算与规范核验',
        'tools': ['get_calculation_findings', 'run_stage_analysis', 'evaluate_design_changes'],
        'humanAction': None,
    },
    'reinforcement': {
        'stageId': 'DESIGN.REINFORCEMENT',
        'owner': 'A7',
        'purpose': '按计算内力完成截面选择、配筋与构造检查',
        'tools': ['get_calculation_findings'],
        'humanAction': {
            'type': 'generate_detailing',
            'endpoint': 'POST /api/agents/chain/{flowId}/advance',
            'note': '深化会写工作版并可能触发配筋重新闭合，必须由工程师确认后执行。',
            'impact': 'writes_working_copy',
        },
    },
    'deliverables': {
        'stageId': 'DESIGN.DELIVERABLES',
        'owner': 'A8',
        'purpose': '生成审查版成果包并做图算表交叉校核',
        'tools': ['prepare_delivery_package'],
        'humanAction': {
            'type': 'prepare_delivery',
            'endpoint': 'POST /api/agents/chain/{flowId}/advance',
            'note': '只导出评审版成果包；正式施工图签发始终关闭。',
            'impact': 'artifact_only',
        },
    },
}

# 阶段号必须能在注册表里查到，写错在导入时就失败。
for _spec in STAGE_SPECS.values():
    _normalize_stage(_spec['stageId'])

STAGE_ORDER = ('basis', 'input', 'scheme', 'calculation', 'reinforcement', 'deliverables')

GATE_NOTES = {
    'done': '该步已完成',
    'human_confirm': '需要工程师确认后才能继续',
    'needs_data': '缺少必要的工程输入资料',
    'role_not_ready': '承担该步的专业角色尚未建立执行链',
    'upstream_incomplete': '上一步尚未完成',
    'needs_frozen_result': '没有可启动的冻结单剖面结果',
    'already_in_progress': '已有任务在执行或等待复核',
    'auto_dispatch': '总控可以直接派单给运行时',
}

_PLAN_TTL_SECONDS = 20.0
_plan_cache: dict[str, tuple[float, dict]] = {}


def _roles() -> dict[str, dict]:
    return {role['id']: role for role in catalog()['roles']}


def _workspace_status(project_id: str) -> tuple[dict | None, dict]:
    """Read the same six-stage projection the workspace header shows.

    Reusing ``build_core_workspace_status`` keeps one definition of "done" for
    the UI and the orchestrator; a second, parallel stage rule set is exactly
    the inconsistency the refactor plan warns about.
    """
    counts = {'boreholeCount': 0, 'stratumCount': 0, 'outlinePointCount': 0}
    try:
        from app.services.core_workspace import build_core_workspace_status
        from app.storage.repository import ProjectRepository, shared_project_store

        repo = ProjectRepository(store=shared_project_store(os.getenv('PITGUARD_DB_PATH') or None))
        project = repo.require_workspace(project_id)
        outline = getattr(getattr(project, 'excavation', None), 'outline', None)
        counts = {
            'boreholeCount': len(project.boreholes or []),
            'stratumCount': len(project.strata or []),
            'outlinePointCount': len(getattr(outline, 'points', None) or []),
        }
        return build_core_workspace_status(
            project, repo.store.get_payload_info(project_id), detail='summary'), counts
    except Exception:
        return None, counts


def _frozen_results(project_id: str) -> list[dict]:
    """Frozen single-section results that belong to this project, newest first."""
    from app.engineering.single_braced_pile.repository import Repository

    store = AgentStore()
    folder = Repository().root / 'results'
    if not folder.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if data.get('projectId') != project_id:
            continue
        section = data.get('sectionId')
        try:
            selection = store.selection(project_id, section)
            launchable = selection['snapshotHash'] == data.get('snapshotHash')
        except KeyError:
            # No working selection yet: AgentRuntime.create will register this one.
            launchable = True
        rows.append({
            'resultHash': path.stem,
            'sectionId': section,
            'analysisStatus': data.get('analysisStatus'),
            'createdAt': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            'launchable': launchable,
        })
    return rows


def _age_seconds(updated_at: str | None) -> float:
    if not updated_at:
        return 0.0
    try:
        stamp = datetime.fromisoformat(str(updated_at).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except ValueError:
        return 0.0


def _recovery(runs: list[dict]) -> list[dict]:
    """Turn failed / paused / stale runs into explicit recovery options."""
    rows: list[dict] = []
    for run in runs:
        status = run.get('status')
        age = _age_seconds(run.get('updatedAt'))
        entry: dict[str, Any] = {
            'runId': run['id'], 'status': status, 'step': run.get('step'),
            'progress': run.get('progress'), 'ageSeconds': round(age, 1),
        }
        if status == 'awaiting_review':
            entry.update({
                'action': 'human_decision', 'requiresHuman': True,
                'reason': '候选已复算完成，等待人工采用或拒绝。',
                'endpoint': f"/api/agents/runs/{run['id']}/decision",
            })
        elif status == 'paused':
            entry.update({
                'action': 'resume', 'requiresHuman': False,
                'reason': '任务已暂停，确认现场条件后可恢复。',
                'endpoint': f"/api/agents/runs/{run['id']}/resume",
            })
        elif status == 'failed':
            error = str(run.get('error') or '')
            database_conflict = ('readonly database' in error.lower()
                                 or 'database is locked' in error.lower())
            entry.update({
                'action': 'retry_after_environment_fix' if database_conflict else 'inspect_then_retry',
                'requiresHuman': False,
                'reason': ('数据库并发写冲突（既有 Worker 竞态），先确认只有一个写库进程再重试。'
                           if database_conflict else '任务执行失败，先看事件与错误再决定重试。'),
                'error': error[:200],
                'endpoint': f"/api/agents/runs/{run['id']}/overview",
            })
        elif status in ('queued', 'running') and age > 300:
            entry.update({
                'action': 'advance_or_restart_worker', 'requiresHuman': False,
                'reason': '任务超过 5 分钟没有推进，可能没有可用 worker。',
                'endpoint': f"/api/agents/runs/{run['id']}/advance",
            })
        else:
            continue
        rows.append(entry)
    return rows


def _active_run(runs: list[dict]) -> dict | None:
    for run in runs:
        if run.get('status') in ('queued', 'running', 'awaiting_review'):
            return run
        if run.get('status') == 'paused':
            return run
    return None


def _missing_input(summary: dict) -> list[str]:
    missing: list[str] = []
    if int(summary.get('boreholeCount') or 0) == 0:
        missing.append('钻孔')
    if int(summary.get('stratumCount') or 0) == 0:
        missing.append('地层')
    if int(summary.get('outlinePointCount') or 0) < 3:
        missing.append('闭合基坑轮廓（至少 3 个点）')
    return missing


def _stage_block(
    key: str,
    index: int,
    status_by_key: dict[str, dict],
    *,
    summary: dict,
    frozen: list[dict],
    active_run: dict | None,
    roles: dict[str, dict],
    project_id: str,
) -> dict:
    spec = STAGE_SPECS[key]
    stage = status_by_key.get(key) or {}
    status = stage.get('status') or 'pending'
    owner = roles.get(spec['owner']) or {}
    executable = owner.get('implementation') == 'pilot'

    block: dict[str, Any] = {
        'stageId': spec['stageId'],
        'key': key,
        'title': stage.get('title') or key,
        'status': status,
        'message': stage.get('message'),
        'owner': spec['owner'],
        'ownerName': owner.get('name'),
        'ownerExecutable': executable,
        'purpose': spec['purpose'],
        'tools': spec['tools'],
        'dependsOn': STAGE_ORDER[index - 1] if index else None,
        'missing': [],
    }

    if status == 'done':
        block['state'] = 'done'
        block['gate'] = {'type': 'done', 'requiresHuman': False,
                         'reason': GATE_NOTES['done']}
        return block

    previous = STAGE_ORDER[index - 1] if index else None
    if previous and (status_by_key.get(previous) or {}).get('status') != 'done':
        block['state'] = 'blocked'
        block['gate'] = {
            'type': 'upstream_incomplete', 'requiresHuman': False,
            'reason': f"上一步「{status_by_key.get(previous, {}).get('title', previous)}」尚未完成。",
            'blockedBy': previous,
        }
        return block

    if key == 'basis':
        block['state'] = 'ready'
        block['gate'] = {
            'type': 'human_confirm', 'requiresHuman': True,
            'reason': '设计基准决定后续全部计算口径，必须由工程师确认。',
            'humanAction': dict(spec['humanAction'] or {}, projectId=project_id),
        }
    elif key == 'input':
        missing = _missing_input(summary)
        block['missing'] = missing
        block['state'] = 'blocked' if missing else 'ready'
        block['gate'] = {
            'type': 'needs_data', 'requiresHuman': True,
            'reason': ('缺少：' + '、'.join(missing)) if missing else '工程输入需要工程师核认。',
            'humanAction': dict(spec['humanAction'] or {}, projectId=project_id),
        }
    elif key == 'scheme':
        if not executable:
            block['state'] = 'blocked'
            block['gate'] = {
                'type': 'role_not_ready', 'requiresHuman': True,
                'reason': 'A3 支护方案执行链未启用。',
                'humanAction': dict(spec['humanAction'] or {}, projectId=project_id),
            }
        else:
            block['state'] = 'ready'
            block['gate'] = {
                'type': 'human_confirm', 'requiresHuman': True,
                'reason': '生成支护方案会写工作版并使现有计算结果失效，需要工程师确认。',
                'action': dict(spec['humanAction'] or {}, projectId=project_id),
            }
    elif key == 'calculation':
        launchable = [row for row in frozen if row['launchable']]
        block['frozenResults'] = {
            'total': len(frozen), 'launchable': len(launchable),
        }
        if active_run:
            block['state'] = 'blocked'
            block['gate'] = {
                'type': 'already_in_progress', 'requiresHuman': True,
                'reason': f"任务 {active_run['id'][:12]} 处于 {active_run['status']} / {active_run['step']}。",
                'runId': active_run['id'],
                'endpoint': f"/api/agents/runs/{active_run['id']}/overview",
                'action': {
                    'type': 'review_run',
                    'endpoint': f"/api/agents/runs/{active_run['id']}/overview",
                    'note': '该任务在等待人工复核；确认或驳回候选后再继续。',
                    'impact': 'none',
                },
            }
        elif not launchable:
            block['state'] = 'blocked'
            block['missing'] = ['可启动的冻结单剖面结果']
            block['gate'] = {
                'type': 'needs_frozen_result', 'requiresHuman': True,
                'reason': ('该项目没有冻结结果。' if not frozen
                           else f"该项目有 {len(frozen)} 个冻结结果，但与当前工作选择不一致。"),
                'humanAction': {
                    'type': 'choose_frozen_result',
                    'endpoint': 'GET /api/agents/frozen-results',
                    'note': '先在单排桩工作台冻结当前剖面并登记工作选择，再回来派单。',
                },
            }
        else:
            block['state'] = 'ready'
            block['gate'] = {
                'type': 'auto_dispatch', 'requiresHuman': False,
                'reason': f"可启动冻结结果 {len(launchable)} 个，派单只作用于独立候选。",
                'defaultResultHash': launchable[0]['resultHash'],
            }
    elif key == 'reinforcement':
        if not executable:
            block['state'] = 'blocked'
            block['gate'] = {
                'type': 'role_not_ready', 'requiresHuman': True,
                'reason': 'A7 构件深化执行链未启用。',
                'humanAction': dict(spec['humanAction'] or {}, projectId=project_id),
            }
        else:
            block['state'] = 'ready'
            block['gate'] = {
                'type': 'human_confirm', 'requiresHuman': True,
                'reason': '深化会写工作版并可能触发配筋重新闭合，需要工程师确认。',
                'action': dict(spec['humanAction'] or {}, projectId=project_id),
            }
    else:  # deliverables
        if not executable:
            block['state'] = 'blocked'
            block['gate'] = {
                'type': 'role_not_ready', 'requiresHuman': True,
                'reason': 'A8 成果与校审执行链未启用。',
                'humanAction': dict(spec['humanAction'] or {}, projectId=project_id),
                'formalConstructionIssue': False,
            }
        else:
            block['state'] = 'ready'
            block['gate'] = {
                'type': 'human_confirm', 'requiresHuman': True,
                'reason': '只导出评审版成果包；正式施工图签发始终关闭。',
                'action': dict(spec['humanAction'] or {}, projectId=project_id),
                'formalConstructionIssue': False,
            }
    return block


def build_plan(project_id: str, *, refresh: bool = False) -> dict:
    """Deterministic six-stage plan for one project."""
    cached = _plan_cache.get(project_id)
    monotonic = time.monotonic()
    if cached and not refresh and monotonic - cached[0] < _PLAN_TTL_SECONDS:
        payload = dict(cached[1])
        payload['cachedSecondsAgo'] = round(monotonic - cached[0], 1)
        return payload

    status, counts = _workspace_status(project_id)
    if status is None:
        raise KeyError(project_id)

    summary = dict(status.get('summary') or {})
    summary.update(counts)
    stages = status.get('stages') or []

    status_by_key = {item.get('key'): item for item in stages}
    frozen = _frozen_results(project_id)
    runs = AgentStore().list(project_id=project_id, limit=10)
    active = _active_run(runs)
    roles = _roles()

    blocks = [
        _stage_block(key, index, status_by_key, summary=summary, frozen=frozen,
                     active_run=active, roles=roles, project_id=project_id)
        for index, key in enumerate(STAGE_ORDER)
    ]
    next_block = next((item for item in blocks if item['state'] != 'done'), blocks[-1])

    payload = {
        'schemaVersion': SCHEMA,
        'projectId': project_id,
        'mode': 'deterministic',
        'generatedAt': now(),
        'roles': {
            'executable': [role['id'] for role in roles.values()
                           if role['implementation'] == 'pilot'],
            'planned': [role['id'] for role in roles.values()
                        if role['implementation'] != 'pilot'],
        },
        'stages': blocks,
        'next': {
            'stageId': next_block['stageId'],
            'key': next_block['key'],
            'title': next_block['title'],
            'owner': next_block['owner'],
            'ownerName': next_block['ownerName'],
            'ownerExecutable': next_block['ownerExecutable'],
            'state': next_block['state'],
            'gate': next_block['gate'],
            'dispatchable': next_block['gate'].get('type') == 'auto_dispatch',
            'missing': next_block.get('missing') or [],
        },
        'blockers': list(status.get('blockers') or [])[:8],
        'summary': summary,
        'recovery': _recovery(runs),
        'formalConstructionIssue': False,
    }
    _plan_cache[project_id] = (monotonic, payload)
    return payload


def dispatch(
    project_id: str,
    *,
    actor: str,
    conversation_id: str | None = None,
    result_hash: str | None = None,
    max_candidates: int = 3,
    client_request_id: str | None = None,
) -> dict:
    """Dispatch the next stage when — and only when — every gate is open."""
    store = AgentStore()
    plan = build_plan(project_id, refresh=True)
    nxt = plan['next']
    gate = nxt['gate']

    def _refuse(reason: str, *, gate_type: str | None = None) -> dict:
        decision = {
            'dispatched': False, 'stageKey': nxt['key'], 'stageId': nxt['stageId'],
            'gateType': gate_type or gate.get('type'), 'reason': reason,
        }
        store.record_orchestration(
            project_id=project_id, actor=actor, conversation_id=conversation_id,
            stage_key=nxt['key'], gate=gate_type or gate.get('type'),
            dispatched=False, run_id=None, decision=decision,
        )
        return {**decision, 'plan': plan, 'formalConstructionIssue': False}

    if conversation_id:
        conversation = store.get_conversation(conversation_id)
        if conversation['projectId'] != project_id:
            raise ValueError('会话与项目不匹配')

    if gate.get('type') != 'auto_dispatch':
        return _refuse(f"当前步骤「{nxt['title']}」不能自动派单：{gate.get('reason')}")

    candidates = [row for row in _frozen_results(project_id) if row['launchable']]
    chosen = result_hash or gate.get('defaultResultHash')
    if not chosen:
        return _refuse('没有可启动的冻结结果，无法派单。', gate_type='needs_frozen_result')
    if not any(row['resultHash'] == chosen for row in candidates):
        return _refuse('指定的冻结结果不可启动：与当前工作选择不一致或不属于本项目。',
                       gate_type='needs_frozen_result')

    from .contracts import StartRun
    from .engine import AgentRuntime

    try:
        run = AgentRuntime().create(
            StartRun(
                result_hash=chosen,
                max_candidates=max_candidates,
                client_request_id=client_request_id,
                conversation_id=conversation_id,
            ),
            actor=actor,
        )
    except Exception as exc:  # surface the real reason instead of a silent no-op
        return _refuse(f"派单被运行时拒绝：{type(exc).__name__}: {str(exc)[:160]}",
                       gate_type='dispatch_rejected')

    decision = {
        'dispatched': True, 'stageKey': nxt['key'], 'stageId': nxt['stageId'],
        'gateType': gate.get('type'), 'reason': gate.get('reason'),
        'runId': run['id'], 'resultHash': chosen,
    }
    store.record_orchestration(
        project_id=project_id, actor=actor, conversation_id=conversation_id,
        stage_key=nxt['key'], gate=gate.get('type'), dispatched=True,
        run_id=run['id'], decision=decision,
    )
    if conversation_id:
        try:
            store.append_message(
                conversation_id, role='tool',
                content=f"总控已派单：{nxt['title']} · {nxt['owner']}（任务 {run['id'][:12]}）。",
                metadata={
                    'answerKind': 'orchestration', 'stageKey': nxt['key'],
                    'stageId': nxt['stageId'], 'runId': run['id'],
                    'dispatched': True, 'engineeringJudgement': False,
                },
            )
        except Exception:
            pass
    _plan_cache.pop(project_id, None)
    return {
        **decision,
        'run': {'id': run['id'], 'status': run['status'], 'step': run['step'],
                'progress': run['progress']},
        'plan': build_plan(project_id, refresh=True),
        'formalConstructionIssue': False,
    }


def history(*, project_id: str | None = None, limit: int = 30) -> list[dict]:
    return AgentStore().orchestrations(project_id=project_id, limit=limit)
