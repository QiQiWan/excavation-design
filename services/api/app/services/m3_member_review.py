"""M3/M7 element and joint utilization reference review, V3.89.4.

Demand vectors from the frozen M3 research candidate have no verified ULS/SLS
combination factors. Capacity metadata is external, so checks are conservative
screening only. Never use this module to issue formal structural compliance.
"""
from __future__ import annotations
from math import pi, hypot, sqrt
from pydantic import Field, model_validator

from app.engineering.single_braced_pile.assurance.model import Strict
from .m3_code_review import HEX, read_review_archive


class MemberCapacity(Strict):
    member_id: str = Field(min_length=1,max_length=200)
    axial_magnitude_capacity_kn: float = Field(gt=0,le=1e10)
    shear_y_capacity_kn: float = Field(gt=0,le=1e10)
    shear_z_capacity_kn: float = Field(gt=0,le=1e10)
    moment_y_capacity_knm: float = Field(gt=0,le=1e10)
    moment_z_capacity_knm: float = Field(gt=0,le=1e10)
    torsion_capacity_knm: float = Field(gt=0,le=1e10)
    effective_length_factor: float | None = Field(default=None,gt=0.2,le=5)
    method_reference: str = Field(min_length=12,max_length=350)
    source_sha256: str = Field(pattern=HEX)


class NodeCapacity(Strict):
    stage_id: str = Field(min_length=2,max_length=80)
    node_id: int = Field(ge=0)
    force_resultant_capacity_kn: float = Field(gt=0,le=1e10)
    moment_resultant_capacity_knm: float = Field(gt=0,le=1e10)
    anchor_available_mm: float = Field(ge=0,le=100000)
    anchor_required_mm: float = Field(gt=0,le=100000)
    weld_throat_mm: float = Field(gt=0,le=200)
    weld_effective_length_mm: float = Field(gt=0,le=100000)
    weld_allowable_shear_mpa: float = Field(gt=0,le=10000)
    bearing_area_mm2: float = Field(gt=0,le=1e9)
    bearing_allowable_mpa: float = Field(gt=0,le=5000)
    method_reference: str = Field(min_length=12,max_length=350)
    source_sha256: str = Field(pattern=HEX)


class ComponentReviewDocument(Strict):
    schema_version: str = Field(alias='schemaVersion')
    project_id: str = Field(min_length=1,max_length=100)
    source_archive_sha256: str = Field(pattern=HEX)
    candidate_result_sha256: str = Field(pattern=HEX)
    revision: str = Field(min_length=1,max_length=100)
    action_classification: str = Field(default='unclassified_m3_reference',max_length=120)
    member_capacities: tuple[MemberCapacity,...] = Field(default=(),max_length=800)
    node_capacities: tuple[NodeCapacity,...] = Field(default=(),max_length=3000)
    @model_validator(mode='after')
    def strict_keys(self):
        if self.schema_version!='pitguard.m3-component-capacity/3.89.4':
            raise ValueError('Unsupported M3 member and node capacity contract')
        keys=[x.member_id for x in self.member_capacities]
        if len(keys)!=len(set(keys)):raise ValueError('Duplicate member capacity identifiers')
        nodes=[(x.stage_id,x.node_id) for x in self.node_capacities]
        if len(nodes)!=len(set(nodes)):raise ValueError('Duplicate stage-node capacity identifiers')
        if self.action_classification!='unclassified_m3_reference':
            raise ValueError('M3 demand actions are not verified as ULS/SLS design combinations')
        return self


def screen_component_capacity(study,result,archive:bytes,result_sha256:str,study_archive_sha256:str):
    doc,src=read_review_archive(archive)
    review=ComponentReviewDocument.model_validate(doc)
    if review.project_id!=study.frame.topology.project_id or review.source_archive_sha256!=study_archive_sha256 or review.candidate_result_sha256!=result_sha256:
        raise ValueError('Component review frozen study/candidate identity mismatch')
    for entry in (*review.member_capacities,*review.node_capacities):
        if entry.source_sha256 not in src['sourceDigests']:
            raise ValueError('Component capacity source bytes missing or unverified')
    members={m['id']:m for m in result['structuralGraph']['members']}
    stage_ids={stage['stageId'] for stage in result['stages']}
    nodes={n['id'] for n in result['structuralGraph']['nodes']}
    for item in review.member_capacities:
        if item.member_id not in members:raise ValueError('Member capacity references unknown frozen member')
    for item in review.node_capacities:
        if item.stage_id not in stage_ids or item.node_id not in nodes:
            raise ValueError('Node capacity references unknown frozen stage/node')
    by_member={x.member_id:x for x in review.member_capacities}
    by_node={(x.stage_id,x.node_id):x for x in review.node_capacities}
    frame=study.frame
    sections={'wall':frame.wall_section,'crown':frame.crown_section,
              'wale':frame.wale_section,'strut':frame.strut_section,'column':frame.column_section}
    rows=[];node_rows=[];missing=[];violations=[]
    for stage in result['stages']:
        sid=stage['stageId']
        for measured in stage['memberResults']:
            mid=measured['memberId'];limit=by_member.get(mid)
            if not limit:
                missing.append({'stageId':sid,'memberId':mid,'kind':'member_capacity_missing'})
                continue
            ratios={
                'axialMagnitude':measured['axialKn']/limit.axial_magnitude_capacity_kn,
                'shearY':measured['shearYKn']/limit.shear_y_capacity_kn,
                'shearZ':measured['shearZKn']/limit.shear_z_capacity_kn,
                'momentY':measured['momentYKnM']/limit.moment_y_capacity_knm,
                'momentZ':measured['momentZKnM']/limit.moment_z_capacity_knm,
                'torsion':measured['torsionKnM']/limit.torsion_capacity_knm}
            # A linear N-My-Mz sum is conservative screening, NOT a generic code formula.
            linear=ratios['axialMagnitude']+ratios['momentY']+ratios['momentZ']
            ratios['linearAxialBiaxialScreen']=linear
            group=members[mid]['group'];section=sections.get(group)
            stability=None
            if limit.effective_length_factor is not None and section is not None and group in ('strut','column'):
                ell=measured['lengthM']*limit.effective_length_factor
                ncr=pi*pi*section.E_kn_m2*min(section.Iy_m4,section.Iz_m4)/(ell*ell)
                stability={'eulerReferenceKn':round(ncr,6),'axialOverEuler':round(measured['axialKn']/ncr,8),
                           'note':'Elastic Euler benchmark; not design column strength'}
            status='screen_exceeded' if max(ratios.values())>1+1e-9 or (stability and stability['axialOverEuler']>1) else 'within_reference_screen'
            row={'stageId':sid,'memberId':mid,'group':group,'methodReference':limit.method_reference,
                 'sourceSha256':limit.source_sha256,'ratios':{k:round(v,8) for k,v in ratios.items()},
                 'stabilityReference':stability,'status':status,'engineeringQualified':False}
            rows.append(row)
            if status=='screen_exceeded':violations.append({'stageId':sid,'objectId':mid,'type':'member'})
        node_actions={}
        for action in stage['braceNodeActions']:
            node_actions.setdefault(action['nodeId'],[]).append(action)
        for node_id,actions in node_actions.items():
            limit=by_node.get((sid,node_id))
            if not limit:
                missing.append({'stageId':sid,'nodeId':node_id,'kind':'node_capacity_missing'})
                continue
            # Sum of absolute member-end resultants prevents artificial cancellation
            # among independent connections. It is a gross upper-bound screen only.
            force=sum(sqrt(sum(v*v for v in a['actionOnNodeForceKn'])) for a in actions)
            moment=sum(sqrt(sum(v*v for v in a['actionOnNodeMomentKnM'])) for a in actions)
            weld_mpa=force*1000/(limit.weld_throat_mm*limit.weld_effective_length_mm)
            bearing_mpa=force*1000/limit.bearing_area_mm2
            ratios={'force':force/limit.force_resultant_capacity_kn,
                    'moment':moment/limit.moment_resultant_capacity_knm,
                    'anchor':limit.anchor_required_mm/max(limit.anchor_available_mm,1e-12),
                    'weldDirectShear':weld_mpa/limit.weld_allowable_shear_mpa,
                    'grossBearing':bearing_mpa/limit.bearing_allowable_mpa}
            status='screen_exceeded' if max(ratios.values())>1+1e-9 else 'within_reference_screen'
            node_rows.append({'stageId':sid,'nodeId':node_id,
                'connectedBraceEndCount':len(actions),'forceUpperBoundKn':round(force,6),
                'momentUpperBoundKnM':round(moment,6),
                'sourceSha256':limit.source_sha256,'methodReference':limit.method_reference,
                'ratios':{k:round(v,8) for k,v in ratios.items()},'status':status,'engineeringQualified':False})
            if status=='screen_exceeded':violations.append({'stageId':sid,'objectId':node_id,'type':'node'})
    status='screen_exceeded' if violations else 'incomplete' if missing else 'numerical_screen_only'
    return {'schemaVersion':'pitguard.m3-component-screen/3.89.4',
        'candidateResultSha256':result_sha256,'sourceArchiveSha256':study_archive_sha256,
        'reviewArchiveSha256':src['archiveSha256'],'projectId':review.project_id,
        'actionClassification':review.action_classification,'memberChecks':rows,'nodeChecks':node_rows,
        'missingChecks':missing,'screenViolations':violations,
        'unresolvedEngineeringChecks':['UNVERIFIED_ULS_AND_SLS_COMBINATION_FACTORS',
            'CONCRETE_N_M_V_INTERACTION_NOT_QUALIFIED','STEEL_MEMBER_BUCKLING_AND_TORSION_NOT_FULLY_QUALIFIED',
            'WELD_AND_ANCHOR_GROUP_CODE_METHOD_UNQUALIFIED','NODE_LOAD_PATH_AND_DETAIL_REVIEW_REQUIRED',
            'MISSING_CAPACITY_DATA' if missing else 'INDEPENDENT_CAPACITY_AUTHORITY_REQUIRED'],
        'status':status,'screenedOnly':True,'sourceBytesVerified':True,
        'engineeringQualified':False,'formalConstructionIssue':False}
