"""One immutable calculation/result identity and three independent review tracks."""
from __future__ import annotations
from pydantic import Field,model_validator
from .model import Strict
from .hydraulic import HydraulicAuditInput,audit_hydraulic
from .circular import CircularAuditInput,audit_circular
from .luoyu import LuoyuAuditInput,audit_luoyu


class UnifiedAuditInput(Strict):
    result_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    circular:CircularAuditInput
    hydraulic:HydraulicAuditInput|None=None
    luoyu:LuoyuAuditInput|None=None
    @model_validator(mode='after')
    def aligned(self):
        if self.circular.result_hash!=self.result_hash:
            raise ValueError('One calculation result required across all audits')
        if self.hydraulic and (self.hydraulic.project_id,self.hydraulic.section_id)!=\
                (self.circular.project_id,self.circular.section_id):
            raise ValueError('Hydraulic audit must use the same project/section')
        if self.luoyu and (self.luoyu.evidence.project_id,self.luoyu.evidence.section_id)!=\
                (self.circular.project_id,self.circular.section_id):
            raise ValueError('Luoyu audit must use the same project/section')
        return self


def audit_unified(body:UnifiedAuditInput,result:dict,*,independent:bool=True)->dict:
    circular=audit_circular(body.circular,result)
    hydro=audit_hydraulic(body.hydraulic,independent=independent) if body.hydraulic else None
    project=audit_luoyu(body.luoyu) if body.luoyu else None
    checks={'circular':circular['assessmentStatus'],
         'hydraulic':hydro['assessmentStatus'] if hydro else 'not_submitted',
         'realProject':project['assessmentStatus'] if project else 'not_requested'}
    severe={'failed_check','fail_or_numerical_invalid'}
    status='engineering_fail' if any(x in severe for x in checks.values()) else 'review_incomplete'
    recommended=[]
    for name,status_one in checks.items():
        if status_one not in ('not_requested',):
            recommended.append({'domain':name,'status':status_one,'blocksFormalIssue':True})
    if not hydro:
        recommended.append({'domain':'hydraulic','status':'not_submitted','blocksFormalIssue':True})
    return {'schemaVersion':'pitguard.engineering-acceptance/1',
       'projectId':result['projectId'],'sectionId':result['sectionId'],
       'snapshotHash':result['snapshotHash'],'resultHash':result['resultHash'],
       'analysisStatus':result.get('analysisStatus'),
       'overall':status,'issueGate':{'formalConstructionIssue':False,
            'canEngineerReviewWorkingCopy':result.get('analysisStatus')=='complete',
            'reason':'This workflow cannot certify site-specific geotechnical design, structural provisions or drawings.'},
       'domainStatuses':checks,'blockingItems':recommended,
       'hydraulic':hydro,'circular':circular,'realProject':project}
