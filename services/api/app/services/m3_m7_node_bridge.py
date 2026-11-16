"""M3 model-end actions vs separately supplied M7 node-action data.

The tool compares numerical evidence from two supplied byte streams and does
not certify that either the original engineering methods or reviewers are real.
"""
from __future__ import annotations
from hashlib import sha256
from math import sqrt
from typing import Literal
import json

from pydantic import Field, model_validator
from app.engineering.single_braced_pile.assurance.model import Strict
from app.services.m3_physical_evidence import _read_json

HEX=r'^[0-9a-f]{64}$'


class M7IndependentAction(Strict):
    stage_id: str = Field(min_length=2,max_length=80)
    member_id: str = Field(min_length=2,max_length=120)
    node_id: int = Field(ge=0,le=320)
    point_m: tuple[float,float,float]
    force_kn: tuple[float,float,float]
    moment_knm: tuple[float,float,float]


class M7IndependentNodeReport(Strict):
    schema_version: Literal['pitguard.m7-independent-node-actions/1'] = Field(alias='schemaVersion')
    project_id: str = Field(min_length=2,max_length=100)
    source_archive_sha256: str = Field(pattern=HEX)
    candidate_result_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=16,max_length=400)
    revision: str = Field(min_length=2,max_length=100)
    force_tolerance_kn: float = Field(ge=0,le=1000)
    moment_tolerance_knm: float = Field(ge=0,le=1000)
    node_actions: tuple[M7IndependentAction,...] = Field(min_length=1,max_length=1800)

    @model_validator(mode='after')
    def unique_actions(self):
        keys=[(a.stage_id,a.member_id,a.node_id) for a in self.node_actions]
        if len(keys)!=len(set(keys)):
            raise ValueError('Duplicate independent node action identity')
        return self


def compare_m7_node_actions(candidate:dict,candidate_sha256:str,source_archive_sha256:str,
                            project_id:str,raw_independent:bytes):
    if len(raw_independent)>2_000_000:
        raise ValueError('Independent M7 node action source exceeds 2 MB')
    evidence=M7IndependentNodeReport.model_validate(_read_json(raw_independent))
    if evidence.project_id!=project_id or evidence.source_archive_sha256!=source_archive_sha256:
        raise ValueError('Independent node evidence project or study source hash mismatched')
    if evidence.candidate_result_sha256!=candidate_sha256:
        raise ValueError('Independent node action evidence must match frozen candidate result bytes')
    original={}
    for stage in candidate['stages']:
        for action in stage['braceNodeActions']:
            key=(action['stageId'],action['memberId'],action['nodeId'])
            if key in original:
                raise ValueError('Candidate has duplicate node/member/stage identity')
            original[key]=action
    comparison=[];used=set();failed=False
    for action in evidence.node_actions:
        key=(action.stage_id,action.member_id,action.node_id)
        computed=original.get(key)
        if computed is None:
            raise ValueError('Independent node evidence references nonexistent model member, node or stage')
        if sqrt(sum((a-b)**2 for a,b in zip(action.point_m,computed['pointM']))) > 1e-5:
            raise ValueError('Independent action node coordinate identity mismatched')
        used.add(key)
        df=sqrt(sum((a-b)**2 for a,b in zip(action.force_kn,computed['actionOnNodeForceKn'])))
        dm=sqrt(sum((a-b)**2 for a,b in zip(action.moment_knm,computed['actionOnNodeMomentKnM'])))
        matched=df<=evidence.force_tolerance_kn+1e-10 and dm<=evidence.moment_tolerance_knm+1e-10
        failed=failed or not matched
        comparison.append({'stageId':key[0],'memberId':key[1],'nodeId':key[2],
            'forceVectorDifferenceKn':round(df,8),'momentVectorDifferenceKnM':round(dm,8),
            'status':'numeric_match_only' if matched else 'numeric_difference_exceeds_tolerance'})
    missing=sorted(set(original)-used)
    status='numeric_difference' if failed else 'missing_input' if missing else 'numeric_match_only'
    return {'schemaVersion':'pitguard.m3-m7-node-comparison/3.89.2',
        'candidateResultSha256':candidate_sha256,
        'independentSourceSha256':sha256(raw_independent).hexdigest(),
        'sourceArchiveSha256':source_archive_sha256,
        'projectId':project_id,'methodReference':evidence.method_reference,
        'sourceRevision':evidence.revision,'status':status,
        'matchedCount':sum(1 for r in comparison if r['status']=='numeric_match_only'),
        'failedCount':sum(1 for r in comparison if r['status']!='numeric_match_only'),
        'missingCount':len(missing),'missingNodeIds':[
            {'stageId':x[0],'memberId':x[1],'nodeId':x[2]} for x in missing],
        'entries':comparison,
        'engineeringQualified':False,'connectionCapacityQualified':False,
        'sourceAuthorIndependentlyVerified':False,
        'formalConstructionIssue':False}
