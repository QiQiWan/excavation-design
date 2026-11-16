"""Durable, lease-driven M3 physical candidate Agent. L1 research proposals only.

Uses the existing Agent SQLite database in isolated m3_* tables. Every source
bundle is an immutable content-addressed file and is revalidated by workers.
There is no working-selection mutation or official-design issuance route.
"""
from __future__ import annotations
from hashlib import sha256
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter, time
from uuid import uuid4
import json
import os
import sqlite3

from app.agents.store import AgentStore, ConflictError, encode, now
from app.services.m3_physical_evidence import read_physical_bundle
from app.services.m3_physical_solver import (physical_candidates, solve_physical_candidate,
    objective_metrics, finalize_physical_results)


class SpatialAgentStore:
    def __init__(self,db_path=None):
        self.db_path=Path(db_path or AgentStore().path)
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self.blob_dir=self.db_path.parent/'pitguard_m3_evidence_v3892'
        self.blob_dir.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS m3_runs (
                  id TEXT PRIMARY KEY,actor TEXT NOT NULL,project_id TEXT NOT NULL,
                  bundle_sha256 TEXT NOT NULL, study_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'queued',
                  next_index INTEGER NOT NULL DEFAULT 0, max_evaluations INTEGER NOT NULL,
                  max_runtime_seconds REAL NOT NULL,elapsed_compute_seconds REAL NOT NULL DEFAULT 0,
                  optimizer INTEGER NOT NULL DEFAULT 1, cancel_requested INTEGER NOT NULL DEFAULT 0,
                  pause_requested INTEGER NOT NULL DEFAULT 0,
                  lease_owner TEXT,lease_until REAL,attempt INTEGER NOT NULL DEFAULT 0,
                  error TEXT, report_json TEXT, client_request_id TEXT, revision INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  UNIQUE(actor,client_request_id));
                CREATE INDEX IF NOT EXISTS m3_work ON m3_runs(status,lease_until,created_at);
                CREATE TABLE IF NOT EXISTS m3_candidate_results (
                  run_id TEXT NOT NULL, candidate_index INTEGER NOT NULL,candidate_id TEXT NOT NULL,
                  result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,elapsed_seconds REAL NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(run_id,candidate_index));
                CREATE TABLE IF NOT EXISTS m3_node_reviews (
                  run_id TEXT NOT NULL, candidate_index INTEGER NOT NULL,
                  reviewer TEXT NOT NULL, independent_sha256 TEXT NOT NULL,
                  result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  PRIMARY KEY(run_id,candidate_index,independent_sha256));
                CREATE TABLE IF NOT EXISTS m3_method_reviews (
                  run_id TEXT NOT NULL,candidate_index INTEGER NOT NULL,
                  kind TEXT NOT NULL,reviewer TEXT NOT NULL,
                  review_archive_sha256 TEXT NOT NULL,report_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(run_id,candidate_index,kind,review_archive_sha256));
                CREATE TABLE IF NOT EXISTS m3_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,
                  event_key TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,UNIQUE(run_id,event_key));
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.db_path,timeout=12,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=12000')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
        finally:
            db.close()

    def _source_path(self,digest):
        if len(digest)!=64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('Invalid immutable M3 source digest')
        return self.blob_dir/(digest+'.zip')

    def _store_blob(self,raw,digest):
        path=self._source_path(digest)
        if not path.exists():
            flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY
            try:
                handle=os.open(path,flags,0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(handle,'wb') as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
        if sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Content-addressed M3 source file was modified')

    def load_blob(self,run):
        raw=self._source_path(run['bundleSha256']).read_bytes()
        if sha256(raw).hexdigest()!=run['bundleSha256']:
            raise ValueError('Stored M3 source archive bytes differ from frozen hash')
        study,audit=read_physical_bundle(raw)
        if audit['studySha256']!=run['studySha256']:
            raise ValueError('M3 physical study JSON hash changed')
        return study,audit

    def create(self,raw:bytes,actor:str,client_request_id:str|None=None,*,optimizer=True):
        study,audit=read_physical_bundle(raw)
        cands=physical_candidates(study,optimizer)
        if not cands:
            raise ValueError('Unsupported M3 plan geometry; no physical candidate')
        digest=audit['archiveSha256'];created=now();run_id=uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if client_request_id:
                prior=db.execute('SELECT * FROM m3_runs WHERE actor=? AND client_request_id=?',
                                 (actor,client_request_id)).fetchone()
                if prior:
                    if prior['bundle_sha256']!=digest or bool(prior['optimizer'])!=optimizer:
                        db.execute('ROLLBACK')
                        raise ConflictError('Idempotency request conflicts with prior M3 study or mode')
                    db.execute('COMMIT')
                    return self._run(prior)
            self._store_blob(raw,digest)
            db.execute('''INSERT INTO m3_runs
                (id,actor,project_id,bundle_sha256,study_sha256,status,next_index,
                 max_evaluations,max_runtime_seconds,optimizer,client_request_id,created_at,updated_at)
                 VALUES (?,?,?,?,?,'queued',0,?,?,?,?,?,?)''',
                (run_id,actor,study.frame.topology.project_id,digest,audit['studySha256'],
                 study.frame.max_evaluations,study.frame.max_runtime_seconds,int(optimizer),
                 client_request_id,created,created))
            self._event(db,run_id,'created','study_submitted',{'sourceArchiveSha256':digest,
                            'candidateCount':len(cands),'engineeringQualified':False})
            db.execute('COMMIT')
        return self.get(run_id)

    @staticmethod
    def _run(row):
        if row is None:raise KeyError('M3 Agent run not found')
        r=dict(row)
        rename={'project_id':'projectId','bundle_sha256':'bundleSha256','study_sha256':'studySha256',
          'next_index':'nextIndex','max_evaluations':'maxEvaluations',
          'max_runtime_seconds':'maxRuntimeSeconds','elapsed_compute_seconds':'elapsedComputeSeconds',
          'cancel_requested':'cancelRequested','pause_requested':'pauseRequested',
          'lease_owner':'leaseOwner','lease_until':'leaseUntil','client_request_id':'clientRequestId',
          'created_at':'createdAt','updated_at':'updatedAt','report_json':'report'}
        out={rename.get(k,k):v for k,v in r.items()}
        out['optimizer']=bool(out['optimizer']);out['cancelRequested']=bool(out['cancelRequested']);out['pauseRequested']=bool(out['pauseRequested'])
        if out['report'] is not None:out['report']=json.loads(out['report'])
        out['formalConstructionIssue']=False
        return out

    @staticmethod
    def _event(db,run_id,key,kind,payload):
        db.execute('''INSERT OR IGNORE INTO m3_events(run_id,event_key,event_type,payload_json,created_at)
                      VALUES(?,?,?,?,?)''',(run_id,key,kind,encode(payload),now()))

    def get(self,run_id):
        with self.connection() as db:
            return self._run(db.execute('SELECT * FROM m3_runs WHERE id=?',(run_id,)).fetchone())

    def list(self,project_id:str,limit=30):
        with self.connection() as db:
            return [self._run(row) for row in db.execute('SELECT * FROM m3_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?',
                        (project_id,min(limit,100))).fetchall()]

    def events(self,run_id):
        with self.connection() as db:
            rows=db.execute('SELECT * FROM m3_events WHERE run_id=? ORDER BY id LIMIT 300',(run_id,)).fetchall()
            return [{'id':row['id'],'type':row['event_type'],'key':row['event_key'],
                     'payload':json.loads(row['payload_json']),'createdAt':row['created_at']} for row in rows]

    def candidates(self,run_id):
        with self.connection() as db:
            rows=db.execute('SELECT * FROM m3_candidate_results WHERE run_id=? ORDER BY candidate_index',(run_id,)).fetchall()
            return [{'candidateIndex':row['candidate_index'],'candidateId':row['candidate_id'],
                     'result':json.loads(row['result_json']),'resultSha256':row['result_sha256'],
                     'elapsedSeconds':row['elapsed_seconds']} for row in rows]

    def claim(self,owner:str,run_id:str|None=None,lease_seconds=600):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            clause="status IN ('queued','running') AND cancel_requested=0 AND pause_requested=0 AND (lease_until IS NULL OR lease_until<?)"
            params=[time()]
            if run_id:
                clause+=' AND id=?';params.append(run_id)
            row=db.execute('SELECT id FROM m3_runs WHERE '+clause+' ORDER BY created_at LIMIT 1',tuple(params)).fetchone()
            if row is None:
                db.execute('COMMIT');return None
            db.execute('UPDATE m3_runs SET status=?,lease_owner=?,lease_until=?,attempt=attempt+1,updated_at=?,revision=revision+1 WHERE id=?',
                       ('running',owner,time()+lease_seconds,now(),row['id']))
            db.execute('COMMIT')
            return self.get(row['id'])

    def heartbeat(self,run_id,owner,attempt,lease_seconds=600):
        with self.connection() as db:
            cursor=db.execute('UPDATE m3_runs SET lease_until=? WHERE id=? AND lease_owner=? AND attempt=? AND status=?',
                (time()+lease_seconds,run_id,owner,attempt,'running'))
            return cursor.rowcount==1

    def commit_candidate(self,run,owner,row,seconds,*,done=False,reason=None):
        run_id=run['id'];index=run['nextIndex'];attempt=run['attempt']
        blob=encode(row);checksum=sha256(blob.encode('utf-8')).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM m3_runs WHERE id=?',(run_id,)).fetchone()
            if (current is None or current['lease_owner']!=owner or current['attempt']!=attempt or
                current['next_index']!=index or current['status']!='running'):
                db.execute('ROLLBACK');raise ConflictError('M3 worker lease or cursor was lost before commit')
            db.execute('''INSERT OR IGNORE INTO m3_candidate_results
                (run_id,candidate_index,candidate_id,result_json,result_sha256,elapsed_seconds,created_at)
                 VALUES (?,?,?,?,?,?,?)''',
                (run_id,index,row['candidateId'],blob,checksum,seconds,now()))
            next_index=index+1
            elapsed=current['elapsed_compute_seconds']+seconds
            if current['cancel_requested']:
                status='cancelled'
            elif current['pause_requested']:
                status='paused'
            elif done or elapsed>=current['max_runtime_seconds']:
                # The reporting step is itself durable and may be resumed after
                # process termination. Do not claim completion before it commits.
                status='queued'
            else:
                status='queued'
            db.execute('''UPDATE m3_runs SET next_index=?,elapsed_compute_seconds=?,status=?,
                lease_owner=NULL,lease_until=NULL,updated_at=?,revision=revision+1 WHERE id=?''',
                (next_index,elapsed,status,now(),run_id))
            self._event(db,run_id,'candidate-'+str(index),'candidate_completed',
                {'candidateId':row['candidateId'],'status':row['status'],'candidateIndex':index})
            db.execute('COMMIT')
        return self.get(run_id)

    def finalize(self,run,owner,reason):
        rows=self.candidates(run['id'])
        study,audit=self.load_blob(run)
        report=finalize_physical_results(study,[x['result'] for x in rows],run['optimizer'],
                    run['elapsedComputeSeconds'],reason,audit)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM m3_runs WHERE id=?',(run['id'],)).fetchone()
            if current is None or current['lease_owner']!=owner or current['attempt']!=run['attempt']:
                db.execute('ROLLBACK');raise ConflictError('Lost M3 worker lease during finalization')
            db.execute('''UPDATE m3_runs SET status=?,report_json=?,lease_owner=NULL,lease_until=NULL,
                updated_at=?,revision=revision+1 WHERE id=?''',
                ('awaiting_engineering_review',encode(report),now(),run['id']))
            self._event(db,run['id'],'report','pareto_report_ready',{'candidateCount':len(rows),'stopReason':reason})
            db.execute('COMMIT')
        return self.get(run['id'])

    def review_summary(self,run_id):
        run=self.get(run_id)
        if run['report'] is not None:
            return run['report']
        if run['status']!='awaiting_engineering_review':
            return {'status':'in_progress','candidates':len(self.candidates(run_id)),
                    'formalConstructionIssue':False}
        study,audit=self.load_blob(run)
        report=finalize_physical_results(study,[x['result'] for x in self.candidates(run_id)],
            run['optimizer'],run['elapsedComputeSeconds'],'candidate_or_time_budget_reached',audit)
        return report

    def record_node_review(self,run_id,candidate_index,reviewer,raw_independent):
        from app.services.m3_m7_node_bridge import compare_m7_node_actions
        run=self.get(run_id)
        study,audit=self.load_blob(run)
        row=next((x for x in self.candidates(run_id) if x['candidateIndex']==candidate_index),None)
        if row is None:
            raise ValueError('Candidate missing in frozen M3 run')
        if row['result']['status']!='nonlinear_contact_reference_solved_only':
            raise ValueError('Unsolved M3 candidate cannot support M7 node comparison')
        report=compare_m7_node_actions(row['result'],row['resultSha256'],run['bundleSha256'],
                              study.frame.topology.project_id,raw_independent)
        source_digest=report['independentSourceSha256']
        path=self.blob_dir/(source_digest+'.json')
        if not path.exists():
            try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:pass
            else:
                with os.fdopen(fd,'wb') as f:
                    f.write(raw_independent);f.flush();os.fsync(f.fileno())
        if sha256(path.read_bytes()).hexdigest()!=source_digest:
            raise ValueError('M7 independent evidence was modified on disk')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT OR IGNORE INTO m3_node_reviews
                (run_id,candidate_index,reviewer,independent_sha256,result_json,created_at)
                VALUES(?,?,?,?,?,?)''',
                (run_id,candidate_index,reviewer,source_digest,encode(report),now()))
            self._event(db,run_id,'m7-'+str(candidate_index)+'-'+source_digest,'m7_node_action_review',
                {'candidateIndex':candidate_index,'status':report['status'],'reviewer':reviewer})
            db.execute('COMMIT')
        return report

    def node_reviews(self,run_id):
        with self.connection() as db:
            rows=db.execute('SELECT * FROM m3_node_reviews WHERE run_id=? ORDER BY created_at',(run_id,)).fetchall()
            return [{'candidateIndex':x['candidate_index'],'reviewer':x['reviewer'],
                     'independentSourceSha256':x['independent_sha256'],
                     'review':json.loads(x['result_json']),'createdAt':x['created_at']} for x in rows]

    def change_state(self,run_id,action):
        if action not in ('pause','resume','cancel'):
            raise ValueError('Unrecognized spatial Agent task action')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM m3_runs WHERE id=?',(run_id,)).fetchone()
            if row is None:
                db.execute('ROLLBACK');raise KeyError('M3 run not found')
            if row['status'] in ('awaiting_engineering_review','cancelled','failed'):
                db.execute('ROLLBACK');raise ConflictError('Terminal M3 study cannot be changed')
            if action=='resume':
                if row['status']!='paused':
                    db.execute('ROLLBACK');raise ConflictError('Only paused M3 run may resume')
                db.execute('''UPDATE m3_runs SET pause_requested=0,status='queued',updated_at=? WHERE id=?''',(now(),run_id))
            elif action=='pause':
                status='paused' if row['status']=='queued' else row['status']
                db.execute('UPDATE m3_runs SET pause_requested=1,status=?,updated_at=? WHERE id=?',(status,now(),run_id))
            else:
                status='cancelled' if row['status'] in ('queued','paused') else row['status']
                db.execute('UPDATE m3_runs SET cancel_requested=1,status=?,updated_at=? WHERE id=?',(status,now(),run_id))
            self._event(db,run_id,'control-'+uuid4().hex,action+'_requested',{'actorAction':action})
            db.execute('COMMIT')
        return self.get(run_id)

    def fail(self,run,owner,error):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id FROM m3_runs WHERE id=? AND lease_owner=? AND attempt=?',
                           (run['id'],owner,run['attempt'])).fetchone()
            if row:
                db.execute('UPDATE m3_runs SET status=?,error=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?',
                    ('failed',str(error)[:800],now(),run['id']))
                self._event(db,run['id'],'failed','error',{'error':str(error)[:250]})
            db.execute('COMMIT')


    def record_method_review(self, run_id, candidate_index, reviewer, raw_archive, kind):
        # Append-only, hash-linked independent method review; no adoption writes.
        if kind not in ('normative', 'component'):
            raise ValueError('Unknown review method category')
        from app.services.m3_code_review import compare_normative_pressure
        from app.services.m3_member_review import screen_component_capacity
        from pydantic import ValidationError
        run=self.get(run_id)
        matching=[r for r in self.candidates(run_id) if r['candidateIndex']==candidate_index]
        if not matching:
            raise KeyError('Frozen candidate is missing')
        row=matching[0]
        if row['result'].get('status')!='nonlinear_contact_reference_solved_only':
            raise ValueError('Cannot review a candidate without completed immutable calculation')
        study,audit=self.load_blob(run)
        func=compare_normative_pressure if kind=='normative' else screen_component_capacity
        report=func(study,row['result'],raw_archive,row['resultSha256'],run['bundleSha256'])
        digest=sha256(raw_archive).hexdigest()
        path=self.blob_dir/(digest+'.review.zip')
        if not path.exists():
            try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:pass
            else:
                with os.fdopen(fd,'wb') as f:
                    f.write(raw_archive);f.flush();os.fsync(f.fileno())
        if sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Review archive on disk has changed')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT OR IGNORE INTO m3_method_reviews
                (run_id,candidate_index,kind,reviewer,review_archive_sha256,report_json,created_at)
                VALUES(?,?,?,?,?,?,?)''',
                (run_id,candidate_index,kind,reviewer,digest,encode(report),now()))
            self._event(db,run_id,'method-'+kind+'-'+str(candidate_index)+'-'+digest,
                'm3_'+kind+'_method_review',{'candidateIndex':candidate_index,
                  'reviewer':reviewer,'status':report['status'],'reviewArchiveSha256':digest})
            db.execute('COMMIT')
        return report

    def method_reviews(self, run_id, candidate_index=None, kind=None):
        sql='SELECT * FROM m3_method_reviews WHERE run_id=?'
        args=[run_id]
        if candidate_index is not None:
            sql+=' AND candidate_index=?';args.append(candidate_index)
        if kind is not None:
            sql+=' AND kind=?';args.append(kind)
        sql+=' ORDER BY created_at,kind'
        with self.connection() as db:
            items=db.execute(sql,args).fetchall()
        return [{'candidateIndex':r['candidate_index'],'kind':r['kind'],
                 'reviewer':r['reviewer'],'archiveSha256':r['review_archive_sha256'],
                 'createdAt':r['created_at'],'report':json.loads(r['report_json'])}
                for r in items]


class SpatialAgentRuntime:
    def __init__(self,store=None):
        self.store=store or SpatialAgentStore()

    def process_one(self,run_id=None):
        owner='m3-'+uuid4().hex
        run=self.store.claim(owner,run_id)
        if run is None:return None
        try:
            study,audit=self.store.load_blob(run)
            candidates=physical_candidates(study,run['optimizer'])
            if run['nextIndex']>=len(candidates) or run['nextIndex']>=run['maxEvaluations']:
                return self.store.finalize(run,owner,'candidate_budget_reached')
            if run['elapsedComputeSeconds']>=run['maxRuntimeSeconds']:
                return self.store.finalize(run,owner,'time_budget_reached_before_next_candidate')
            cand=candidates[run['nextIndex']]
            start=perf_counter()
            try:
                result=objective_metrics(solve_physical_candidate(study,candidates,cand),study.frame)
            except (ValueError,ArithmeticError) as exc:
                result={'candidateId':cand['candidateId'],'status':'blocked','reason':str(exc),
                    'engineeringQualified':False,'formalConstructionIssue':False}
            elapsed=perf_counter()-start
            return self.store.commit_candidate(run,owner,result,elapsed,
                        done=run['nextIndex']+1>=len(candidates),reason='candidate_budget_reached')
        except Exception as exc:
            self.store.fail(run,owner,exc)
            raise
