"""V3.88.1 assistant session model: conversations, messages and run linkage.

All input cases are synthetic; a conversation never grants engineering authority.
"""
from __future__ import annotations
import json, os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.agents.contracts import StartRun, ConversationCreate, MessageCreate
from app.agents.store import AgentStore, ConflictError
from app.engineering.single_braced_pile.repository import Repository
from app.main import app


@pytest.fixture(scope='module')
def baseline(tmp_path_factory):
    root = tmp_path_factory.mktemp('v3881_baseline')
    repo = Repository(root=root / 'source')
    source = Path(__file__).resolve().parents[2] / 'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    result = repo.compute(json.loads(source.read_text(encoding='utf-8')))
    assert result['analysisStatus'] == 'complete'
    return repo, result


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / 'agent.sqlite3')


@pytest.fixture
def api(monkeypatch, tmp_path, baseline):
    _, source = baseline
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'api-agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE', str(tmp_path / 'api-repo'))
    monkeypatch.setenv('PITGUARD_AGENT_WORKER', 'external')
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
        'key-checker': {'role': 'checker', 'actor': 'bob'},
        'key-viewer': {'role': 'viewer', 'actor': 'view'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    sample = Path(__file__).resolve().parents[2] / 'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    result = Repository(root=tmp_path / 'api-repo').compute(json.loads(sample.read_text(encoding='utf8')))
    assert result['resultHash'] == source['resultHash']
    with TestClient(app) as client:
        yield client, result


def headers(key='key-designer'):
    return {'X-PitGuard-Key': key}


def test_conversation_create_and_message_order(store):
    conversation = store.create_conversation(project_id='P-1', actor='alice', title='归因')
    assert len(conversation['id']) == 32
    assert conversation['messageCount'] == 0
    for text in ('第一条', '第二条', '第三条'):
        store.append_message(conversation['id'], role='user', content=text)
    messages = store.messages(conversation['id'])
    assert [m['content'] for m in messages] == ['第一条', '第二条', '第三条']
    assert store.get_conversation(conversation['id'])['messageCount'] == 3


def test_message_metadata_keeps_engineering_reference(store):
    conversation = store.create_conversation(project_id='P-1', actor='alice')
    message = store.append_message(conversation['id'], role='user', content='看这里',
                                    metadata={'referencedRunId': 'calc-1'})
    assert message['metadata']['referencedRunId'] == 'calc-1'
    assert store.messages(conversation['id'])[0]['metadata']['referencedRunId'] == 'calc-1'


def test_message_to_unknown_conversation_is_rejected(store):
    with pytest.raises(KeyError):
        store.append_message('0' * 32, role='user', content='x')


def test_server_side_roles_are_allowed_by_the_store(store):
    """The runtime writes assistant turns itself; only the public API forbids clients doing so."""
    conversation = store.create_conversation(project_id='P-1', actor='alice')
    message = store.append_message(conversation['id'], role='assistant', content='服务端回复')
    assert message['role'] == 'assistant'
    with pytest.raises(ValueError):
        store.append_message(conversation['id'], role='engineer', content='未知角色')


def test_client_contract_forbids_assistant_role():
    with pytest.raises(ValidationError):
        MessageCreate.model_validate({'role': 'assistant', 'content': '伪造助手消息'})
    assert MessageCreate.model_validate({'content': '正常提问'}).role == 'user'


def test_conversation_id_pattern_is_strict():
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({'project_id': 'P-1', 'title': 'x' * 200})
    with pytest.raises(ValidationError):
        StartRun.model_validate({'result_hash': 'a' * 64, 'conversation_id': 'not-hex'})


def test_run_records_its_conversation(store, baseline):
    _, result = baseline
    conversation = store.create_conversation(project_id=result['projectId'], actor='alice')
    run = store.create(result=result, actor='alice', conversation_id=conversation['id'])
    assert run['conversationId'] == conversation['id']
    assert run['status'] == 'queued'


def test_idempotent_request_cannot_be_rebound_to_another_conversation(store, baseline):
    _, result = baseline
    first = store.create_conversation(project_id=result['projectId'], actor='alice')
    second = store.create_conversation(project_id=result['projectId'], actor='alice')
    store.create(result=result, actor='alice', client_request_id='same-key-0001',
                 conversation_id=first['id'])
    with pytest.raises(ConflictError, match='会话'):
        store.create(result=result, actor='alice', client_request_id='same-key-0001',
                     conversation_id=second['id'])


def test_api_conversation_roundtrip(api):
    client, result = api
    created = client.post('/api/agents/conversations', headers=headers(),
                           json={'project_id': result['projectId'], 'title': '会话'})
    assert created.status_code == 201, created.text
    cid = created.json()['id']
    assert client.post(f'/api/agents/conversations/{cid}/messages', headers=headers(),
                       json={'role': 'user', 'content': '这是根因吗？'}).status_code == 201
    forbidden = client.post(f'/api/agents/conversations/{cid}/messages', headers=headers(),
                             json={'role': 'assistant', 'content': '伪造'})
    assert forbidden.status_code == 422
    detail = client.get(f'/api/agents/conversations/{cid}', headers=headers())
    assert detail.status_code == 200
    messages = detail.json()['messages']
    # The engineer's turn plus the assistant's status receipt.
    assert [m['role'] for m in messages] == ['user', 'assistant']
    assert messages[0]['content'] == '这是根因吗？'
    assert detail.json()['formalConstructionIssue'] is False


def test_api_viewer_cannot_open_conversation(api):
    client, result = api
    created = client.post('/api/agents/conversations', headers=headers(),
                           json={'project_id': result['projectId'], 'title': '会话'})
    cid = created.json()['id']
    assert client.post(f'/api/agents/conversations/{cid}/messages',
                       headers=headers('key-viewer'),
                       json={'role': 'user', 'content': 'x'}).status_code == 403


def test_api_unknown_conversation_is_404(api):
    client, _ = api
    assert client.get('/api/agents/conversations/' + '0' * 32, headers=headers()).status_code == 404


def test_api_run_can_start_from_conversation(api):
    client, result = api
    created = client.post('/api/agents/conversations', headers=headers(),
                           json={'project_id': result['projectId'], 'title': '会话'})
    cid = created.json()['id']
    run = client.post('/api/agents/runs', headers=headers(),
                      json={'result_hash': result['resultHash'], 'conversation_id': cid,
                            'client_request_id': 'api-conv-0001'})
    assert run.status_code == 201, run.text
    assert run.json()['conversationId'] == cid


def test_existing_agent_behaviour_unchanged(api):
    client, _ = api
    catalog = client.get('/api/agents/catalog', headers=headers()).json()
    assert len(catalog['roles']) == 8
    assert len(catalog['tools']) == 6
    assert catalog['formalConstructionIssue'] is False
    model = client.get('/api/agents/model/configuration', headers=headers()).json()
    assert model['toolExecutionByModel'] is False


def test_roles_endpoint_reserves_eight_positions(api):
    client, _ = api
    response = client.get('/api/agents/roles', headers=headers())
    assert response.status_code == 200
    roles = response.json()['roles']
    assert [r['id'] for r in roles] == ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
    assert {r['implementation'] for r in roles} == {'pilot', 'planned'}
    assert {r['id'] for r in roles if r['executable']} == {'A5', 'A6'}
    assert response.json()['formalConstructionIssue'] is False


def test_posting_a_message_writes_an_assistant_reply(api):
    client, result = api
    created = client.post('/api/agents/conversations', headers=headers(),
                           json={'project_id': result['projectId'], 'title': '会话'})
    cid = created.json()['id']
    posted = client.post(f'/api/agents/conversations/{cid}/messages', headers=headers(),
                          json={'role': 'user', 'content': '这 46 项先改哪个？'})
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert body['message']['role'] == 'user'
    assert body['reply']['role'] == 'assistant'
    assert body['reply']['metadata']['answerKind'] == 'status_receipt'
    assert body['reply']['metadata']['engineeringJudgement'] is False
    assert body['formalConstructionIssue'] is False
    assert '不做工程判断' in body['reply']['content']


def test_assistant_reply_reads_the_actual_project_state(api):
    """The receipt must report stored project state, not invented content."""
    client, result = api
    created = client.post('/api/agents/conversations', headers=headers(),
                           json={'project_id': result['projectId'], 'title': '会话'})
    cid = created.json()['id']
    reply = client.post(f'/api/agents/conversations/{cid}/messages', headers=headers(),
                         json={'role': 'user', 'content': '状态'}).json()['reply']
    assert 'A5' in reply['content'] and 'A8' in reply['content']
    assert '可执行角色' in reply['content']
    assert '待建角色' in reply['content']
