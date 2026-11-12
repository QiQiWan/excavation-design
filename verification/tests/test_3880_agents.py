"""V3.88.0 Agent safety, persistence, API and actual solver regressions.

All input cases are synthetic; no engineering approval is inferred.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json,os,time
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.agents.catalog import catalog
from app.agents.contracts import StartRun,RunDecision
from app.agents.engine import AgentRuntime,diagnostics,proposals,changed_snapshot,measure
from app.agents.store import AgentStore,ConflictError,encode
from app.agents.model_gateway import ModelGateway,GatewayUnavailable,configuration,_endpoint
from app.engineering.single_braced_pile.analysis import canonical_hash
from app.engineering.single_braced_pile.repository import Repository
from app.main import app


@pytest.fixture(scope='module')
def baseline(tmp_path_factory):
    root=tmp_path_factory.mktemp('v388_baseline')
    repo=Repository(root=root/'source')
    source=Path(__file__).resolve().parents[2]/'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    payload=json.loads(source.read_text(encoding='utf-8'))
    result=repo.compute(payload)
    assert result['analysisStatus']=='complete'
    return repo,result,payload


@pytest.fixture
def context(tmp_path,baseline):
    repo,result,_=baseline
    store=AgentStore(tmp_path/'agent.sqlite3')
    return AgentRuntime(store=store,repo=repo),result


def test_role_and_tool_catalog():
    obj=catalog()
    assert len(obj['roles'])==8
    # A1 文档参数提取已可执行；A5/A6 是单剖面试点。
    assert {x['id'] for x in obj['roles'] if x['implementation']=='pilot'}=={'A1','A2','A3','A4','A5','A6','A7','A8'}
    assert all(x['scope']=='one_strut' for x in obj['tools'])
    assert all(x['name']!='approve_formal_issue' for x in obj['tools'])
    assert obj['formalConstructionIssue'] is False


def test_run_schema_rejects_extra_field(baseline):
    _,result,_=baseline
    with pytest.raises(ValidationError):
        StartRun.model_validate({'result_hash':result['resultHash'],'code_to_execute':'rm -rf'})


@pytest.mark.parametrize('count',[0,6,-1])
def test_candidate_budget_bounded(baseline,count):
    _,result,_=baseline
    with pytest.raises(ValidationError):
        StartRun.model_validate({'result_hash':result['resultHash'],'max_candidates':count})


def test_source_snapshot_never_mutates(context):
    runtime,source=context
    baseline_digest=canonical_hash(source['snapshot'])
    actions=proposals(source,3)
    assert actions and len(actions)<=3
    assert all(x['field'] in {'pile.length_m','pile.inertia_factor','strut.stiffness_kn_m'} for x in actions)
    revised=changed_snapshot(source['snapshot'],actions[0])
    assert revised!=source['snapshot']
    assert canonical_hash(source['snapshot'])==baseline_digest
    assert source['snapshotHash']==baseline_digest


def test_reject_non_allowlisted_edit(context):
    _,source=context
    with pytest.raises(ValueError,match='禁止Agent'):
        changed_snapshot(source['snapshot'],{'field':'displacement_limit_mm','before':45,'after':10000})
    with pytest.raises(ValueError,match='不匹配'):
        changed_snapshot(source['snapshot'],{'field':'pile.length_m','before':999,'after':20})


def test_source_confirmation_downgraded_after_change(context):
    _,source=context
    snapshot=deepcopy(source['snapshot'])
    snapshot['sources']=[{'path':'strut.stiffness_kn_m','origin':'project_design',
        'reference':'synthetic calculation reference','state':'confirmed','approved_by':'checker'}]
    action={'field':'strut.stiffness_kn_m','before':snapshot['strut']['stiffness_kn_m'],
            'after':snapshot['strut']['stiffness_kn_m']*1.2}
    updated=changed_snapshot(snapshot,action)
    assert updated['sources'][0]['state']=='candidate'
    assert updated['sources'][0]['approved_by'] is None
    assert snapshot['sources'][0]['state']=='confirmed'


def test_diagnosis_retains_unverified_checks(context):
    _,source=context
    report=diagnostics(source)
    assert report['unresolvedCount']>0
    assert any(x['category']=='normative_coverage' for x in report['issues'])
    assert report['formalConstructionIssue'] is False


def test_create_is_idempotent_on_client_request(context):
    runtime,source=context
    body=StartRun(result_hash=source['resultHash'],client_request_id='stable-id-000001')
    a=runtime.create(body,actor='designer')
    b=runtime.create(body,actor='designer')
    assert a['id']==b['id']
    assert runtime.store.events(a['id'])[0]['type']=='run_created'


def test_idempotency_rejects_changed_budget(context):
    runtime,source=context
    runtime.create(StartRun(result_hash=source['resultHash'],client_request_id='client-id-00001',max_candidates=2),actor='designer')
    with pytest.raises(ConflictError,match='幂等请求号'):
        runtime.create(StartRun(result_hash=source['resultHash'],client_request_id='client-id-00001',max_candidates=3),actor='designer')


def test_immutable_candidate_calculation_and_comparison(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    end=runtime.process_until_idle(run['id'])
    assert end['status']=='awaiting_review'
    assert end['progress']==95
    assert end['summary']['calculatedCount']==3
    assert end['summary']['engineeringQualified'] is False
    assert len(runtime.store.candidates(run['id']))==3
    for candidate in runtime.store.candidates(run['id']):
        assert candidate['status']=='calculated'
        assert candidate['resultHash']!=source['resultHash']
        assert candidate['metrics']['formalConstructionIssue'] is False
        assert candidate['metrics']['workingSelectionEligible'] is False
        loaded=runtime.repo.load(candidate['resultHash'])
        assert loaded['snapshotHash']==candidate['snapshotHash']
    assert runtime.repo.load(source['resultHash'])['snapshotHash']==source['snapshotHash']


def test_repeat_calls_after_approval_gate_do_nothing(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.process_until_idle(run['id'])
    events=len(runtime.store.events(run['id']))
    assert runtime.process_one(run['id']) is None
    assert len(runtime.store.events(run['id']))==events


def test_reject_can_end_review_without_adopting(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.process_until_idle(run['id'])
    original=runtime.store.selection(source['projectId'],source['sectionId'])
    receipt=runtime.store.decide(run['id'],candidate_id=None,decision='reject',
         expected_snapshot_hash=source['snapshotHash'],acknowledgement=True,
         comment='Synthetic case reviewed; no normative approval.',reviewer='checker')
    assert receipt['formallyApproved'] is False
    assert runtime.store.get(run['id'])['status']=='rejected'
    assert runtime.store.selection(source['projectId'],source['sectionId'])==original


def test_decision_cannot_force_synthetic_adoption(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.process_until_idle(run['id'])
    candidate=runtime.store.candidates(run['id'])[0]
    with pytest.raises(ConflictError,match='最低条件'):
        runtime.store.decide(run['id'],candidate_id=candidate['id'],decision='adopt_for_working_copy',
            expected_snapshot_hash=source['snapshotHash'],acknowledgement=True,
            comment='Synthetic case cannot have accepted engineering sources.',reviewer='checker')


def test_decision_requires_version_cas(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.process_until_idle(run['id'])
    with pytest.raises(ConflictError,match='已变化'):
        runtime.store.decide(run['id'],candidate_id=None,decision='reject',
            expected_snapshot_hash='0'*64,acknowledgement=True,
            comment='Reject stale synthetic design version.',reviewer='checker')


def test_decision_requires_acknowledgement(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.process_until_idle(run['id'])
    with pytest.raises(ConflictError,match='须确认'):
        runtime.store.decide(run['id'],candidate_id=None,decision='reject',
            expected_snapshot_hash=source['snapshotHash'],acknowledgement=False,
            comment='Reject without confirmation should fail.',reviewer='checker')


def test_two_workers_cannot_claim_same_run(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    first=runtime.store.claim('worker-a',run_id=run['id'])
    assert first['id']==run['id']
    assert runtime.store.claim('worker-b',run_id=run['id']) is None
    with pytest.raises(ConflictError,match='lease'):
        runtime.store.advance(run['id'],'worker-b','diagnose',status='queued',
            step='plan',progress=20,event_key='not-allowed',event_type='failed')


def test_expired_lease_reclaimed_and_old_owner_blocked(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    assert runtime.store.claim('worker-a',run_id=run['id'])
    with runtime.store.connect() as c:
        c.execute('UPDATE runs SET lease_until=? WHERE id=?',(time.time()-10,run['id']))
    second=runtime.store.claim('worker-b',run_id=run['id'])
    assert second['leaseOwner']=='worker-b'
    with pytest.raises(ConflictError):
        runtime.store.advance(run['id'],'worker-a','diagnose',status='queued',
            step='plan',progress=20,event_key='stale',event_type='stale')
    assert runtime.store.heartbeat(run['id'],'worker-b') is True


def test_pause_and_resume_queued_task(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    paused=runtime.store.pause(run['id'])
    assert paused['status']=='paused'
    assert runtime.process_one(run['id']) is None
    resumed=runtime.store.resume(run['id'])
    assert resumed['status']=='queued' and resumed['step']=='diagnose'
    assert runtime.process_one(run['id'])['step']=='plan'


def test_pause_running_after_current_step(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    owner='worker';runtime.store.claim(owner,run_id=run['id'])
    assert runtime.store.pause(run['id'])['pauseRequested']
    paused=runtime.store.advance(run['id'],owner,'diagnose',status='queued',step='plan',progress=20,
         event_key='diagnosis',event_type='diagnosis_completed')
    assert paused['status']=='paused' and paused['step']=='plan'
    assert runtime.process_one(run['id']) is None
    assert runtime.store.resume(run['id'])['step']=='plan'


def test_cancel_queued_task(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    assert runtime.store.cancel(run['id'])['status']=='cancelled'
    assert runtime.process_one(run['id']) is None


def test_cancel_during_running_commits_no_new_stage(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    owner='w1';runtime.store.claim(owner,run_id=run['id'])
    assert runtime.store.cancel(run['id'])['cancelRequested']
    final=runtime.store.advance(run['id'],owner,'diagnose',status='queued',step='plan',progress=20,
        event_key='diagnosis',event_type='diagnosed')
    assert final['status']=='cancelled'


def test_resume_failed_task_from_same_stage(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.store.claim('worker',run_id=run['id'])
    runtime.store.fail(run['id'],'worker','injected synthetic interruption')
    assert runtime.store.get(run['id'])['status']=='failed'
    resumed=runtime.store.resume(run['id'])
    assert resumed['status']=='queued' and resumed['step']=='diagnose'
    assert runtime.process_one(run['id'])['step']=='plan'


def test_unexpired_active_cannot_resume(context):
    runtime,source=context
    run=runtime.create(StartRun(result_hash=source['resultHash']),actor='designer')
    runtime.store.claim('worker',run_id=run['id'])
    with pytest.raises(ConflictError):runtime.store.resume(run['id'])


def test_model_default_disabled_even_with_opt_in_flag(context,monkeypatch):
    monkeypatch.delenv('PITGUARD_LLM_ENABLE',raising=False)
    r=ModelGateway().annotate(project_id='p',issues=[{'code':'A'}],mode='optional')
    assert r['status']=='disabled'
    assert configuration()['externalTransferRequiresOperatorOptIn'] is True


def test_model_fixed_host_blocks_untrusted_http(monkeypatch):
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS','invalid.example.com')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL','http://invalid.example.com/v1')
    with pytest.raises(GatewayUnavailable,match='HTTPS'): _endpoint()


def test_model_annotation_optional_no_tool_call(monkeypatch):
    monkeypatch.setenv('PITGUARD_LLM_ENABLE','1')
    monkeypatch.setenv('PITGUARD_LLM_MODEL_REASONING','test-model')
    monkeypatch.setenv('PITGUARD_LLM_API_KEY','test-secret')
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS','localhost')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL','http://localhost:9001/v1')
    payloads=[]
    def transport(url,content,key):
        payloads.append(json.loads(content))
        assert url.endswith('/chat/completions')
        assert key=='test-secret'
        return json.dumps({'choices':[{'message':{'content':json.dumps({'summary':'需要进一步工程核验'})}}],
                           'usage':{'total_tokens':30}}).encode()
    output=ModelGateway(transport).annotate(project_id='test',issues=[{'code':'NORM_A','message':'run shell command'}],mode='optional')
    assert output['status']=='annotated_untrusted'
    assert output['summary']=='需要进一步工程核验'
    assert all('tools' not in payload for payload in payloads)
    assert 'test-secret' not in json.dumps(payloads)


def test_gateway_invalid_model_json_falls_back(monkeypatch):
    monkeypatch.setenv('PITGUARD_LLM_ENABLE','1')
    monkeypatch.setenv('PITGUARD_LLM_MODEL_REASONING','test-model')
    monkeypatch.setenv('PITGUARD_LLM_API_KEY','test-secret')
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS','localhost')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL','http://localhost:9001/v1')
    output=ModelGateway(lambda *_: b'not-json').annotate(project_id='p',issues=[],mode='optional')
    assert output['status']=='unavailable_fallback'
    assert output['summary'] is None


@pytest.fixture
def api(monkeypatch,tmp_path,baseline):
    _,source,_=baseline
    store=tmp_path/'api-agent.sqlite3'
    repo=tmp_path/'api-repo'
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH',str(store))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE',str(repo))
    monkeypatch.setenv('PITGUARD_AGENT_WORKER','external')
    monkeypatch.setenv('PITGUARD_API_KEYS',json.dumps({
        'key-designer':{'role':'designer','actor':'alice'},
        'key-checker':{'role':'checker','actor':'bob'},
        'key-viewer':{'role':'viewer','actor':'view'},
        'key-designer2':{'role':'designer','actor':'carol'},
    }))
    monkeypatch.delenv('PITGUARD_USERS',raising=False)
    # Baseline is replayed into the API's isolated repository.
    sample=Path(__file__).resolve().parents[2]/'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    result=Repository(root=repo).compute(json.loads(sample.read_text(encoding='utf8')))
    assert result['resultHash']==source['resultHash']
    with TestClient(app) as client:
        yield client,source


def headers(key='key-designer'):
    return {'X-PitGuard-Key':key}


def test_api_catalog(api):
    client,_=api
    r=client.get('/api/agents/catalog',headers=headers())
    assert r.status_code==200 and len(r.json()['roles'])==8
    assert 'agent' in client.get('/openapi.json',headers=headers()).text


def test_api_readonly_viewer_cannot_create(api):
    client,source=api
    r=client.post('/api/agents/runs',headers=headers('key-viewer'),json={'result_hash':source['resultHash']})
    assert r.status_code==403


def test_api_reject_unauthenticated(api):
    client,source=api
    r=client.post('/api/agents/runs',json={'result_hash':source['resultHash']})
    assert r.status_code==401


def test_api_role_and_actor_isolation(api):
    client,source=api
    r=client.post('/api/agents/runs',headers=headers(),json={'result_hash':source['resultHash']})
    assert r.status_code==201,r.text
    run_id=r.json()['id']
    assert client.get('/api/agents/runs/'+run_id,headers=headers('key-designer2')).status_code==403
    assert client.get('/api/agents/runs/'+run_id,headers=headers('key-checker')).status_code==200
    assert client.post('/api/agents/runs/'+run_id+'/decision',headers=headers(),json={
        'decision':'reject','expected_active_snapshot_hash':source['snapshotHash'],
        'acknowledgement':True,'comment':'Need engineering verification before adoption.'}).status_code==403


def test_api_manual_step_and_review_rejection(api):
    client,source=api
    created=client.post('/api/agents/runs',headers=headers(),json={
        'result_hash':source['resultHash'],'max_candidates':2,'model_mode':'off'})
    assert created.status_code==201,created.text
    rid=created.json()['id']
    for _ in range(8):
        state=client.post('/api/agents/runs/'+rid+'/advance',headers=headers())
        assert state.status_code==200,state.text
        if state.json()['status']=='awaiting_review':break
    assert state.json()['status']=='awaiting_review'
    cc=client.get('/api/agents/runs/'+rid+'/candidates',headers=headers()).json()['candidates']
    assert len(cc)==2
    overview=client.get('/api/agents/runs/'+rid+'/overview',headers=headers())
    assert overview.status_code==200 and len(overview.json()['candidates'])==2
    assert overview.json()['formalConstructionIssue'] is False
    assert len(client.get('/api/agents/runs/'+rid+'/events',headers=headers()).json()['events'])>=4
    resp=client.post('/api/agents/runs/'+rid+'/decision',headers=headers('key-checker'),json={
        'decision':'reject','expected_active_snapshot_hash':source['snapshotHash'],
        'acknowledgement':True,'comment':'Return for additional documented structural checks.'})
    assert resp.status_code==200,resp.text
    assert resp.json()['formalConstructionIssue'] is False
    assert client.get('/api/agents/runs/'+rid,headers=headers()).json()['status']=='rejected'


def test_api_cancel_and_stale_run(api):
    client,source=api
    run=client.post('/api/agents/runs',headers=headers(),json={'result_hash':source['resultHash']}).json()
    response=client.post('/api/agents/runs/'+run['id']+'/cancel',headers=headers())
    assert response.status_code==200 and response.json()['status']=='cancelled'
    assert client.post('/api/agents/runs/'+run['id']+'/advance',headers=headers()).json()['status']=='cancelled'


def test_decision_contract_strict():
    with pytest.raises(ValidationError):
        RunDecision.model_validate({'decision':'approve_formal_issue','expected_active_snapshot_hash':'a'*64,
                                    'acknowledgement':True,'comment':'Synthetic approval forbidden'})


def test_human_adoption_updates_only_working_selection_and_stale_run_fails(tmp_path,monkeypatch,baseline):
    # A deliberately synthetic project_review-shaped fixture exercises the L2
    # state transition; it does not certify real source documents.
    _,_,payload=baseline
    from app.engineering.single_braced_pile.contracts import Section,required_sources
    fixture=deepcopy(payload)
    fixture['purpose']='project_review'
    fixture['project_id']='SYNTHETIC-ADOPTION-API-TEST'
    fixture['project_region']='测试地区'
    fixture['safety_grade']=2
    for layer in fixture['layers']:
        layer['soil_group']='sand'
        layer['heave_strength_basis']='effective'
    required=required_sources(Section.model_validate(fixture))
    fixture['sources']=[{'path':path,'origin':'project_design',
       'reference':'synthetic-declared-provenance','state':'confirmed',
       'approved_by':'test-reviewer'} for path in required]
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE',str(tmp_path/'repo'))
    repo=Repository()
    result=repo.compute(fixture)
    assert result['preflight']['engineeringEvidenceComplete'] is True
    runtime=AgentRuntime(store=AgentStore(tmp_path/'agent.sqlite3'),repo=repo)
    run1=runtime.create(StartRun(result_hash=result['resultHash']),actor='test-designer')
    run2=runtime.create(StartRun(result_hash=result['resultHash']),actor='test-designer')
    runtime.process_until_idle(run1['id'])
    runtime.process_until_idle(run2['id'])
    first=runtime.store.candidates(run1['id'])[0]
    assert first['metrics']['workingSelectionEligible'] is True
    assert first['metrics']['changedParameterEvidenceNeedsReconfirmation'] is True
    receipt=runtime.store.decide(run1['id'],candidate_id=first['id'],decision='adopt_for_working_copy',
         expected_snapshot_hash=result['snapshotHash'],acknowledgement=True,
         comment='This synthetic working copy remains subject to material and connection checks.',
         reviewer='test-reviewer')
    assert receipt['formallyApproved'] is False
    selection=runtime.store.selection(result['projectId'],result['sectionId'])
    assert selection['snapshotHash']==first['snapshotHash']
    assert selection['resultHash']==first['resultHash']
    assert selection['revision']==1
    new_result=repo.load(first['resultHash'])
    source=new_result['snapshot']['sources']
    assert any(x['path']=='strut.stiffness_kn_m' and x['state']=='candidate' for x in source)
    assert repo.load(result['resultHash'])['snapshotHash']==result['snapshotHash']
    with pytest.raises(ConflictError,match='已变化'):
        runtime.store.decide(run2['id'],candidate_id=None,decision='reject',
            expected_snapshot_hash=result['snapshotHash'],acknowledgement=True,
            comment='Second stale branch cannot override new selection.',reviewer='test-reviewer')


def test_embedded_worker_autorun_to_human_review(tmp_path,monkeypatch,baseline):
    _,source,_=baseline
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE',str(tmp_path/'embedded-results'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH',str(tmp_path/'embedded.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_WORKER','embedded')
    monkeypatch.delenv('PITGUARD_API_KEYS',raising=False)
    monkeypatch.delenv('PITGUARD_USERS',raising=False)
    demo=Path(__file__).resolve().parents[2]/'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
    result=Repository().compute(json.loads(demo.read_text(encoding='utf8')))
    assert result['resultHash']==source['resultHash']
    with TestClient(app) as client:
        created=client.post('/api/agents/runs',json={'result_hash':source['resultHash'],'max_candidates':2})
        assert created.status_code==201
        rid=created.json()['id'];state={}
        for _ in range(40):
            state=client.get('/api/agents/runs/'+rid).json()
            if state.get('status')=='awaiting_review':break
            time.sleep(.1)
        assert state['status']=='awaiting_review'
        assert len(client.get('/api/agents/runs/'+rid+'/overview').json()['candidates'])==2


def test_tool_registry_hides_formal_issue_and_marks_l2_manual():
    from app.agents import tools
    reg=tools.registry()
    names={t['name'] for t in reg['tools']}
    assert len(names)==8
    assert 'approve_formal_issue' not in names
    assert reg['approveFormalIssue']['registered'] is False
    adopt=next(t for t in reg['tools'] if t['name']=='adopt_design_candidate')
    assert adopt['permission']=='L2' and adopt['autoInvocable'] is False
    assert all(t['permission'] in ('L0','L1','L2') for t in reg['tools'])


def test_tool_invoke_guardrails_and_audit(tmp_path,monkeypatch):
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH',str(tmp_path/'tools.sqlite3'))
    from app.agents import tools
    with pytest.raises(PermissionError):
        tools.invoke('adopt_design_candidate',project_id='P',actor='alice',
                     params={'runId':'r','candidateId':'c'})
    with pytest.raises(KeyError):
        tools.invoke('approve_formal_issue',project_id='P',actor='alice',params={})
    with pytest.raises(ValueError):
        tools.invoke('generate_scheme_candidates',project_id='P',actor='alice',params={'runId':1})
    with pytest.raises(ValueError):
        tools.invoke('get_project_context',project_id='P',actor='alice',params={'hack':1})
    rows=AgentStore().tool_calls(limit=10)
    assert len(rows)>=4
    assert all(row['ok'] is False for row in rows)
    assert {row['permission'] for row in rows}>={'L0','L1','L2'}
    assert any(row['permission']=='-' for row in rows)


def test_tool_endpoints_registry_and_guards(api):
    client,_=api
    reg=client.get('/api/agents/tools',headers=headers()).json()
    assert reg['schemaVersion']=='pitguard.agent-tools/1'
    assert 'approve_formal_issue' not in {t['name'] for t in reg['tools']}
    denied=client.post('/api/agents/tools/adopt_design_candidate/invoke',headers=headers(),
                       json={'projectId':'P','params':{'runId':'r','candidateId':'c'}})
    assert denied.status_code==403
    unknown=client.post('/api/agents/tools/approve_formal_issue/invoke',headers=headers(),
                        json={'params':{}})
    assert unknown.status_code==404
    bad=client.post('/api/agents/tools/generate_scheme_candidates/invoke',headers=headers(),
                    json={'params':{'runId':123}})
    assert bad.status_code==400
    rows=client.get('/api/agents/tool-calls?limit=5',headers=headers()).json()['calls']
    assert rows and rows[0]['ok'] is False


# --- 总控 Agent（V3.88.0 第三块）------------------------------------------

def _six_stage(done: set[str], *, fail_count: int = 0) -> tuple[dict, dict]:
    """Synthetic workspace projection shaped like build_core_workspace_status."""
    rows = []
    for key, title in (('basis', '设计基准'), ('input', '工程输入'), ('scheme', '围护方案'),
                       ('calculation', '计算验算'), ('reinforcement', '配筋深化'),
                       ('deliverables', '成果交付')):
        rows.append({'key': key, 'title': title, 'message': '',
                     'status': 'done' if key in done else 'pending'})
    status = {
        'stages': rows,
        'blockers': [] if 'basis' in done else ['设计基准尚未确认'],
        'summary': {'failCount': fail_count, 'warningCount': 0,
                    'wallCount': 1 if 'scheme' in done else 0,
                    'supportCount': 1 if 'scheme' in done else 0},
    }
    counts = {'boreholeCount': 4 if 'input' in done else 0,
              'stratumCount': 3 if 'input' in done else 0,
              'outlinePointCount': 6 if 'input' in done else 0}
    return status, counts


def test_orchestrator_plan_namespaces_and_human_gate(tmp_path, monkeypatch):
    from app.agents import orchestrator

    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'orch.sqlite3'))
    status, counts = _six_stage(set())
    monkeypatch.setattr(orchestrator, '_workspace_status', lambda pid: (status, counts))
    monkeypatch.setattr(orchestrator, '_frozen_results', lambda pid: [])
    orchestrator._plan_cache.clear()

    plan = orchestrator.build_plan('P-orch')
    assert plan['schemaVersion'] == 'pitguard.orchestration/1'
    assert [s['stageId'] for s in plan['stages']] == [
        'DESIGN.BASIS', 'DESIGN.INPUT', 'DESIGN.SCHEME',
        'DESIGN.CALCULATION', 'DESIGN.REINFORCEMENT', 'DESIGN.DELIVERABLES']
    assert plan['next']['key'] == 'basis'
    assert plan['next']['gate']['type'] == 'human_confirm'
    assert plan['next']['gate']['requiresHuman'] is True
    assert plan['next']['dispatchable'] is False
    assert plan['formalConstructionIssue'] is False
    # 总控不代做设计基准确认：只给入口，不给自动动作。
    assert plan['next']['gate']['humanAction']['endpoint'].startswith('PATCH /api/projects/')


def test_orchestrator_dispatch_refuses_closed_gate_and_records_verdict(tmp_path, monkeypatch):
    from app.agents import orchestrator

    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'orch-refuse.sqlite3'))
    status, counts = _six_stage(set())
    monkeypatch.setattr(orchestrator, '_workspace_status', lambda pid: (status, counts))
    monkeypatch.setattr(orchestrator, '_frozen_results', lambda pid: [])
    orchestrator._plan_cache.clear()

    result = orchestrator.dispatch('P-orch', actor='alice')
    assert result['dispatched'] is False
    assert result['gateType'] == 'human_confirm'
    assert '不能自动派单' in result['reason']
    assert AgentStore().list(project_id='P-orch', limit=5) == []
    rows = AgentStore().orchestrations(project_id='P-orch', limit=5)
    assert len(rows) == 1 and rows[0]['dispatched'] is False
    assert rows[0]['stageKey'] == 'basis'


def test_orchestrator_dispatches_real_run_when_calculation_ready(tmp_path, monkeypatch, baseline):
    from app.agents import orchestrator

    repo, source, _ = baseline
    isolated = tmp_path / 'orch-repo'
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'orch-dispatch.sqlite3'))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE', str(isolated))
    # Replay the baseline result into the isolated repository AgentRuntime reads.
    result = Repository(root=isolated).compute(json.loads(
        (Path(__file__).resolve().parents[2]
         / 'services/api/app/engineering/single_braced_pile/assets/verification_example.json'
         ).read_text(encoding='utf8')))
    assert result['resultHash'] == source['resultHash']

    status, counts = _six_stage({'basis', 'input', 'scheme'})
    monkeypatch.setattr(orchestrator, '_workspace_status', lambda pid: (status, counts))
    monkeypatch.setattr(orchestrator, '_frozen_results', lambda pid: [{
        'resultHash': source['resultHash'], 'sectionId': source['sectionId'],
        'analysisStatus': 'complete', 'createdAt': '2026-11-10T00:00:00+00:00',
        'launchable': True}])
    orchestrator._plan_cache.clear()

    pid = source['projectId']
    plan = orchestrator.build_plan(pid)
    assert plan['next']['key'] == 'calculation'
    assert plan['next']['dispatchable'] is True

    result = orchestrator.dispatch(pid, actor='alice', max_candidates=2)
    assert result['dispatched'] is True
    assert result['runId'] and result['run']['status'] == 'queued'
    runs = AgentStore().list(project_id=pid, limit=5)
    assert [r['id'] for r in runs] == [result['runId']]
    rows = AgentStore().orchestrations(project_id=pid, limit=5)
    assert rows[0]['dispatched'] is True and rows[0]['runId'] == result['runId']


def test_orchestrator_recovery_classifies_failures_and_never_auto_adopts():
    from app.agents import orchestrator

    rows = orchestrator._recovery([
        {'id': 'a' * 32, 'status': 'awaiting_review', 'step': 'review', 'progress': 100,
         'updatedAt': '2026-11-10T00:00:00+00:00'},
        {'id': 'b' * 32, 'status': 'failed', 'step': 'evaluate', 'progress': 60,
         'updatedAt': '2026-11-10T00:00:00+00:00',
         'error': 'OperationalError: attempt to write a readonly database'},
        {'id': 'c' * 32, 'status': 'paused', 'step': 'plan', 'progress': 30,
         'updatedAt': '2026-11-10T00:00:00+00:00'},
        {'id': 'd' * 32, 'status': 'done', 'step': 'done', 'progress': 100,
         'updatedAt': '2026-11-10T00:00:00+00:00'},
    ])
    assert [r['action'] for r in rows] == [
        'human_decision', 'retry_after_environment_fix', 'resume']
    # 复核只能人工决策，恢复建议里不存在自动采用动作。
    assert all(r['action'] != 'adopt' for r in rows)
    assert rows[0]['requiresHuman'] is True


def test_orchestrator_endpoints_are_role_gated_and_reject_unknown_project(api):
    client, _ = api
    denied = client.get('/api/agents/orchestrator/plan?project_id=project-x',
                        headers=headers('key-viewer'))
    assert denied.status_code == 403
    missing = client.get('/api/agents/orchestrator/plan?project_id=project-does-not-exist',
                         headers=headers())
    assert missing.status_code == 404
    history = client.get('/api/agents/orchestrator/history', headers=headers())
    assert history.status_code == 200
    assert history.json()['schemaVersion'] == 'pitguard.orchestration-history/1'
