"""V3.89.1: byte-backed five-stage independent comparison and joint detailing.

The calculation checks the numerical assertions inside TWO distinct raw source
files. Its result is limited: no certificate, method approval, concrete node
interaction or fabrication acceptance can be inferred from a matching file.
"""
from __future__ import annotations
from math import hypot,isfinite,sqrt
from hashlib import sha256
from typing import Literal
import json
import numpy as np
from pydantic import Field,model_validator
from ..assurance.model import Strict
from .native_numeric_import import import_native_numeric

HASH = r'^[a-f0-9]{64}$'
CHANNELS = ('displacementMm','momentKnM','shearKn','pressureKpa','reactionKnPerPile','referenceMm')


class StageTolerances(Strict):
    displacementMm:float = Field(ge=0,le=100)
    momentKnM:float = Field(ge=0,le=1e6)
    shearKn:float = Field(ge=0,le=1e6)
    pressureKpa:float = Field(ge=0,le=1e6)
    reactionKnPerPile:float = Field(ge=0,le=1e6)
    referenceMm:float = Field(ge=0,le=100)


class ConcreteBearing(Strict):
    area_m2: float = Field(gt=0,le=100)
    design_bearing_mpa: float = Field(gt=0,le=1000)


class BoltGroup(Strict):
    locations_uv_m: tuple[tuple[float,float],...] = Field(min_length=2,max_length=120)
    tension_capacity_kn: float = Field(gt=0,le=1e7)
    shear_capacity_kn: float = Field(gt=0,le=1e7)
    interaction: Literal['quadratic_reference','linear_reference']
    @model_validator(mode='after')
    def uniqueness(self):
        if len({(round(x,8),round(y,8)) for x,y in self.locations_uv_m})!=len(self.locations_uv_m):
            raise ValueError('Duplicate bolt coordinates')
        return self


class WeldGroup(Strict):
    effective_length_m: float = Field(gt=0,le=100)
    throat_mm: float = Field(gt=0,le=50)
    design_shear_mpa: float = Field(gt=0,le=2000)


class Anchorage(Strict):
    available_length_mm: float = Field(ge=0,le=50000)
    required_length_mm: float = Field(gt=0,le=50000)
    bar_diameter_mm: float = Field(gt=0,le=100)


class NodeDetailReview(Strict):
    node_id: str = Field(min_length=1,max_length=100)
    stage_id: str = Field(min_length=2,max_length=80)
    combination_name: str = Field(min_length=2,max_length=100)
    action_member_id: str = Field(min_length=1,max_length=100)
    connection_detail_sha256: str = Field(pattern=HASH)
    method_qualification_sha256: str = Field(pattern=HASH)
    independent_calculation_sha256: str = Field(pattern=HASH)
    plate_center_m: tuple[float,float,float]
    normal_global: tuple[float,float,float]
    tangent_global: tuple[float,float,float]
    bearing: ConcreteBearing | None = None
    bolts: BoltGroup | None = None
    weld: WeldGroup | None = None
    anchorage: Anchorage | None = None
    @model_validator(mode='after')
    def valid_basis(self):
        n=np.array(self.normal_global,dtype=float); u=np.array(self.tangent_global,dtype=float)
        if not np.all(np.isfinite(n)) or not np.all(np.isfinite(u)):
            raise ValueError('Joint local basis contains nonfinite values')
        if abs(float(n@n)-1)>1e-5 or abs(float(u@u)-1)>1e-5 or abs(float(n@u))>1e-5:
            raise ValueError('Joint local basis must be orthonormal unit vectors')
        if all(x is None for x in (self.bearing,self.bolts,self.weld,self.anchorage)):
            raise ValueError('A joint review needs an explicit connection check')
        return self


class DeepReviewContract(Strict):
    schema_version: Literal['pitguard.deep-engineering-review/1'] = Field(default='pitguard.deep-engineering-review/1',alias='schemaVersion')
    original_numeric_sha256: str = Field(pattern=HASH)
    independent_stages_sha256: str = Field(pattern=HASH)
    stage_tolerances: StageTolerances
    connection_details: tuple[NodeDetailReview,...] = Field(default=(),max_length=120)
    original_engineer_review_sha256: str | None = Field(default=None,pattern=HASH)
    reviewer_numerical_tolerance: float = Field(default=0.02,ge=0,le=10)
    @model_validator(mode='after')
    def check_pair(self):
        if self.original_numeric_sha256==self.independent_stages_sha256:
            raise ValueError('Original and independent stage data cannot be the same raw source file')
        if len({(x.node_id,x.stage_id,x.combination_name,x.action_member_id) for x in self.connection_details})!=len(self.connection_details):
            raise ValueError('Duplicate detailed node review identity')
        return self


def _strict_json(blob:bytes):
    if len(blob)>12_000_000:raise ValueError('Independent stage JSON exceeds 12 MB')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise ValueError('Duplicate independent JSON field: '+k)
            d[k]=v
        return d
    return json.loads(blob.decode('utf-8-sig'),object_pairs_hook=unique,
         parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite stage value '+x)))


def compare_independent_stage_bytes(native_blob:bytes,independent_blob:bytes,project_id:str,
                                    section_id:str,tolerances:StageTolerances)->dict:
    original=import_native_numeric(native_blob)
    reference=original['nativeReference']
    candidate=_strict_json(independent_blob)
    if (not isinstance(candidate,dict) or candidate.get('schemaVersion')!='pitguard.independent-five-stage/1'
         or candidate.get('projectId')!=project_id or candidate.get('sectionId')!=section_id
         or original['projectId']!=project_id or original['sectionId']!=section_id):
        raise ValueError('Independent stage identity, source or schema mismatch')
    stages=candidate.get('stages')
    if not isinstance(stages,list) or len(stages)!=5 or [x.get('id') for x in stages if isinstance(x,dict)]!=['S1','S2','S3','S4','S5']:
        raise ValueError('Independent model requires exactly S1-S5')
    if candidate.get('depthDatum')!='original_ground_down_m' or candidate.get('forceBasis')!='single_pile':
        raise ValueError('Independent stage datum or force basis mismatch')
    columns={'curve':('curve',('depthSiteM','side'),('displacementMm','momentKnM','shearKn')),
             'pressure':('pressureStations',('depthSiteM','side','pressureKind'),('pressureKpa',)),
             'brace':('strutReactions',('id',),('reactionKnPerPile','referenceMm'))}
    per_stage=[];all_issues=[];total=0
    for a,b in zip(stages,reference['stages']):
        sid=b['id'];stage_issues=[];stats={};overall=True
        if a.get('status')!='success' or a.get('activeSupportIds')!=b['activeSupportIds']:
            stage_issues.append('STAGE_STATUS_OR_ACTIVE_SUPPORT_SET_MISMATCH')
        for label,(field,keys,channels) in columns.items():
            originals=b[field];proposals=a.get(field)
            if not isinstance(proposals,list) or len(proposals)!=len(originals):
                stage_issues.append(label+':STATION_OR_SUPPORT_COUNT_MISMATCH');continue
            def key(row):
                if not isinstance(row,dict):raise ValueError('Non-object stage channel')
                return tuple(round(row[k],8) if k=='depthSiteM' else row[k] for k in keys)
            try:
                m1={key(r):r for r in originals};m2={key(r):r for r in proposals}
            except (TypeError,KeyError,ValueError) as exc:
                stage_issues.append(label+':INVALID_STATION_KEY');continue
            if len(m2)!=len(proposals) or set(m1)!=set(m2):
                stage_issues.append(label+':STATION_KEY_MISMATCH');continue
            for channel in channels:
                diffs=[]
                for name,ref in m1.items():
                    val=m2[name].get(channel)
                    if not isinstance(val,(int,float)) or isinstance(val,bool) or not isfinite(val):
                        stage_issues.append(label+':NONFINITE_OR_MISSING_'+channel);break
                    diffs.append(float(val)-float(ref[channel]))
                if len(diffs)!=len(m1):continue
                tol=getattr(tolerances,channel)
                peak=max(map(abs,diffs),default=0.)
                stats[channel]={'maxAbsDifference':round(peak,9),
                     'rmse':round(sqrt(sum(x*x for x in diffs)/max(1,len(diffs))),9),
                     'absoluteTolerance':tol,'matchedStations':len(diffs),
                     'status':'pass' if peak<=tol else 'fail'}
                total+=len(diffs)
                if peak>tol:stage_issues.append(channel+':TOLERANCE_EXCEEDED')
        if stage_issues:all_issues.extend(sid+':'+x for x in stage_issues)
        per_stage.append({'stageId':sid,'channelChecks':stats,
           'status':'fail' if stage_issues else 'numeric_match_only',
           'issues':stage_issues})
    return {'originalSha256':sha256(native_blob).hexdigest(),
        'independentSha256':sha256(independent_blob).hexdigest(),
        'status':'fail' if all_issues else 'numeric_match_only',
        'matchedValueCount':total,'stages':per_stage,
        'issues':all_issues,'rawBytesVerified':True,
        'methodQualificationApproved':False,'sourceAuthorshipVerified':False,
        'engineeringQualified':False,'formalConstructionIssue':False}


def _source_roles_present(spec:NodeDetailReview,roles:dict[str,set[str]])->list[str]:
    requirement={'connection_detail_sha256':{'connection_detail'},
                 'method_qualification_sha256':{'method_qualification'},
                 'independent_calculation_sha256':{'independent_calculation'}}
    missing=[]
    for field,kinds in requirement.items():
        value=getattr(spec,field)
        if not roles.get(value,set()).intersection(kinds):missing.append(field)
    return missing


def calculate_node_detail(spec:NodeDetailReview,node,source_roles:dict[str,set[str]])->dict:
    """Independent elementary free-body calculations from source-bound action vectors.

    Formulae for anchor plate, weld and bond are SCREENING until the applicable
    design methodology and interaction checks receive professional approval.
    """
    from .field_case_intake import NodeCase
    node=NodeCase.model_validate(node)
    missing=_source_roles_present(spec,source_roles)
    if (spec.node_id,spec.stage_id,spec.combination_name)!=(node.node_id,node.stage_id,node.combination_name):
        raise ValueError('Detailed connection stage, node or combination mismatch')
    actions=[x for x in node.member_actions if x.member_id==spec.action_member_id]
    if len(actions)!=1:raise ValueError('Node action member not found')
    action=actions[0];F=np.asarray(action.force_kn);M=np.asarray(action.moment_knm)
    M=M+np.cross(np.asarray(action.point_m)-np.asarray(spec.plate_center_m),F)
    n=np.asarray(spec.normal_global);u=np.asarray(spec.tangent_global);v=np.cross(n,u)
    N,Fu,Fv=(float(F@axis) for axis in (n,u,v))
    Mn,Mu,Mv=(float(M@axis) for axis in (n,u,v))
    checks=[]
    if spec.bearing is not None:
        stress=max(0,N)/spec.bearing.area_m2/1000
        utilization=stress/spec.bearing.design_bearing_mpa
        checks.append({'type':'local_bearing_component','demandMpa':round(stress,7),
             'declaredResistanceMpa':spec.bearing.design_bearing_mpa,
             'utilization':round(utilization,8),'status':'fail' if utilization>1 else 'screening_only'})
    if spec.bolts is not None:
        raw=np.asarray(spec.bolts.locations_uv_m,dtype=float)
        points=raw-np.mean(raw,axis=0)
        su=float(np.sum(points[:,0]**2));sv=float(np.sum(points[:,1]**2));sr=su+sv
        if abs(Mu)>1e-7 and sv<1e-8:missing.append('ANCHOR_GROUP_SECOND_MOMENT_U_UNDEFINED')
        if abs(Mv)>1e-7 and su<1e-8:missing.append('ANCHOR_GROUP_SECOND_MOMENT_V_UNDEFINED')
        if abs(Mn)>1e-7 and sr<1e-8:missing.append('ANCHOR_GROUP_TORSION_DISTRIBUTION_UNDEFINED')
        if not any(x.startswith('ANCHOR_GROUP_') for x in missing):
            count=len(points)
            tensions=np.full(count,-N/count)
            if abs(Mu)>1e-7:tensions+=Mu*points[:,1]/sv
            if abs(Mv)>1e-7:tensions-=Mv*points[:,0]/su
            shear=np.empty((count,2));shear[:,0]=Fu/count;shear[:,1]=Fv/count
            if abs(Mn)>1e-7:
                shear[:,0]+=-Mn*points[:,1]/sr
                shear[:,1]+=Mn*points[:,0]/sr
            T=np.maximum(tensions,0)
            shears=np.linalg.norm(shear,axis=1)
            t_ratio=T/spec.bolts.tension_capacity_kn;v_ratio=shears/spec.bolts.shear_capacity_kn
            interactions=t_ratio+v_ratio if spec.bolts.interaction=='linear_reference' else t_ratio*t_ratio+v_ratio*v_ratio
            utilization=float(np.max(interactions))
            checks.append({'type':'anchor_group_reference_interaction','demand':{
                'maximumTensionKn':round(float(max(T)),7),'maximumShearKn':round(float(max(shears)),7)},
                'method':spec.bolts.interaction,'utilization':round(utilization,8),
                'boltCount':count,'status':'fail' if utilization>1 else 'screening_only'})
    if spec.weld is not None:
        area_m2=spec.weld.effective_length_m*spec.weld.throat_mm/1000
        stress=hypot(Fu,Fv)/area_m2/1000
        utilization=stress/spec.weld.design_shear_mpa
        if abs(Mn)+abs(Mu)+abs(Mv)>1e-6:
            missing.append('WELD_COMPLEX_MOMENT_AND_ECCENTRICITY_METHOD')
        checks.append({'type':'weld_direct_shear_reference','demandMpa':round(stress,7),
          'utilization':round(utilization,8),
          'status':'outside_method_scope' if 'WELD_COMPLEX_MOMENT_AND_ECCENTRICITY_METHOD' in missing
                     else 'fail' if utilization>1 else 'screening_only'})
    if spec.anchorage is not None:
        utilisation=spec.anchorage.required_length_mm/max(spec.anchorage.available_length_mm,1e-12)
        checks.append({'type':'anchorage_geometry_only','barDiameterMm':spec.anchorage.bar_diameter_mm,
            'availableLengthMm':spec.anchorage.available_length_mm,
            'requiredLengthMmFromExternalMethod':spec.anchorage.required_length_mm,
            'utilization':round(utilisation,8),
            'status':'fail' if utilisation>1 else 'screening_only'})
    failed=any(x['status']=='fail' for x in checks)
    return {'nodeId':spec.node_id,'stageId':spec.stage_id,'combinationName':spec.combination_name,
        'memberId':spec.action_member_id,'localAction':{'compressionPositiveKn':round(N,6),
          'shearUKn':round(Fu,6),'shearVKn':round(Fv,6),
          'torsionNormalKnM':round(Mn,6),'momentUKnM':round(Mu,6),'momentVKnM':round(Mv,6)},
        'checks':checks,'missingEvidenceOrMethod':sorted(set(missing)),
        'status':'fail' if failed else 'missing_input' if missing else 'calculated_scope_limited',
        'fullConnectionCapacityQualified':False,'normativeMethodApproved':False,
        'formalConstructionIssue':False}


def compare_engineer_review_bytes(blob:bytes,project_id:str,section_id:str,
                                  details:list[dict],tolerance:float)->dict:
    """Cross-check a separately supplied *structured transcription* of review
    entries. Byte identity is checked by caller; signer authenticity is NOT.
    """
    original=_strict_json(blob)
    if (not isinstance(original,dict) or original.get('schemaVersion')!='pitguard.original-engineer-review/1'
        or original.get('projectId')!=project_id or original.get('sectionId')!=section_id):
        raise ValueError('Original engineer review transcription identity/schema mismatch')
    rows=original.get('findings')
    if not isinstance(rows,list) or len(rows)>1000:
        raise ValueError('Original review findings need bounded array')
    indexed={};issues=[]
    for row in rows:
        if not isinstance(row,dict) or not all(k in row for k in ('nodeId','stageId','checkType','utilization','reviewerDecision','methodReference')):
            issues.append('INVALID_ORIGINAL_REVIEW_ENTRY');continue
        key=(row['nodeId'],row['stageId'],row['checkType'])
        if key in indexed:issues.append('DUPLICATE_ENGINEER_REVIEW_ENTRY:'+str(key));continue
        if (not isinstance(row['utilization'],(int,float)) or isinstance(row['utilization'],bool)
            or not isfinite(row['utilization']) or row['utilization']<0
            or row['reviewerDecision'] not in ('pass','fail','review_required')
            or not isinstance(row['methodReference'],str) or len(row['methodReference'])<8):
            issues.append('INVALID_REVIEW_NUMERIC_OR_METHOD:'+str(key));continue
        indexed[key]=row
    report=[]
    for item in details:
        for check in item.get('checks',[]):
            name=(item['nodeId'],item['stageId'],check['type'])
            ref=indexed.get(name)
            if ref is None:
                issues.append('REVIEW_ENTRY_MISSING:'+str(name));continue
            calc=check.get('utilization')
            if not isinstance(calc,(float,int)):
                issues.append('COMPUTED_UTILIZATION_MISSING:'+str(name));continue
            abs_diff=abs(float(calc)-float(ref['utilization']))
            same=abs_diff<=tolerance
            if not same:issues.append('ENGINEER_COMPARISON_DIFF:'+str(name))
            if ref['reviewerDecision']=='pass' and check['status'] in ('fail','outside_method_scope'):
                issues.append('REVIEWER_PASS_CONFLICTS_WITH_CALCULATED_FAILURE:'+str(name))
            report.append({'nodeId':item['nodeId'],'stageId':item['stageId'],'checkType':check['type'],
                'modelUtilization':calc,'reviewedUtilization':ref['utilization'],
                'absoluteDifference':round(abs_diff,8),'numericalTolerance':tolerance,
                'reviewerDecisionAsTranscribed':ref['reviewerDecision'],
                'numericallyMatched':same})
    if not report:issues.append('NO_MATCHED_ENGINEER_REVIEW_CHECKS')
    return {'status':'review_records_numerically_matched_authenticity_pending' if not issues else 'review_discrepancy',
      'checkedCount':len(report),'checks':report,'issues':sorted(set(issues)),
      'authorshipVerified':False,'methodQualificationApproved':False,'formalConstructionIssue':False}


def run_deep_review(contract:DeepReviewContract,files_by_hash:dict[str,bytes],
                    roles:dict[str,set[str]],project_id:str,section_id:str,node_cases:list)->dict:
    if contract.original_numeric_sha256 not in files_by_hash or contract.independent_stages_sha256 not in files_by_hash:
        return {'status':'missing_input','missingEvidenceOrMethod':['ORIGINAL_OR_INDEPENDENT_STAGE_BYTES'],
                'engineeringQualified':False,'formalConstructionIssue':False}
    if 'original_numeric_export' not in roles.get(contract.original_numeric_sha256,set()):
        raise ValueError('Original stage document has incorrect evidence kind')
    if 'independent_calculation' not in roles.get(contract.independent_stages_sha256,set()):
        raise ValueError('Independent stage calculation has incorrect evidence kind')
    comparison=compare_independent_stage_bytes(files_by_hash[contract.original_numeric_sha256],
        files_by_hash[contract.independent_stages_sha256],project_id,section_id,contract.stage_tolerances)
    idx={(x.node_id,x.stage_id,x.combination_name):x for x in node_cases}
    nodes=[]
    for detail in contract.connection_details:
        node=idx.get((detail.node_id,detail.stage_id,detail.combination_name))
        if node is None:
            nodes.append({'nodeId':detail.node_id,'status':'missing_input',
              'missingEvidenceOrMethod':['MATCHED_NODE_ACTION'], 'formalConstructionIssue':False})
        else:nodes.append(calculate_node_detail(detail,node,roles))
    missing=[]
    signed_review=None
    if contract.original_engineer_review_sha256 is not None:
        h=contract.original_engineer_review_sha256
        if h not in files_by_hash or 'inspection_report' not in roles.get(h,set()):
            missing.append('ORIGINAL_ENGINEER_INSPECTION_REPORT_BYTES')
        else:
            signed_review=compare_engineer_review_bytes(files_by_hash[h],project_id,section_id,
                  nodes,contract.reviewer_numerical_tolerance)
            if signed_review['status']=='review_discrepancy':missing.append('ORIGINAL_ENGINEER_REVIEW_DISCREPANCY')
    else:missing.append('ORIGINAL_ENGINEER_REVIEW_NOT_SUPPLIED')
    if not nodes:missing.append('DETAILED_CONNECTION_CHECKS')
    if comparison['status']!='numeric_match_only':missing.append('FIVE_STAGE_NUMERICAL_COMPARISON_NOT_CLOSED')
    if any(x['status']=='fail' for x in nodes):status='fail'
    elif comparison['status']=='fail':status='fail'
    elif missing or any(x['status']=='missing_input' for x in nodes):status='missing_input'
    else:status='calculated_scope_limited'
    return {'schemaVersion':'pitguard.deep-review-result/3.89.1','status':status,
        'stageComparison':comparison,'detailedConnections':nodes,
        'engineerRecordComparison':signed_review,
        'missingEvidenceOrMethod':sorted(set(missing)),
        'originalSourceAuthorshipVerified':False,'engineeringQualified':False,
        'formalConstructionIssue':False}
