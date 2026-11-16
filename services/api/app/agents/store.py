"""Transactional SQLite Agent runs, immutable candidates and compare-and-swap adoption.

A durable run is NOT an executable pickle. Only finite, allowlisted stages are
stored; workers use an atomic lease and can safely re-claim after a crash.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json,os,sqlite3,time


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class ConflictError(ValueError):
    pass


class AgentStore:
    def __init__(self,db_path=None):
        default=Path(os.getenv('PITGUARD_DB_PATH', str(Path(__file__).resolve().parents[2]/'pitguard.sqlite3')))
        self.path=Path(db_path or os.getenv('PITGUARD_AGENT_DB_PATH') or default.with_name('pitguard_agent_v388.sqlite3'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, section_id TEXT NOT NULL,
                    actor TEXT NOT NULL, source_result_hash TEXT NOT NULL,
                    source_snapshot_hash TEXT NOT NULL, status TEXT NOT NULL,
                    step TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    candidate_limit INTEGER NOT NULL, max_rounds INTEGER NOT NULL DEFAULT 1,
                    focus TEXT NOT NULL DEFAULT 'auto', model_mode TEXT NOT NULL,
                    max_evaluations INTEGER NOT NULL DEFAULT 15,
                    max_runtime_seconds INTEGER NOT NULL DEFAULT 900,
                    diagnostics_json TEXT, plan_json TEXT, summary_json TEXT,
                    error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until REAL, revision INTEGER NOT NULL DEFAULT 0,
                    client_request_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(actor,client_request_id));
                CREATE INDEX IF NOT EXISTS runs_schedule ON runs(status,lease_until,created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    event_key TEXT NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id,event_key));
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, proposal_index INTEGER NOT NULL,
                    patch_json TEXT NOT NULL, snapshot_hash TEXT, result_hash TEXT,
                    metrics_json TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                    round_index INTEGER NOT NULL DEFAULT 1, parent_result_hash TEXT,
                    created_at TEXT NOT NULL, UNIQUE(run_id,proposal_index));
                CREATE TABLE IF NOT EXISTS candidate_evidence (
                    candidate_id TEXT NOT NULL, field_path TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(candidate_id,field_path));
                CREATE TABLE IF NOT EXISTS selection (
                    project_id TEXT NOT NULL, section_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL, result_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(project_id,section_id));
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, candidate_id TEXT,
                    decision TEXT NOT NULL, comment TEXT NOT NULL, reviewer TEXT NOT NULL,
                    previous_snapshot_hash TEXT NOT NULL, selected_snapshot_hash TEXT,
                    created_at TEXT NOT NULL, UNIQUE(run_id));
            ''')
            # Migration for installations that ran an early V3.88 pilot build.
            columns={x[1] for x in c.execute('PRAGMA table_info(runs)')}
            if 'pause_requested' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0')
            if 'max_rounds' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT 1')
            if 'focus' not in columns:
                c.execute("ALTER TABLE runs ADD COLUMN focus TEXT NOT NULL DEFAULT 'auto'")
            if 'max_evaluations' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN max_evaluations INTEGER NOT NULL DEFAULT 15')
            if 'max_runtime_seconds' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN max_runtime_seconds INTEGER NOT NULL DEFAULT 900')
            candidate_columns={x[1] for x in c.execute('PRAGMA table_info(candidates)')}
            if 'round_index' not in candidate_columns:
                c.execute('ALTER TABLE candidates ADD COLUMN round_index INTEGER NOT NULL DEFAULT 1')
            if 'parent_result_hash' not in candidate_columns:
                c.execute('ALTER TABLE candidates ADD COLUMN parent_result_hash TEXT')

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=12.0,isolation_level=None)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=12000')
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        try: yield c
        finally: c.close()

    @staticmethod
    def _run(row):
        if row is None: raise KeyError('Agent run not found')
        r=dict(row)
        for old,new in [('diagnostics_json','diagnostics'),('plan_json','plan'),('summary_json','summary')]:
            r[new]=json.loads(r.pop(old)) if r.get(old) else None
        return {('projectId' if k=='project_id' else
                 'sectionId' if k=='section_id' else
                 'sourceResultHash' if k=='source_result_hash' else
                 'sourceSnapshotHash' if k=='source_snapshot_hash' else
                 'maxCandidates' if k=='candidate_limit' else
                 'maxRounds' if k=='max_rounds' else
                 'maxEvaluations' if k=='max_evaluations' else
                 'maxRuntimeSeconds' if k=='max_runtime_seconds' else
                 'modelMode' if k=='model_mode' else
                 'cancelRequested' if k=='cancel_requested' else
                 'pauseRequested' if k=='pause_requested' else
                 'createdAt' if k=='created_at' else
                 'updatedAt' if k=='updated_at' else
                 'leaseOwner' if k=='lease_owner' else
                 'leaseUntil' if k=='lease_until' else
                 'clientRequestId' if k=='client_request_id' else k):
                bool(v) if k in ('cancel_requested','pause_requested') else v for k,v in r.items()}

    def create(self,*,result,actor:str,max_candidates:int=3,max_rounds:int=1,focus:str='auto',model_mode:str='off',
               max_evaluations:int|None=None,max_runtime_seconds:int=900,client_request_id=None):
        rh=result['resultHash'];sh=result['snapshotHash'];pr=result['projectId'];sec=result['sectionId']
        rid=uuid4().hex;ts=now()
        max_evaluations = max_evaluations if max_evaluations is not None else max_candidates*max_rounds
        if (not isinstance(max_evaluations,int) or isinstance(max_evaluations,bool) or
             not 1<=max_evaluations<=15 or not isinstance(max_runtime_seconds,int) or
             isinstance(max_runtime_seconds,bool) or not 10<=max_runtime_seconds<=7200):
            raise ValueError('Invalid bounded evaluation or runtime budget')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if client_request_id:
                prior=c.execute('SELECT * FROM runs WHERE actor=? AND client_request_id=?',(actor,client_request_id)).fetchone()
                if prior:
                    if (prior['source_result_hash']!=rh or prior['candidate_limit']!=max_candidates
                            or prior['max_rounds']!=max_rounds or prior['focus']!=focus or prior['model_mode']!=model_mode
                            or prior['max_evaluations']!=max_evaluations
                            or prior['max_runtime_seconds']!=max_runtime_seconds):
                        c.execute('ROLLBACK')
                        raise ConflictError('幂等请求号对应不同冻结结果、模型模式或候选预算')
                    c.execute('COMMIT')
                    return self._run(prior)
            selection=c.execute('SELECT * FROM selection WHERE project_id=? AND section_id=?',(pr,sec)).fetchone()
            if selection and selection['snapshot_hash']!=sh:
                c.execute('ROLLBACK')
                raise ConflictError('单剖面Agent工作选择已变更：请从最新冻结结果启动')
            if not selection:
                c.execute('INSERT INTO selection VALUES(?,?,?,?,?,?,?)',(pr,sec,sh,rh,0,actor,ts))
            c.execute('''INSERT INTO runs(id,project_id,section_id,actor,source_result_hash,
                source_snapshot_hash,status,step,progress,candidate_limit,max_rounds,focus,model_mode,
                max_evaluations,max_runtime_seconds,client_request_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid,pr,sec,actor,rh,sh,'queued','diagnose',0,max_candidates,max_rounds,focus,model_mode,
                 max_evaluations,max_runtime_seconds,client_request_id,ts,ts))
            self._event(c,rid,'created','run_created',{'sourceResultHash':rh})
            c.execute('COMMIT')
        return self.get(rid)

    @staticmethod
    def _event(c,rid,key,kind,data):
        c.execute('INSERT OR IGNORE INTO events(run_id,event_key,event_type,payload_json,created_at) VALUES(?,?,?,?,?)',
                  (rid,key,kind,encode(data),now()))

    def get(self,rid):
        with self.connect() as c: return self._run(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def list(self,*,project_id:str,limit:int=30):
        with self.connect() as c:
            return [self._run(x) for x in c.execute('SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?', (project_id,min(limit,100))).fetchall()]

    def events(self,rid,limit=100):
        with self.connect() as c:
            rows=c.execute('SELECT * FROM events WHERE run_id=? ORDER BY id ASC LIMIT ?', (rid,min(limit,500))).fetchall()
            return [{'id':x['id'],'type':x['event_type'],'key':x['event_key'],
                     'payload':json.loads(x['payload_json']),'createdAt':x['created_at']} for x in rows]

    def candidates(self,rid):
        with self.connect() as c:
            rows=c.execute('SELECT * FROM candidates WHERE run_id=? ORDER BY proposal_index',(rid,)).fetchall()
            return [self._candidate(x) for x in rows]

    @staticmethod
    def _candidate(x):
        if x is None: raise KeyError('Candidate not found')
        return {'id':x['id'],'runId':x['run_id'],'proposalIndex':x['proposal_index'],
            'patch':json.loads(x['patch_json']),'snapshotHash':x['snapshot_hash'],
            'resultHash':x['result_hash'],'metrics':json.loads(x['metrics_json']),
            'status':x['status'],'error':x['error'],'createdAt':x['created_at'],
            'roundIndex':x['round_index'],'parentResultHash':x['parent_result_hash']}

    def selection(self,pr,sec):
        with self.connect() as c:
            r=c.execute('SELECT * FROM selection WHERE project_id=? AND section_id=?',(pr,sec)).fetchone()
            if r is None: raise KeyError('Working selection missing')
            return {'projectId':pr,'sectionId':sec,'snapshotHash':r['snapshot_hash'],
                    'resultHash':r['result_hash'],'revision':r['revision'],
                    'updatedBy':r['updated_by'],'formalConstructionIssue':False}

    def claim(self,owner:str,lease_seconds=300,run_id=None):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            cond='''status IN ('queued','running') AND cancel_requested=0
                     AND (lease_until IS NULL OR lease_until<?)'''
            args=[time.time()]
            if run_id:
                cond+=' AND id=?';args.append(run_id)
            row=c.execute('SELECT * FROM runs WHERE '+cond+' ORDER BY created_at ASC LIMIT 1',tuple(args)).fetchone()
            if row is None: c.execute('COMMIT');return None
            c.execute('UPDATE runs SET status=?, lease_owner=?, lease_until=?, updated_at=?, revision=revision+1 WHERE id=?',
                      ('running',owner,time.time()+lease_seconds,now(),row['id']))
            c.execute('COMMIT')
            return self.get(row['id'])

    def heartbeat(self,rid,owner,lease_seconds=300):
        with self.connect() as c:
            row=c.execute('UPDATE runs SET lease_until=? WHERE id=? AND lease_owner=? AND status=?',
                (time.time()+lease_seconds,rid,owner,'running'))
            return row.rowcount==1

    def advance(self,rid,owner,expected_step,*,status,step,progress,event_key,event_type,payload=None,diagnostics=None,plan=None,summary=None,candidate=None,error=None):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not row or row['lease_owner']!=owner or row['step']!=expected_step or row['status']!='running':
                c.execute('ROLLBACK');raise ConflictError('Agent run lease or stage was lost')
            if candidate:
                c.execute('''INSERT OR IGNORE INTO candidates(id,run_id,proposal_index,patch_json,snapshot_hash,
                    result_hash,metrics_json,status,error,round_index,parent_result_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (candidate['id'],rid,candidate['proposalIndex'],encode(candidate['patch']),
                     candidate.get('snapshotHash'),candidate.get('resultHash'),encode(candidate['metrics']),
                     candidate['status'],candidate.get('error'),candidate.get('roundIndex',1),
                     candidate.get('parentResultHash'),now()))
            if row['cancel_requested']:
                status='cancelled';step='done';event_key+=':cancelled';event_type='run_cancelled'
            elif row['pause_requested']:
                status='paused';event_key+=':paused';event_type='run_paused'
            fields=['status=?','step=?','progress=?','lease_owner=NULL','lease_until=NULL','updated_at=?','revision=revision+1']
            values=[status,step,progress,now()]
            for k,v in [('diagnostics_json',diagnostics),('plan_json',plan),('summary_json',summary)]:
                if v is not None:fields.append(k+'=?');values.append(encode(v))
            if error is not None:fields.append('error=?');values.append(str(error)[:800])
            values.append(rid)
            c.execute('UPDATE runs SET '+','.join(fields)+' WHERE id=?',values)
            self._event(c,rid,event_key,event_type,payload or {})
            c.execute('COMMIT')
        return self.get(rid)

    def fail(self,rid,owner,message):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT step FROM runs WHERE id=? AND lease_owner=?',(rid,owner)).fetchone()
            if not row:c.execute('ROLLBACK');return
            c.execute('UPDATE runs SET status=?,error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?',
                      ('failed',str(message)[:800],now(),rid))
            self._event(c,rid,'failure:'+row['step'],'step_failed',{'step':row['step'],'errorType':str(message)[:150]})
            c.execute('COMMIT')

    def pause(self,rid):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status,lease_until FROM runs WHERE id=?',(rid,)).fetchone()
            if row is None:c.execute('ROLLBACK');raise KeyError('Agent run not found')
            if row['status'] not in ('queued','running'):
                c.execute('ROLLBACK');raise ConflictError('仅允许暂停排队或运行中的任务')
            active=(row['status']=='running' and (row['lease_until'] or 0)>time.time())
            state='running' if active else 'paused'
            c.execute('UPDATE runs SET status=?,pause_requested=1,updated_at=? WHERE id=?',(state,now(),rid))
            self._event(c,rid,'pause','pause_requested',{'afterCurrentStep':active})
            c.execute('COMMIT')
        return self.get(rid)

    def cancel(self,rid):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status,lease_until FROM runs WHERE id=?',(rid,)).fetchone()
            if row is None:c.execute('ROLLBACK');raise KeyError('Agent run not found')
            if row['status'] in ('completed','cancelled','rejected','completed_no_candidates'):
                c.execute('COMMIT');return self.get(rid)
            newstatus='cancelled' if row['status']!='running' or (row['lease_until'] or 0)<time.time() else 'running'
            c.execute('UPDATE runs SET status=?,cancel_requested=1,updated_at=? WHERE id=?',(newstatus,now(),rid))
            self._event(c,rid,'cancel','cancel_requested',{})
            c.execute('COMMIT')
        return self.get(rid)

    def resume(self,rid):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if row is None: c.execute('ROLLBACK');raise KeyError('Agent run not found')
            if row['status']=='running' and (row['lease_until'] or 0)>time.time():
                c.execute('ROLLBACK');raise ConflictError('当前任务仍由活动Worker执行')
            if row['status'] not in ('failed','running','paused'):
                c.execute('ROLLBACK');raise ConflictError('仅允许恢复失败或租约过期的运行')
            c.execute('UPDATE runs SET status=?,error=NULL,cancel_requested=0,pause_requested=0,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?',
                      ('queued',now(),rid))
            self._event(c,rid,'resume:'+str(row['revision']),'run_resumed',{'step':row['step']})
            c.execute('COMMIT')
        return self.get(rid)

    def candidate_evidence(self,run_id:str,candidate_id:str)->dict:
        """Sidecar receipts are claims plus verified bytes, never an approval of code applicability."""
        with self.connect() as c:
            candidate=c.execute('SELECT * FROM candidates WHERE id=? AND run_id=?',
                                (candidate_id,run_id)).fetchone()
            run=c.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            if candidate is None or run is None:raise KeyError('Candidate not found in this run')
            receipts=c.execute('SELECT * FROM candidate_evidence WHERE candidate_id=?',
                               (candidate_id,)).fetchall()
        from app.engineering.single_braced_pile.repository import Repository
        from .search_rules import changed_fields
        repo=Repository()
        source=repo.load(run['source_result_hash']);result=repo.load(candidate['result_hash'])
        fields=changed_fields(source['snapshot'],result['snapshot'])
        by_field={r['field_path']:json.loads(r['receipt_json']) for r in receipts}
        return {'schemaVersion':'pitguard.agent-evidence-gate/1','candidateId':candidate_id,
                'resultHash':candidate['result_hash'],'requiredFields':fields,
                'receipts':by_field,'missingFields':sorted(set(fields)-set(by_field)),
                'sourceBytesMatchedAtRegistration':all(r.get('fileBytesVerified') is True for r in by_field.values()),
                'adoptionEvidenceComplete':bool(fields) and set(fields)==set(by_field),
                'sourceByteRecheckAtAdoptionRequired':True,
                'engineeringComplianceVerified':False,'formalConstructionIssue':False}

    def register_evidence(self,run_id:str,candidate_id:str,*,field_path:str,
                          relative_path:str,expected_sha256:str,declared_value,
                          source_reference:str,method_basis:str,reviewer:str)->dict:
        from app.engineering.single_braced_pile.repository import Repository
        from app.engineering.single_braced_pile.assurance.source_bytes import verify_raw_source_file
        from .search_rules import changed_fields
        root=os.getenv('PITGUARD_AGENT_EVIDENCE_ROOT','').strip()
        if not root:raise ConflictError('尚未配置独立来源文件根目录 PITGUARD_AGENT_EVIDENCE_ROOT')
        with self.connect() as c:
            run=c.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            candidate=c.execute('SELECT * FROM candidates WHERE id=? AND run_id=?',
                                (candidate_id,run_id)).fetchone()
            if run is None or candidate is None:raise KeyError('Candidate not found')
            if run['status']!='awaiting_review' or candidate['status']!='calculated':
                raise ConflictError('仅允许对已完成计算且等待校审的候选重新确认参数依据')
        repo=Repository()
        source=repo.load(run['source_result_hash'])
        revised=repo.load(candidate['result_hash'])
        expected=changed_fields(source['snapshot'],revised['snapshot'])
        if field_path not in expected:raise ConflictError('提交字段不属于该候选的实际修改项')
        after=expected[field_path]['after']
        if (isinstance(after,int) and not isinstance(after,bool) and type(declared_value) not in (int,float)
                or isinstance(after,float) and not isinstance(declared_value,(int,float))
                or isinstance(declared_value,bool) or after!=declared_value):
            raise ConflictError('提交的依据数值与封存候选快照不一致')
        receipt=verify_raw_source_file(source_root=Path(root),relative_path=relative_path,
                    expected_sha256=expected_sha256,maximum_size_bytes=25_000_000)
        if receipt['status']!='byte_hash_confirmed':
            raise ConflictError('来源文件原始字节不存在或 SHA256 不一致')
        data={'schemaVersion':'pitguard.agent-parameter-evidence/1',
              'runId':run_id,'candidateId':candidate_id,'candidateSnapshotHash':candidate['snapshot_hash'],
              'candidateResultHash':candidate['result_hash'],'fieldPath':field_path,
              'candidateValue':after,'sourceRelativePath':relative_path,'sourceSha256':expected_sha256,
              'sourceBytes':receipt['bytes'],'sourceReference':source_reference,
              'methodBasis':method_basis,'reviewedBy':reviewer,'reviewedAt':now(),
              'fileBytesVerified':True,'sourceContentInterpretationEngineerDeclared':True,
              'normativeMethodApproved':False,'formalConstructionIssue':False}
        digest=sha256(encode(data).encode('utf8')).hexdigest();data['receiptHash']=digest
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            fresh=c.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
            if not fresh or fresh['status']!='awaiting_review':
                c.execute('ROLLBACK');raise ConflictError('来源复核期间运行状态已经变化')
            previous=c.execute('SELECT receipt_sha256 FROM candidate_evidence WHERE candidate_id=? AND field_path=?',
                               (candidate_id,field_path)).fetchone()
            if previous:
                c.execute('ROLLBACK');raise ConflictError('该候选参数已有不可变复核记录，请新建候选进行更正')
            c.execute('INSERT INTO candidate_evidence VALUES(?,?,?,?,?)',
                      (candidate_id,field_path,digest,encode(data),now()))
            self._event(c,run_id,'evidence:'+candidate_id+':'+field_path,'parameter_evidence_recorded',
                        {'candidateId':candidate_id,'field':field_path,'receiptHash':digest})
            c.execute('COMMIT')
        return data

    def _verify_adoption_evidence(self,c,*,run,candidate,source_result,candidate_result):
        from app.engineering.single_braced_pile.assurance.source_bytes import verify_raw_source_file
        from .search_rules import changed_fields
        fields=changed_fields(source_result['snapshot'],candidate_result['snapshot'])
        if not fields:raise ConflictError('候选未实际修改任何工程参数')
        rows=c.execute('SELECT * FROM candidate_evidence WHERE candidate_id=?',
                       (candidate['id'],)).fetchall()
        records={r['field_path']:json.loads(r['receipt_json']) for r in rows}
        if set(records)!=set(fields):
            raise ConflictError('候选变更参数的原始来源及校审记录尚未全部重新确认')
        root=os.getenv('PITGUARD_AGENT_EVIDENCE_ROOT','').strip()
        if not root:raise ConflictError('未配置工程证据文件根目录')
        for path,change in fields.items():
            record=records[path]
            raw=dict(record);digest=raw.pop('receiptHash',None)
            if (digest!=sha256(encode(raw).encode('utf8')).hexdigest()
                    or record['candidateResultHash']!=candidate['result_hash']
                    or record['candidateSnapshotHash']!=candidate_result['snapshotHash']
                    or record['candidateValue']!=change['after'] or record['fieldPath']!=path):
                raise ConflictError('候选依据记录身份或参数内容不一致')
            checked=verify_raw_source_file(source_root=Path(root),
                 relative_path=record['sourceRelativePath'],expected_sha256=record['sourceSha256'],maximum_size_bytes=25_000_000)
            if checked['status']!='byte_hash_confirmed':
                raise ConflictError('采用前来源原始文件字节核验失败')

    def decide(self,rid,*,candidate_id,decision,expected_snapshot_hash,acknowledgement,comment,reviewer):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            run=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if run is None:c.execute('ROLLBACK');raise KeyError('Agent run not found')
            if run['status']!='awaiting_review':c.execute('ROLLBACK');raise ConflictError('任务尚未进入人工审批状态或已完成')
            current=c.execute('SELECT * FROM selection WHERE project_id=? AND section_id=?',
                              (run['project_id'],run['section_id'])).fetchone()
            if not current or current['snapshot_hash']!=expected_snapshot_hash or current['snapshot_hash']!=run['source_snapshot_hash']:
                c.execute('ROLLBACK');raise ConflictError('源设计工作版本已变化，禁止采用旧候选')
            if not acknowledgement:c.execute('ROLLBACK');raise ConflictError('须确认工程方法和正式签发尚未闭合')
            adopted_snapshot=None
            if decision=='adopt_for_working_copy':
                candidate=c.execute('SELECT * FROM candidates WHERE id=? AND run_id=?',(candidate_id,rid)).fetchone()
                if not candidate:c.execute('ROLLBACK');raise ConflictError('候选不存在或不属于此运行')
                metrics=json.loads(candidate['metrics_json'])
                if candidate['status']!='calculated' or metrics.get('workingSelectionEligible') is not True:
                    c.execute('ROLLBACK');raise ConflictError('候选未满足工作版采用最低条件')
                # Re-evaluate the immutable source and candidate. A mutable DB
                # metric or a forged review token cannot authorize selection.
                from app.engineering.single_braced_pile.repository import Repository
                from app.engineering.single_braced_pile.analysis import canonical_hash
                from .engine import measure,verify_candidate_lineage
                try:
                    repo=Repository()
                    source_result=repo.load(run['source_result_hash'])
                    candidate_result=repo.load(candidate['result_hash'])
                    verify_candidate_lineage(c,run,candidate,repo)
                    fresh=measure(candidate_result,source_result)
                    if (source_result['snapshotHash']!=run['source_snapshot_hash']
                            or candidate_result['snapshotHash']!=candidate['snapshot_hash']
                            or not fresh['workingSelectionEligible']):
                        raise ValueError('候选源版本、边界条件或工程验收条件校验未通过')
                    self._verify_adoption_evidence(c,run=run,candidate=candidate,
                         source_result=source_result,candidate_result=candidate_result)
                except (FileNotFoundError,ValueError,KeyError,TypeError) as exc:
                    c.execute('ROLLBACK')
                    raise ConflictError('候选复核失败：'+str(exc)) from exc
                adopted_snapshot=candidate['snapshot_hash']
                c.execute('''UPDATE selection SET snapshot_hash=?,result_hash=?,revision=revision+1,
                    updated_by=?,updated_at=? WHERE project_id=? AND section_id=?''',
                    (adopted_snapshot,candidate['result_hash'],reviewer,now(),run['project_id'],run['section_id']))
            else:
                candidate_id=None
            receipt={'runId':rid,'candidateId':candidate_id,'decision':decision,'comment':comment,
                     'reviewer':reviewer,'sourceSnapshotHash':expected_snapshot_hash,
                     'selectedSnapshotHash':adopted_snapshot,'formallyApproved':False,'createdAt':now()}
            receipt_id=sha256(encode(receipt).encode('utf-8')).hexdigest()
            c.execute('''INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)''',
                      (receipt_id,rid,candidate_id,decision,comment,reviewer,expected_snapshot_hash,adopted_snapshot,receipt['createdAt']))
            c.execute('UPDATE runs SET status=?,step=?,progress=100,updated_at=? WHERE id=?',
                      ('completed' if adopted_snapshot else 'rejected','done',now(),rid))
            self._event(c,rid,'decision','human_decision',{'decision':decision,'candidateId':candidate_id,'reviewer':reviewer})
            c.execute('COMMIT')
            return {'decisionId':receipt_id,**receipt,'formalConstructionIssue':False}
