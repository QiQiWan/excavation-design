"""A8 成果与校审：盘点交付就绪度与审签状态，必要时生成评审版成果包。

正式施工图签发不进入本模块：导出任务的 issueMode 固定为 review，
任何「正式签发 / 施工版出图」请求一律拒绝，必须回到人工审批与签发流程。
"""
from __future__ import annotations

from typing import Any

from .design_basis_intake import _repository

SCHEMA = 'pitguard.delivery-assessment/1'

FORMAL_REQUEST_HINTS = ('正式签发', '正式出图', '正式施工图', '签发施工图',
                        '盖出图章', '施工版出图', '直接签发', '盖章出图', '签发')
_FORMAL_WORDS = FORMAL_REQUEST_HINTS


def _gap(rows: list[dict], key: str, label: str, reason: str, suggestion: str) -> None:
    rows.append({'key': key, 'label': label, 'reason': reason, 'suggestion': suggestion})


def assess(project_id: str, actor: str) -> dict:
    """A8：交付就绪度、四级审签与图纸修订的现状盘点。"""
    from app.services.delivery_release import evaluate_delivery_release_readiness
    from app.services.review_workflow import review_status

    project = _repository(actor).require(project_id)
    readiness = evaluate_delivery_release_readiness(project, issue_mode='review')
    review = review_status(project)
    checks = list(readiness.get('checks') or [])
    revisions = list(project.drawing_revisions or [])
    construction_revisions = [r for r in revisions if str(getattr(r, 'issue_status', '')) == 'construction']

    gaps: list[dict[str, Any]] = []
    if not project.calculation_results:
        # 没有计算时，就绪度各项都是同一个原因的结果，只报一条，避免堆十条重复缺项。
        _gap(gaps, 'calculation', '计算结果', '当前没有任何计算结果，无法形成成果',
             '先完成当前快照的完整计算并通过复核')

    for row in ([] if not project.calculation_results else checks):
        code = str(row.get('code') or '')
        state = str(row.get('status') or '')
        if state == 'fail':
            _gap(gaps, code, str(row.get('title') or code),
                 str(row.get('requiredAction') or '未通过'), '按该项要求补齐后重新评估')
        elif state == 'warning':
            _gap(gaps, code, str(row.get('title') or code),
                 str(row.get('requiredAction') or '待完成'), '评审版可先出，正式版前必须关闭')

    review_state = str(review.get('status') or '')
    if review_state and review_state != 'approved':
        _gap(gaps, 'reviewWorkflow', '四级审签',
             f'审签状态为 {review_state}（当前角色：{review.get("currentRole") or "—"}）',
             '完成设计、校核、审核、批准四级审签')
    if review_state == 'stale':
        _gap(gaps, 'reviewStale', '审签快照',
             '审签通过后项目又被修改，原审签已失效',
             '重新提交审签，确保审签快照与当前设计一致')
    if review_state == 'approved' and not review.get('separationOfDutiesValid', True):
        _gap(gaps, 'separationOfDuties', '岗位分离',
             '同一人被记录为多个审签角色',
             '设计、校核、审核、批准必须由不同人员签署')
    if not construction_revisions:
        _gap(gaps, 'revision', '施工版修订', '还没有与当前快照一致的施工版图纸修订',
             '完成审签后创建施工版修订；评审阶段可先出评审版成果包')

    if not project.calculation_results:
        status = 'blocked'
    elif gaps:
        status = 'incomplete'
    else:
        status = 'ready'

    fail_count = sum(1 for row in checks if row.get('status') == 'fail')
    warn_count = sum(1 for row in checks if row.get('status') == 'warning')
    pass_count = sum(1 for row in checks if row.get('status') == 'pass')

    if status == 'ready':
        summary_text = (f'成果就绪：{pass_count} 项检查全部通过，审签状态已批准，'
                        f'存在当前快照的施工版修订。')
    elif status == 'blocked':
        summary_text = '成果尚不具备条件：当前没有计算结果，共 %d 项待补。' % len(gaps)
    else:
        summary_text = (f'成果就绪度：{pass_count} 项通过、{fail_count} 项未通过、'
                        f'{warn_count} 项待办；审签状态 {review_state or "—"}，'
                        f'共 {len(gaps)} 项待关闭。')

    return {
        'schemaVersion': SCHEMA,
        'mode': 'assessment',
        'label': '成果与校审盘点',
        'projectId': project_id,
        'status': status,
        'readiness': {
            'status': readiness.get('status'),
            'allowed': readiness.get('allowed'),
            'fail': fail_count, 'warning': warn_count, 'pass': pass_count,
            'checks': [{'code': row.get('code'), 'title': row.get('title'),
                        'status': row.get('status'),
                        'action': row.get('requiredAction')}
                       for row in checks],
        },
        'review': {
            'status': review_state,
            'currentRole': review.get('currentRole'),
            'approvalValid': bool(review.get('approvalValid')),
            'separationOfDutiesValid': bool(review.get('separationOfDutiesValid')),
            'actionCount': int(review.get('actionCount') or 0),
        },
        'revisions': {
            'count': len(revisions),
            'construction': len(construction_revisions),
        },
        'gaps': gaps,
        'summary': summary_text,
        'suggestedNext': [g['suggestion'] for g in gaps[:3]],
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }


def prepare(project_id: str, actor: str, text: str | None = None) -> dict:
    """A8：提交评审版成果包导出任务；正式签发一律拒绝。"""
    project = _repository(actor).require(project_id)
    base = {'schemaVersion': SCHEMA, 'mode': 'assessment', 'projectId': project_id,
            'gaps': [], 'engineeringJudgement': False,
            'formalConstructionIssue': False}

    content = str(text or '')
    if any(word in content for word in _FORMAL_WORDS):
        return {**base, 'label': '正式签发未执行', 'status': 'blocked',
                'summary': ('正式施工图签发不向 Agent 开放：需要具备注册结构工程师'
                            '资格的批准人完成四级审签，并在签发端点人工确认。'),
                'gaps': [{'key': 'formalIssue', 'label': '正式签发',
                          'reason': 'Agent 无权签发正式施工图',
                          'suggestion': '切换到人工审签与签发流程'}],
                'suggestedNext': ['完成四级审签后由批准人人工签发']}
    if not project.calculation_results:
        return {**base, 'label': '成果包未提交', 'status': 'blocked',
                'summary': '当前没有计算结果，不能生成成果包。',
                'suggestedNext': ['先完成当前施工阶段计算']}

    from app.tasks.manager import task_manager

    task = task_manager.submit(project_id, 'export_coordinated_delivery', {
        'issueMode': 'review', 'rebarMode': 'balanced', 'includeIfcProfiles': True,
    })
    return {**base, 'label': '评审版成果包已提交', 'status': 'submitted',
            'taskId': task.id,
            'summary': (f'已提交评审版成果包任务 {task.id}（issueMode=review）。'
                        '正式施工图仍需人工审签后单独签发，Agent 不会代签。'),
            'suggestedNext': ['在任务中心下载成果包；正式签发走人工审批']}
