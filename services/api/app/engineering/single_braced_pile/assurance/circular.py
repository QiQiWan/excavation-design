"""Synchronized circular-pile engineering evidence audit.

The output keeps strength, serviceability and construction checks separate.
No unsupported crack-width formula or axial-shear extension is introduced.
"""
from __future__ import annotations
from math import isclose
from pydantic import Field, model_validator
from .model import Strict,EvidenceBundle,audit_required


class ExternalCircularCrackCalculation(Strict):
    snapshot_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
    combination_name: str=Field(min_length=2)
    stage_id: str=Field(min_length=1)
    depth_m: float=Field(ge=0)
    side: str=Field(pattern=r'^(above|below)$')
    method_id: str=Field(min_length=8)
    method_scope: str=Field(min_length=15)
    width_mm: float=Field(ge=0,le=20)
    limit_mm: float=Field(gt=0,le=2)
    calculation_file_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    independent_reviewer: str=Field(min_length=2)
    verification_basis: str=Field(min_length=20)


class CircularAuditInput(Strict):
    project_id: str=Field(min_length=1)
    section_id: str=Field(min_length=1)
    result_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
    evidence: EvidenceBundle
    minimum_outer_hoop_cover_mm: float | None=Field(default=None,gt=0,le=150)
    maximum_hoop_spacing_mm: float | None=Field(default=None,gt=0,le=400)
    external_crack_calculation: ExternalCircularCrackCalculation | None=None
    @model_validator(mode='after')
    def same_project(self):
        if (self.project_id,self.section_id)!=(self.evidence.project_id,self.evidence.section_id):
            raise ValueError('Circular-pile evidence does not belong to this project/section')
        return self


def audit_circular(request:CircularAuditInput,result:dict)->dict:
    snap=result['snapshot'];cage=snap['cage'];pile=snap['pile']
    if (request.project_id,request.section_id,request.result_hash)!=(
        result['projectId'],result['sectionId'],result['resultHash']):
        raise ValueError('Frozen circular-pile result identity mismatch')
    required={
      'circular.diameter_m':(pile['diameter_m'],'m'),
      'circular.bar_count':(cage['bar_count'],'count'),
      'circular.bar_diameter_mm':(cage['bar_diameter_mm'],'mm'),
      'circular.axis_cover_mm':(cage['bar_axis_cover_mm'],'mm'),
      'circular.stirrup_diameter_mm':(cage['stirrup_diameter_mm'],'mm'),
      'circular.stirrup_spacing_mm':(cage['stirrup_spacing_mm'],'mm'),
      'circular.concrete_design_mpa':(cage['concrete_design_mpa'],'MPa'),
      'circular.steel_design_mpa':(cage['steel_design_mpa'],'MPa'),
    }
    if request.minimum_outer_hoop_cover_mm is not None:
        required['circular.minimum_outer_hoop_cover_mm']=(request.minimum_outer_hoop_cover_mm,'mm')
    if request.maximum_hoop_spacing_mm is not None:
        required['circular.maximum_hoop_spacing_mm']=(request.maximum_hoop_spacing_mm,'mm')
    evidence=audit_required(request.evidence,required)
    source_items={x['key']:x['status'] for x in evidence['items']}
    checks=result.get('normativeCoverage',{}).get('requiredChecks',{})
    matched=checks.get('concrete_circular_matched_nm_v',{}).get('result',{})
    crack=checks.get('concrete_circular_crack_control',{}).get('result',{})
    rows=matched.get('cases',[])
    count_same=sum(isinstance(r.get('axialDesignKn'),(int,float)) and
                 r.get('depthM') is not None and r.get('stageId') and r.get('combination')
                 for r in rows)
    failed_strength=any(x.get('status')=='fail' or x.get('shearStatus')=='fail' for x in rows)
    unverified_shear=[{'combination':x.get('combination'),'stage':x.get('stageId'),
                      'depthM':x.get('depthM'),'axialDesignKn':x.get('axialDesignKn')}
                      for x in rows if x.get('shearStatus')!='pass']
    diameter_mm=pile['diameter_m']*1000
    actual_cover=cage['bar_axis_cover_mm']-cage['bar_diameter_mm']/2-cage['stirrup_diameter_mm']
    provided_hoop=cage['stirrup_spacing_mm']
    cover_status='not_checked' if request.minimum_outer_hoop_cover_mm is None else (
        'pass' if actual_cover>=request.minimum_outer_hoop_cover_mm else 'fail')
    spacing_status='not_checked' if request.maximum_hoop_spacing_mm is None else (
        'pass' if provided_hoop<=request.maximum_hoop_spacing_mm else 'fail')
    if cover_status=='pass' and source_items.get('circular.minimum_outer_hoop_cover_mm')!='matched':
        cover_status='unverified_limit_source'
    if spacing_status=='pass' and source_items.get('circular.maximum_hoop_spacing_mm')!='matched':
        spacing_status='unverified_limit_source'
    outside=any(x.get('status')=='outside_method_scope' for x in rows)
    # Do not allow an arbitrary external width result to override the internal
    # method register. Even a matching, reviewer-signed document still needs
    # approved method qualification and independent source-bytes verification.
    external=None;ext=request.external_crack_calculation
    if ext:
        if ext.snapshot_hash!=result['snapshotHash']:
            raise ValueError('External crack calculation is for a different frozen snapshot')
        if not any(r.get('stageId')==ext.stage_id and r.get('combinationName')==ext.combination_name and
                   isclose(r.get('depthM',-1),ext.depth_m,abs_tol=1e-7) and r.get('side')==ext.side
                   for r in crack.get('cases',[])):
            raise ValueError('External crack width must refer to a computed SLS station, stage and combination')
        expected_limit=(snap.get('crack_control') or {}).get('width_limit_mm')
        if expected_limit is None or not isclose(expected_limit,ext.limit_mm,rel_tol=1e-9):
            raise ValueError('External crack width limit must match the frozen project crack limit')
        external={'status':'submitted_for_independent_method_review' if ext.width_mm<=ext.limit_mm else 'fail',
                  'computedWidthMm':ext.width_mm,'declaredLimitMm':ext.limit_mm,
                  'sourceSha256':ext.calculation_file_sha256,
                  'methodId':ext.method_id,'methodScope':ext.method_scope,
                  'formalDesignReady':False,
                  'note':'No current registered circular crack-width algorithm is qualified for automatic approval'}
    crack_status=crack.get('status','missing_input')
    failure=failed_strength or cover_status=='fail' or spacing_status=='fail' or (
        external is not None and external['status']=='fail') or crack_status=='fail'
    if failure:state='failed_check'
    elif evidence['status']!='metadata_confirmed':state='evidence_incomplete'
    elif not rows or count_same!=len(rows):state='matched_nm_v_incomplete'
    elif unverified_shear or outside:state='axial_shear_method_review_required'
    else:state='serviceability_and_construction_review_required'
    return {'schemaVersion':'pitguard.circular-pile-standard-audit/1',
        'projectId':request.project_id,'sectionId':request.section_id,'resultHash':request.result_hash,
        'assessmentStatus':state,'formalDesignReady':False,
        'evidence':evidence,
        'sectionGeometry':{'diameterMm':diameter_mm,'outerHoopCoverMm':actual_cover,
         'providedHoopSpacingMm':provided_hoop},
        'strength':{'matchedStations':count_same,'totalStations':len(rows),
         'status':matched.get('status','missing_input'),'anyFailure':failed_strength,
         'axialShearUnverifiedStations':unverified_shear[:30],
         'axialShearUnverifiedCount':len(unverified_shear)},
        'serviceability':{'status':crack_status,'externalCrackReport':external,
          'classAndCombinationEvidence':crack.get('cases',[])[:30]},
        'construction':{'outerHoopCover':{'status':cover_status,
                  'actualMm':actual_cover,'requiredMm':request.minimum_outer_hoop_cover_mm},
              'hoopSpacing':{'status':spacing_status,
                  'actualMm':provided_hoop,'maximumMm':request.maximum_hoop_spacing_mm}},
        'normativeBasis':['JGJ 120-2012 4.3.2 and Appendix B (verify published edition)',
                          'GB/T 50010-2010 (2024 amendment): applicable capacity/serviceability provisions'],
        'requiredNextActions':['逐组合、阶段、深度确认轴力与弯剪力同步及二阶效应',
           '选定并独立验证适用圆桩截面、受力、持续作用的裂缝宽度方法',
           '将材料等级、箍筋、保护层、锚固、搭接与连接按相应规范和施工要求逐项校核']}
