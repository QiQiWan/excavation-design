"""V3.88.7: byte-backed, fail-closed real field-case engineering receipt.

This verifies archive integrity, provenance bindings and node ACTION arithmetic.
It does not attest the author of a document, approve normative methods, or
qualify the engineering design. Project engineers must independently review
original signed drawings, design assumptions and actual node capacities.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from math import isfinite, sqrt
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile
import json
import re
from typing import Literal
from pydantic import Field, model_validator
from ..assurance.model import Strict
from .deep_node_review import DeepReviewContract, run_deep_review

MAX_ARCHIVE_BYTES = 32_000_000
MAX_TOTAL_BYTES = 80_000_000
MAX_FILE_COUNT = 40
HASH = r"^[a-f0-9]{64}$"
NEEDED_LUOYU_ROLES = (
    'original_design_report', 'original_construction_drawing',
    'geotechnical_report', 'original_numeric_export',
    'connection_detail', 'independent_calculation', 'method_qualification'
)

class DocumentRecord(Strict):
    path: str = Field(min_length=12, max_length=240)
    kind: Literal['original_design_report','original_construction_drawing',
        'geotechnical_report','original_numeric_export','connection_detail',
        'independent_calculation','method_qualification','node_action_report',
        'node_capacity_report','inspection_report',
        'water_level_observation','pumping_test','monitoring_record']
    sha256: str = Field(pattern=HASH)
    source_locator: str = Field(min_length=4,max_length=400)
    revision: str = Field(min_length=1,max_length=80)

class MemberEndAction(Strict):
    member_id: str = Field(min_length=1,max_length=100)
    source_sha256: str = Field(pattern=HASH)
    point_m: tuple[float,float,float]
    force_kn: tuple[float,float,float]
    moment_knm: tuple[float,float,float]
    sign_basis: Literal['global_xyz_action_on_node']

class CapacityCheck(Strict):
    member_id: str = Field(min_length=1,max_length=100)
    component: Literal['Fx','Fy','Fz','Mx','My','Mz']
    resistance: float = Field(gt=0,le=1e12)
    source_sha256: str = Field(pattern=HASH)
    method_reference: str = Field(min_length=12,max_length=800)

class NodeCase(Strict):
    node_id: str = Field(min_length=1,max_length=100)
    stage_id: str = Field(min_length=1,max_length=80)
    combination_name: str = Field(min_length=2,max_length=100)
    combination_kind: Literal['ULS_basic','construction_adverse']
    support_id: str | None = Field(default=None,min_length=1,max_length=80)
    source_reaction_kn_per_pile: float | None = None
    reference_point_m: tuple[float,float,float]
    member_actions: tuple[MemberEndAction,...] = Field(min_length=2,max_length=32)
    capacity_checks: tuple[CapacityCheck,...] = Field(default=(),max_length=128)
    force_tolerance_kn: float = Field(gt=0,le=100)
    moment_tolerance_knm: float = Field(gt=0,le=100)
    @model_validator(mode='after')
    def unique_members(self):
        if (self.support_id is None) != (self.source_reaction_kn_per_pile is None):
            raise ValueError('Frozen support ID and source reaction must be provided together')
        ids=[x.member_id for x in self.member_actions]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate member end at same node; aggregate physical actions first')
        ids_cap=[(x.member_id,x.component) for x in self.capacity_checks]
        if len(ids_cap)!=len(set(ids_cap)):
            raise ValueError('Duplicate capacity check for same member/component')
        if any(x.member_id not in ids for x in self.capacity_checks):
            raise ValueError('Capacity references member absent in node action list')
        return self

class FieldCase(Strict):
    schema_version: Literal['pitguard.field-case/3.88.7'] = Field(alias='schemaVersion')
    project_id: str = Field(alias='projectId',min_length=2,max_length=160)
    section_id: str = Field(alias='sectionId',min_length=1,max_length=80)
    revision: str = Field(min_length=2,max_length=90)
    result_hash: str | None = Field(default=None,alias='resultHash',pattern=HASH)
    documents: tuple[DocumentRecord,...] = Field(default=(),max_length=39)
    node_cases: tuple[NodeCase,...] = Field(default=(),alias='nodeCases',max_length=100)
    engineering_review: DeepReviewContract | None = Field(default=None,alias='engineeringReview')
    @model_validator(mode='after')
    def uniqueness(self):
        paths=[d.path for d in self.documents]
        if len(paths)!=len(set(paths)):
            raise ValueError('Duplicate document path')
        cases=[(c.node_id,c.stage_id,c.combination_name) for c in self.node_cases]
        if len(cases)!=len(set(cases)):
            raise ValueError('Duplicate node stage/combination')
        return self


def _safe_document_path(path:str)->bool:
    if not isinstance(path,str) or chr(92) in path or not path.startswith('documents/'):
        return False
    if any(ord(x)<32 for x in path) or path.startswith('/') or ':' in path:
        return False
    parts=PurePosixPath(path).parts
    return len(parts)>=2 and all(x not in ('','.','..') for x in parts) and path==str(PurePosixPath(path))


def _load_json(data:bytes):
    if len(data)>2_000_000:
        raise ValueError('case.json exceeds 2 MB')
    def unique(kvs):
        out={}
        for k,v in kvs:
            if k in out: raise ValueError('Duplicate JSON key: '+k)
            out[k]=v
        return out
    return json.loads(data.decode('utf-8-sig'),object_pairs_hook=unique,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON: '+x)))


def _cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def audit_node_case(node:NodeCase,source_hashes:set[str], *, frozen_result:dict|None=None,
                    source_roles:dict[str,set[str]]|None=None)->dict:
    force=[0.,0.,0.];moment=[0.,0.,0.];r0=node.reference_point_m
    members={x.member_id:x for x in node.member_actions}
    needed=[]
    for end in node.member_actions:
        if end.source_sha256 not in source_hashes:
            needed.append('source_document:'+end.member_id)
        elif source_roles is not None and not ({'node_action_report','original_numeric_export','independent_calculation'} & source_roles.get(end.source_sha256,set())):
            needed.append('node_action_source_role:'+end.member_id)
        offset=tuple(end.point_m[i]-r0[i] for i in range(3))
        couple=_cross(offset,end.force_kn)
        for i in range(3):
            force[i]+=end.force_kn[i]
            moment[i]+=end.moment_knm[i]+couple[i]
    fmag=sqrt(sum(x*x for x in force));mmag=sqrt(sum(x*x for x in moment))
    balanced=fmag<=node.force_tolerance_kn and mmag<=node.moment_tolerance_knm
    indexed={'Fx':0,'Fy':1,'Fz':2,'Mx':0,'My':1,'Mz':2}
    capacity=[]
    for c in node.capacity_checks:
        member=members[c.member_id]
        actual=member.force_kn[indexed[c.component]] if c.component.startswith('F') else member.moment_knm[indexed[c.component]]
        # Component-by-component comparison; interaction equations, eccentricity,
        # anchor groups and local bearing require separately qualified methods.
        ratio=abs(actual)/c.resistance
        source_ok=c.source_sha256 in source_hashes
        capacity.append({'memberId':c.member_id,'component':c.component,
            'demand':abs(actual),'declaredResistance':c.resistance,
            'utilization':ratio,'sourceDocumentBytesVerified':source_ok,
            'methodReference':c.method_reference,
            'status':'missing_input' if not source_ok else 'fail' if ratio>1 else 'screening_only'})
        if not source_ok:needed.append('capacity_source_document:'+c.member_id)
        elif source_roles is not None and not ({'node_capacity_report','independent_calculation'} & source_roles.get(c.source_sha256,set())):
            needed.append('node_capacity_source_role:'+c.member_id)
    if not capacity:needed.append('independent_node_capacity_calculations')
    frozen_matched=False
    if frozen_result is not None:
        matches=[x for x in frozen_result.get('actionCombinations',{}).get('explicit',{}).get('cases',[])
                 if x.get('name')==node.combination_name and x.get('kind')==node.combination_kind
                 and x.get('status')=='calculated']
        if node.stage_id not in {x['id'] for x in frozen_result.get('stages',[])} or len(matches)!=1:
            needed.append('frozen_result_stage_and_ULS_combination_match')
        elif node.support_id is None:
            needed.append('frozen_support_reaction_binding')
        else:
            stage=next(x for x in frozen_result['stages'] if x['id']==node.stage_id)
            original=[x for x in stage.get('strut',[]) if x.get('id')==node.support_id]
            if len(original)!=1:
                needed.append('frozen_support_id_not_found_in_stage')
            elif abs(original[0]['reactionKn']-node.source_reaction_kn_per_pile)>max(0.001,abs(original[0]['reactionKn'])*1e-8):
                needed.append('frozen_stage_reaction_value_mismatch')
            else:
                frozen_matched=True
    else:needed.append('frozen_stage_result_and_compatible_action_combination')
    failure=not balanced or any(x['status']=='fail' for x in capacity)
    return {'nodeId':node.node_id,'stageId':node.stage_id,'combinationName':node.combination_name,
        'equilibrium':{'forceResidualKn':[round(x,8) for x in force],
            'momentResidualKnM':[round(x,8) for x in moment],
            'forceResidualNormKn':round(fmag,8),'momentResidualNormKnM':round(mmag,8),
            'forceToleranceKn':node.force_tolerance_kn,'momentToleranceKnM':node.moment_tolerance_knm,
            'status':'pass' if balanced else 'fail'},'capacityScreens':capacity,
        'frozenStageCombinationMatched':frozen_matched,
        'requiredInputs':sorted(set(needed)),
        'status':'fail' if failure else 'missing_input' if needed else 'calculated_scope_limited',
        'fullNodeStrengthQualified':False,'formalConstructionIssue':False}


def intake_field_case(blob:bytes, *, frozen_result:dict|None=None)->dict:
    if not isinstance(blob,bytes) or len(blob)>MAX_ARCHIVE_BYTES:
        raise ValueError('Field-case archive exceeds maximum compressed size')
    try:
        with ZipFile(BytesIO(blob)) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if not 1<=len(infos)<=MAX_FILE_COUNT or len(set(names))!=len(names):
                raise ValueError('Invalid ZIP entry count or duplicate names')
            if names.count('case.json')!=1 or any(n!='case.json' and not _safe_document_path(n) for n in names):
                raise ValueError('ZIP must contain only case.json and documents/<filename> files')
            total=sum(i.file_size for i in infos)
            if total>MAX_TOTAL_BYTES:raise ValueError('Uncompressed ZIP byte budget exceeded')
            for item in infos:
                if item.is_dir() or item.file_size>24_000_000 or (item.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('Directory, symlink or oversized ZIP entry')
                if item.compress_size and item.file_size/item.compress_size>500:
                    raise ValueError('Suspicious ZIP compression ratio')
            case=FieldCase.model_validate(_load_json(z.read('case.json')))
            raw_paths={d.path for d in case.documents}
            if raw_paths!=set(names)-{'case.json'}:
                raise ValueError('Document entries must match document manifest exactly')
            hashes={}
            file_blobs={}
            for d in case.documents:
                digest=sha256(z.read(d.path)).hexdigest()
                if digest!=d.sha256:
                    raise ValueError('Document SHA-256 mismatch: '+d.path)
                hashes[d.path]=digest
                file_blobs[digest]=z.read(d.path)
            original_numeric=[]
            for d in case.documents:
                if d.kind=='original_numeric_export':
                    from .native_numeric_import import import_native_numeric
                    raw=import_native_numeric(file_blobs[d.sha256])
                    if raw['projectId']!=case.project_id or raw['sectionId']!=case.section_id:
                        raise ValueError('Original numeric export has wrong project/section identity')
                    original_numeric.append({'archiveSha256':raw['archiveSha256'],
                        'assessmentStatus':raw['assessmentStatus'],'stationCounts':raw['stationCounts']})
    except BadZipFile as exc:
        raise ValueError('Invalid field-case ZIP') from exc
    if frozen_result is not None and (case.result_hash!=frozen_result.get('resultHash') or
         case.project_id!=frozen_result.get('projectId') or
         case.section_id!=frozen_result.get('sectionId')):
        raise ValueError('Evidence must match frozen result SHA and project/section')
    needed_roles=NEEDED_LUOYU_ROLES if case.section_id=='3-3' and '螺屿' in case.project_id else (
         'original_design_report','original_construction_drawing',
         'connection_detail','independent_calculation','method_qualification')
    have={d.kind for d in case.documents}
    missing_roles=sorted(set(needed_roles)-have)
    actual_hashes=set(hashes.values())
    source_roles:dict[str,set[str]]={}
    for d in case.documents:
        source_roles.setdefault(d.sha256,set()).add(d.kind)
    nodes=[audit_node_case(x,actual_hashes,frozen_result=frozen_result,
                           source_roles=source_roles) for x in case.node_cases]
    deep_review=(run_deep_review(case.engineering_review,file_blobs,source_roles,
                 case.project_id,case.section_id,list(case.node_cases))
                 if case.engineering_review is not None else None)
    if not case.node_cases:missing_roles.append('reviewed_node_action_and_resistance_cases')
    failures=any(x['status']=='fail' for x in nodes)
    pending=bool(missing_roles or any(x['status']=='missing_input' for x in nodes) or
                 deep_review is None or deep_review['status'] in ('missing_input','fail'))
    if deep_review is not None and deep_review['status']=='fail':failures=True
    return {'schemaVersion':'pitguard.field-case-receipt/3.88.7',
        'status':'fail' if failures else 'missing_input' if pending else 'calculated_scope_limited',
        'archiveSha256':sha256(blob).hexdigest(),
        'projectId':case.project_id,'sectionId':case.section_id,'resultHash':case.result_hash,
        'receivedFileCount':len(hashes),'verifiedFileDigests':hashes,
        'bytesIntegrityVerified':True,'documentAuthorshipAuthenticated':False,
        'originalNumericChannels':original_numeric,'missingEvidenceRoles':sorted(set(missing_roles)),
        'nodeReviews':nodes,'deepEngineeringReview':deep_review,'engineeringQualified':False,'formalDesignReady':False,
        'formalConstructionIssue':False,
        'limitations':['File digest verifies uploaded bytes only, not the design-office origin or approval',
          'Node equilibrium + single-component capacity screens cannot certify concrete node interaction, anchors, welds or local bearing',
          'Original staged numeric arrays and signed node checks are mandatory for Luoyu 3-3 acceptance']}


def blank_case_template()->dict:
    """A deliberate BLOCKED blank template; never populate original results."""
    from ..normative.reference_review import luoyu_three_three
    r=luoyu_three_three()
    return {'schemaVersion':'pitguard.field-case/3.88.7',
        'projectId':r['project'],'sectionId':'3-3',
        'revision':'TO_BE_SUPPLIED_BY_DESIGN_OFFICE','resultHash':None,
        'documents':[],'nodeCases':[],'engineeringReview':None}
