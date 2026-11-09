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
                    candidate_limit INTEGER NOT NULL, model_mode TEXT NOT NULL,
                    diagnostics_json TEXT, plan_json TEXT, summary_json TEXT,
                    error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until REAL, revision INTEGER NOT NULL DEFAULT 0,
                    client_request_id TEXT, conversation_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
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
                    created_at TEXT NOT NULL, UNIQUE(run_id,proposal_index));
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
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor TEXT NOT NULL,
                    title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS conversations_project ON conversations(project_id,updated_at);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
            ''')
            # Migration for installations that ran an early V3.88 pilot build.
            columns={x[1] for x in c.execute('PRAGMA table_info(runs)')}
            if 'pause_requested' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0')
            if 'conversation_id' not in columns:
                c.execute('ALTER TABLE runs ADD COLUMN conversation_id TEXT')
            # Run events are mirrored into the linked conversation. source_key
            # makes the mirror idempotent; ordinary turns keep a NULL key.
            message_columns={x[1] for x in c.execute('PRAGMA table_info(messages)')}
            if 'source_key' not in message_columns:
                c.execute('ALTER TABLE messages ADD COLUMN source_key TEXT')
            c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS messages_source_key
                         ON messages(conversation_id,source_key) WHERE source_key IS NOT NULL''')

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
                 'modelMode' if k=='model_mode' else
                 'cancelRequested' if k=='cancel_requested' else
                 'pauseRequested' if k=='pause_requested' else
                 'createdAt' if k=='created_at' else
                 'updatedAt' if k=='updated_at' else
                 'leaseOwner' if k=='lease_owner' else
                 'leaseUntil' if k=='lease_until' else
                 'conversationId' if k=='conversation_id' else
                 'clientRequestId' if k=='client_request_id' else k):
                bool(v) if k in ('cancel_requested','pause_requested') else v for k,v in r.items()}

    def create(self,*,result,actor:str,max_candidates:int=3,model_mode:str='off',
               client_request_id=None,conversation_id:str|None=None):
        rh=result['resultHash'];sh=result['snapshotHash'];pr=result['projectId'];sec=result['sectionId']
        rid=uuid4().hex;ts=now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if client_request_id:
                prior=c.execute('SELECT * FROM runs WHERE actor=? AND client_request_id=?',(actor,client_request_id)).fetchone()
                if prior:
                    if (prior['source_result_hash']!=rh or prior['candidate_limit']!=max_candidates
                            or prior['model_mode']!=model_mode
                            or (prior['conversation_id'] or None)!=(conversation_id or None)):
                        c.execute('ROLLBACK')
                        raise ConflictError('幂等请求号对应不同冻结结果、模型模式、候选预算或会话')
                    c.execute('COMMIT')
                    return self._run(prior)
            selection=c.execute('SELECT * FROM selection WHERE project_id=? AND section_id=?',(pr,sec)).fetchone()
            if selection and selection['snapshot_hash']!=sh:
                c.execute('ROLLBACK')
                raise ConflictError('单剖面Agent工作选择已变更：请从最新冻结结果启动')
            if not selection:
                c.execute('INSERT INTO selection VALUES(?,?,?,?,?,?,?)',(pr,sec,sh,rh,0,actor,ts))
            c.execute('''INSERT INTO runs(id,project_id,section_id,actor,source_result_hash,
                source_snapshot_hash,status,step,progress,candidate_limit,model_mode,client_request_id,conversation_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid,pr,sec,actor,rh,sh,'queued','diagnose',0,max_candidates,model_mode,client_request_id,conversation_id,ts,ts))
            self._event(c,rid,'created','run_created',{'sourceResultHash':rh,
                **({'conversationId':conversation_id} if conversation_id else {})})
            c.execute('COMMIT')
        return self.get(rid)

    EVENT_NOTES = {
        'run_created': '试点任务已创建',
        'diagnosis_completed': '诊断完成，已记录待处理问题',
        'candidate_plan_created': '已生成有界候选方案',
        'candidate_evaluated': '完成一个候选的完整复算',
        'candidate_evaluation_completed': '候选复算全部完成',
        'candidate_comparison_completed': '候选对比完成，等待人工复核',
        'human_decision': '人工复核决策已记录',
        'run_paused': '任务已暂停',
        'run_cancelled': '任务已取消',
        'run_failed': '任务执行失败',
    }

    @staticmethod
    def _event(c,rid,key,kind,data):
        cur=c.execute('INSERT OR IGNORE INTO events(run_id,event_key,event_type,payload_json,created_at) VALUES(?,?,?,?,?)',
                      (rid,key,kind,encode(data),now()))
        if not cur.rowcount:
            return
        row=c.execute('SELECT conversation_id FROM runs WHERE id=?',(rid,)).fetchone()
        cid=row['conversation_id'] if row else None
        if not cid:
            return
        meta={'answerKind':'run_event','runId':rid,'eventKey':key,'eventType':kind,
              'payload':data or {},'engineeringJudgement':False}
        try:
            c.execute('''INSERT OR IGNORE INTO messages
                (conversation_id,role,content,metadata_json,created_at,source_key)
                VALUES(?,?,?,?,?,?)''',
                (cid,'tool',AgentStore.EVENT_NOTES.get(kind,'任务状态更新'),
                 encode(meta),now(),'run:'+rid+':'+key))
            c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(now(),cid))
        except sqlite3.Error:
            pass

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
            'status':x['status'],'error':x['error'],'createdAt':x['created_at']}

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
                    result_hash,metrics_json,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (candidate['id'],rid,candidate['proposalIndex'],encode(candidate['patch']),
                     candidate.get('snapshotHash'),candidate.get('resultHash'),encode(candidate['metrics']),
                     candidate['status'],candidate.get('error'),now()))
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
                from .engine import measure,changed_snapshot
                try:
                    repo=Repository()
                    source_result=repo.load(run['source_result_hash'])
                    candidate_result=repo.load(candidate['result_hash'])
                    action=json.loads(candidate['patch_json'])
                    expected=changed_snapshot(source_result['snapshot'],action)
                    fresh=measure(candidate_result,source_result)
                    if (source_result['snapshotHash']!=run['source_snapshot_hash']
                            or candidate_result['snapshotHash']!=candidate['snapshot_hash']
                            or canonical_hash(expected)!=candidate_result['snapshotHash']
                            or not fresh['workingSelectionEligible']):
                        raise ValueError('候选源版本、边界条件或工程验收条件校验未通过')
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

    # ------------------------------------------------------------------ #
    # Conversation and message records.
    #
    # A conversation is the durable transcript of a designer's session with
    # the engineering assistant. It stores only what was said and what it
    # referenced; it never grants a model any engineering authority.
    # ------------------------------------------------------------------ #

    def create_conversation(self,*,project_id:str,actor:str,title:str='新会话'):
        cid=uuid4().hex;ts=now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?)',
                      (cid,project_id,actor,title,'active',ts,ts))
            c.execute('COMMIT')
        return self.get_conversation(cid)

    def get_conversation(self,cid):
        with self.connect() as c:
            row=c.execute('''SELECT cv.*,(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=cv.id)
                             AS message_count FROM conversations cv WHERE cv.id=?''',(cid,)).fetchone()
        if row is None:raise KeyError('Agent conversation not found')
        r=dict(row)
        return {'id':r['id'],'projectId':r['project_id'],'actor':r['actor'],'title':r['title'],
                'status':r['status'],'messageCount':r['message_count'],
                'createdAt':r['created_at'],'updatedAt':r['updated_at']}

    def list_conversations(self,*,project_id:str,actor:str|None=None):
        with self.connect() as c:
            if actor:
                rows=c.execute('SELECT id FROM conversations WHERE project_id=? AND actor=? '
                               'ORDER BY updated_at DESC LIMIT 100',(project_id,actor)).fetchall()
            else:
                rows=c.execute('SELECT id FROM conversations WHERE project_id=? '
                               'ORDER BY updated_at DESC LIMIT 100',(project_id,)).fetchall()
            ids=[x['id'] for x in rows]
        return [self.get_conversation(i) for i in ids]

    def append_message(self,cid,*,role:str,content:str,metadata:dict|None=None):
        if role not in ('user','assistant','system','tool'):
            raise ValueError('unsupported message role')
        ts=now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT id FROM conversations WHERE id=?',(cid,)).fetchone() is None:
                c.execute('ROLLBACK');raise KeyError('Agent conversation not found')
            cur=c.execute('''INSERT INTO messages(conversation_id,role,content,metadata_json,created_at)
                             VALUES(?,?,?,?,?)''',(cid,role,content,encode(metadata or {}),ts))
            mid=cur.lastrowid
            c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(ts,cid))
            c.execute('COMMIT')
        return {'id':mid,'conversationId':cid,'role':role,'content':content,
                'metadata':metadata or {},'createdAt':ts}

    def messages(self,cid,limit:int=200):
        with self.connect() as c:
            rows=c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?',
                           (cid,min(max(limit,1),1000))).fetchall()
        return [{'id':x['id'],'conversationId':x['conversation_id'],'role':x['role'],
                 'content':x['content'],'metadata':json.loads(x['metadata_json']),
                 'createdAt':x['created_at']} for x in rows]

    def runs_for_conversation(self,cid,limit:int=10):
        """Read-only view of the runs a conversation started or observed."""
        with self.connect() as c:
            rows=c.execute('''SELECT * FROM runs WHERE conversation_id=?
                              ORDER BY created_at DESC LIMIT ?''',
                           (cid,min(max(limit,1),50))).fetchall()
        return [{'id':x['id'],'status':x['status'],'step':x['step'],'progress':x['progress'],
                 'projectId':x['project_id'],'sectionId':x['section_id'],
                 'createdAt':x['created_at'],'updatedAt':x['updated_at']} for x in rows]
