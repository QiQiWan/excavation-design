"""Atomically persisted engineering snapshots, review notes and output dependencies."""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, os, tempfile
from datetime import datetime,timezone
from .analysis import calculate,canonical_hash

class Repository:
    def __init__(self,root=None):
        self.root=Path(root or os.environ.get('PITGUARD_ONE_STRUT_STORE',
                    Path(__file__).resolve().parents[5]/'runtime'/'one_strut'))
        self.root.mkdir(parents=True,exist_ok=True)

    def _file(self,tag,identity):
        if len(identity)!=64 or any(x not in '0123456789abcdef' for x in identity):
            raise ValueError('数据身份必须是SHA256')
        return self.root/tag/(identity+'.json')

    def _write(self,path,obj):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            current=json.loads(path.read_text(encoding='utf-8'))
            if current!=obj: raise ValueError('不允许覆盖历史工程快照与记录')
            return
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as tmp:
            json.dump(obj,tmp,ensure_ascii=False,sort_keys=True,allow_nan=False)
            tmp.flush();os.fsync(tmp.fileno())
            name=tmp.name
        os.replace(name,path)

    def compute(self,input_data):
        result=calculate(input_data)
        self._write(self._file('snapshots',result['snapshotHash']),result['snapshot'])
        self._write(self._file('results',result['resultHash']),result)
        return result

    def load(self,result_hash):
        file=self._file('results',result_hash)
        result=json.loads(file.read_text(encoding='utf-8'))
        digest=result.pop('resultHash',None)
        if digest!=result_hash or canonical_hash(result)!=result_hash:
            raise ValueError('封存结果已变更或结果身份不一致')
        result['resultHash']=digest
        snapshot=json.loads(self._file('snapshots',result['snapshotHash']).read_text(encoding='utf-8'))
        if canonical_hash(snapshot)!=result['snapshotHash'] or snapshot!=result['snapshot']:
            raise ValueError('封存快照已变更，禁止导出')
        return result

    def approve_preliminary(self,result_hash,*,reviewer,notes,acknowledged_codes):
        result=self.load(result_hash)
        if result['analysisStatus']!='complete':
            raise ValueError('数值阶段或土反力合力有缺陷，不允许作为已复核成果')
        if len(reviewer.strip())<2 or len(notes.strip())<8:
            raise ValueError('复核人和明确的复核说明必须填写')
        issues={x['code'] for x in result['issues']}
        if not issues.issubset(set(acknowledged_codes)):
            raise ValueError('必须逐项确认所有尚未完成的工程审核事项，不能默认为通过')
        evidence={'schemaVersion':'pitguard.one-strut-review/1','resultHash':result_hash,
                  'reviewer':reviewer.strip(),'notes':notes.strip(),
                  'scope':'preliminary_calculation_review_only',
                  'acknowledgedIssues':sorted(issues),
                  'reviewedAt':datetime.now(timezone.utc).isoformat(),
                  'formalConstructionIssue':False}
        evidence['reviewHash']=canonical_hash(evidence)
        self._write(self._file('reviews',evidence['reviewHash']),evidence)
        return evidence

    def load_review(self,review_hash,result_hash):
        review=json.loads(self._file('reviews',review_hash).read_text(encoding='utf-8'))
        digest=review.pop('reviewHash',None)
        if digest!=review_hash or canonical_hash(review)!=review_hash or review['resultHash']!=result_hash:
            raise ValueError('复核记录被修改、丢失或不属于本结果')
        review['reviewHash']=digest
        return review


    def save_phase_review(self,result_hash,*,phase,reviewer,method_revision,notes,decision,
                          source_refs=(),independent_verification_ref=None,negative_case_ref=None):
        """Immutable, role-gated preliminary phase record, never a formal approval."""
        result=self.load(result_hash)
        if phase not in ('P1','P2','P3','P4'):
            raise ValueError('Unsupported engineering acceptance phase')
        if decision not in ('acknowledged_for_working_copy','returned_for_revision'):
            raise ValueError('Phase decision must be explicit and non-certifying')
        if len(reviewer.strip())<2 or len(method_revision.strip())<2 or len(notes.strip())<12:
            raise ValueError('Reviewer, method version and meaningful notes required')
        refs=tuple(source_refs)
        if len(refs)>16 or len(set(refs))!=len(refs) or any(not isinstance(x,str) or not 2<=len(x.strip())<=260 for x in refs):
            raise ValueError('At most 16 distinct nonempty source references required')
        for ref in (independent_verification_ref,negative_case_ref):
            if ref is not None and (not isinstance(ref,str) or not 2<=len(ref.strip())<=260):
                raise ValueError('Verification reference must identify a reviewable source')
        payload={'schemaVersion':'pitguard.phase-review/2',
            'resultHash':result_hash,'snapshotHash':result['snapshotHash'],
            'projectId':result['projectId'],'sectionId':result['sectionId'],
            'phase':phase,'decision':decision,'reviewer':reviewer.strip(),
            'methodRevision':method_revision.strip(),'notes':notes.strip(),
            # These identifiers are declarations. Their actual bytes and test
            # methods must be independently checked before any formal signoff.
            'declaredInputSourceReferences':list(refs),
            'declaredIndependentVerificationReference':independent_verification_ref,
            'declaredNegativeCaseReference':negative_case_ref,
            'sourceReferencesAuthenticated':False,
            'formalEngineeringApproval':False,
            'reviewedAt':datetime.now(timezone.utc).isoformat()}
        payload['phaseReviewHash']=canonical_hash(payload)
        self._write(self._file('phase_reviews',payload['phaseReviewHash']),payload)
        return payload

    def load_phase_review(self,identity,result_hash):
        payload=json.loads(self._file('phase_reviews',identity).read_text(encoding='utf-8'))
        digest=payload.pop('phaseReviewHash',None)
        if digest!=identity or canonical_hash(payload)!=identity or payload.get('resultHash')!=result_hash:
            raise ValueError('Engineering phase review was modified or belongs to a different result')
        payload['phaseReviewHash']=digest
        return payload
