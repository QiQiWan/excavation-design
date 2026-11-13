"""后台任务完成回写：成功/失败各一次、幂等、不阻断读消息。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import super_agent
from app.agents.store import AgentStore
from app.main import app
from app.tasks.manager import task_manager


def headers():
    return {'X-PitGuard-Key': 'key-designer'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


def _project(client) -> str:
    return client.post('/api/projects', headers=headers(), json={'name': '任务回写'}).json()['id']


def _conversation(client, project_id: str) -> str:
    return client.post('/api/agents/conversations', headers=headers(),
                       json={'project_id': project_id, 'title': '任务回写'}).json()['id']


def _waiting_ticket(project_id: str, conversation_id: str, task_id: str) -> str:
    store = AgentStore()
    ticket = store.create_ticket(
        project_id=project_id, conversation_id=conversation_id, thread_id=None,
        role_id='A7', stage_id='DESIGN.REINFORCEMENT',
        mandate={'formalConstructionIssue': False},
        status='waiting_review', accept_mode='auto',
    )
    store.update_ticket(ticket['id'], status='waiting_review', worker_run_id=task_id)
    return ticket['id']


def _messages(client, conversation_id: str) -> list[str]:
    payload = client.get(f'/api/agents/conversations/{conversation_id}/messages',
                         headers=headers()).json()
    assert 'taskSyncError' not in payload
    return [str(row['content']) for row in payload['messages']]


def test_finished_task_writes_back_exactly_once(api, monkeypatch):
    pid = _project(api)
    conv = _conversation(api, pid)
    ticket_id = _waiting_ticket(pid, conv, 'task-demo1')
    monkeypatch.setattr(task_manager, 'get', lambda task_id: SimpleNamespace(
        id=task_id, operation='p3_detailing_closure', title='节点与钢筋深化',
        status='success', error=None, result={'status': 'pass'},
    ))

    contents = _messages(api, conv)
    assert any('节点与钢筋深化已完成' in text for text in contents)
    assert any('人工复核' in text for text in contents)
    assert AgentStore().get_ticket(ticket_id)['status'] == 'done'

    # 再读一次不会重复回写。
    contents_again = _messages(api, conv)
    assert sum('节点与钢筋深化已完成' in text for text in contents_again) == 1


def test_failed_task_reports_reason_and_closes_ticket(api, monkeypatch):
    pid = _project(api)
    conv = _conversation(api, pid)
    ticket_id = _waiting_ticket(pid, conv, 'task-demo2')
    monkeypatch.setattr(task_manager, 'get', lambda task_id: SimpleNamespace(
        id=task_id, operation='rebar_design', title='配筋方案',
        status='failed', error='当前计算仍有 10 个硬失败。', result=None,
    ))

    contents = _messages(api, conv)
    assert any('配筋方案失败：当前计算仍有 10 个硬失败。' in text for text in contents)
    assert AgentStore().get_ticket(ticket_id)['status'] == 'failed'


def test_running_task_is_left_waiting(api, monkeypatch):
    pid = _project(api)
    conv = _conversation(api, pid)
    ticket_id = _waiting_ticket(pid, conv, 'task-demo3')
    monkeypatch.setattr(task_manager, 'get', lambda task_id: SimpleNamespace(
        id=task_id, operation='support_layout_optimization', title='支护方案生成',
        status='running', error=None, result=None,
    ))

    assert _messages(api, conv) == []
    assert AgentStore().get_ticket(ticket_id)['status'] == 'waiting_review'
    assert super_agent._TASK_LABELS['support_layout_optimization'] == '支护方案生成'
