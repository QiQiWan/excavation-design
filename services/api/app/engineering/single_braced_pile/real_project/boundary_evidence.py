"""Provenance gates and non-fabricated full-curve comparison for Luoyu 3-3."""
from __future__ import annotations
from math import sqrt, isfinite
import re
from . import luoyu_three_three
from ..normative.reference_review import luoyu_three_three as reference_data

_REQUIRED=('pile_toe_support','crown_per_pile_stiffness','brace_other_wall_stiffness',
           'brace_EA_over_L','brace_initial_gap','soil_pressure_method',
           'fill_gamma','mixed_water_earth_basis','native_curve_arrays')


def luoyu_boundary_register()->dict:
    ref=reference_data();f=ref['facts']
    evidence=[
        {'field':'pile_geometry','status':'direct_report','value':{'diameterM':f['pileDiameterM'],
          'spacingM':f['pileSpacingM'],'lengthM':f['pileLengthM']},'physicalPage':32},
        {'field':'pile_top_offset','status':'direct_report','valueM':f['pileTopOriginalGroundDepthM'],'physicalPage':32},
        {'field':'crown_reported_stiffness','status':'direct_report','value':22.7,'unit':'MN/m2',
          'physicalPage':33,'note':'由该面刚度映射成单桩kN/m弹簧的方法未提供'},
        {'field':'crown_per_pile_stiffness','status':'unconfirmed','valueKnM':None,'physicalPage':33},
        {'field':'original_brace_stiffness','status':'direct_report','value':f['strutStiffnessMNPerM2'],
          'unit':'MN/m2','physicalPage':32},
        {'field':'original_brace_geometry','status':'direct_report','value':{'horizontalSpacingM':10.,
          'lengthM':14.,'angleDeg':45.},'physicalPage':33,
          'note':'报告表列构件几何，是否适用于3-3两侧对撑需原支撑平面核对'},
        {'field':'brace_other_wall_stiffness','status':'unconfirmed','value':None},
        {'field':'brace_EA_over_L','status':'unconfirmed','value':None},
        {'field':'brace_initial_gap','status':'unconfirmed','value':None},
        {'field':'pile_toe_support','status':'unconfirmed','value':None,
          'note':'“刚接”为支撑连接描述，不能自动解释为桩端固结'},
        {'field':'terrain_profile','status':'direct_report','value':f['terrain'],'physicalPage':32},
        {'field':'fill_gamma','status':'inferred_candidate','value':18.,'unit':'kN/m3',
          'note':'首层填土报告重度18，坡台新增填土是否同材质未证实'},
        {'field':'mixed_water_earth_basis','status':'direct_report_needs_method','value':
           [x['stressBasis'] for x in f['soilLayersOriginalGround']],'physicalPage':32},
        {'field':'soil_pressure_method','status':'unconfirmed','value':None,
         'note':'坡台采用朗肯、库仑或弹性附加侧压力的具体原式未给'},
        {'field':'native_curve_arrays','status':'absent','value':None,
         'note':'PDF曲线图片不能替代原软件逐节点数值导出'},
    ]
    by={x['field']:x for x in evidence}
    return {'reference':f'{ref["project"]}/3-3','source':ref['source'],
        'conditions':evidence,'mandatoryUnconfirmed':[k for k in _REQUIRED
          if by.get(k,{}).get('status') not in ('direct_report','independently_verified')],
        'sameConditionValidated':False,
        'sourceControl':'PDF physical pp.32-34; raw node curves unavailable'}


def compare_complete_curves(candidate:dict,reference:dict)->dict:
    """Only compare verified stage/depth channels with shared physical meaning.

    Every reference series must be a measured or native numeric array, not
    interpolated from published extrema. Missing fields return a blocker and
    never generate an RMSE from arbitrary benchmark geometry.
    """
    failed=[]
    if reference.get('sourceKind')!='native_numeric_export':
        failed.append('original_native_numeric_export')
    if not isinstance(reference.get('fileSha256'),str) or not re.fullmatch('[a-fA-F0-9]{64}',reference['fileSha256']):
        failed.append('reference_sha256')
    if reference.get('depthDatum')!='original_ground_down_m':
        failed.append('original_ground_down_m_datum')
    if reference.get('forceBasis')!='single_pile':
        failed.append('validated_single_pile_force_basis')
    if not reference.get('methodAndBoundaryConfirmed'):
        failed.append('independent_method_and_boundary_confirmation')
    data=reference.get('stages',[])
    candidate_stages=candidate.get('stages') or []
    ids=[x.get('id') for x in candidate_stages]
    if len(ids)!=5 or set(ids)!={'S1','S2','S3','S4','S5'}:
        failed.append('candidate_five_stage_curves')
    if len(data)!=5 or {x.get('id') for x in data}!={'S1','S2','S3','S4','S5'}:
        failed.append('five_stage_numeric_arrays')
    if failed:
        return {'comparisonStatus':'blocked','pointwiseErrors':None,'rmse':None,
                'reason':'Missing source or engineering identity required for same-condition comparison',
                'requiredEvidence':sorted(set(failed))}
    outcomes=[]
    for item in data:
        cand=next((s for s in candidate_stages if s.get('id')==item['id']),None)
        if cand is None:
            failed.append(f'{item["id"]}:missing_candidate_stage');continue
        reference_points=item.get('points',[])
        if cand.get('status')!='success' or not isinstance(reference_points,list) or len(reference_points)<10:
            failed.append(f'{item["id"]}:complete_curve');continue
        channels=('depthSiteM','displacementMm','momentKnM','shearKn')
        candidates=cand.get('curve') or []
        if not all(isinstance(x,dict) for x in reference_points+candidates):
            failed.append(f'{item["id"]}:invalid_curve_items');continue
        invalid=(lambda x: any(k not in x or isinstance(x[k],bool) or not isinstance(x[k],(int,float))
                        or not isfinite(x[k]) for k in channels) or x.get('side') not in ('above','below'))
        if any(invalid(x) for x in reference_points+candidates):
            failed.append(f'{item["id"]}:nonfinite_or_undefined_channels');continue
        ref_keys=[(round(x['depthSiteM'],8),x['side']) for x in reference_points]
        cand_keys=[(round(x['depthSiteM'],8),x['side']) for x in candidates]
        if len(set(ref_keys))!=len(ref_keys) or len(set(cand_keys))!=len(cand_keys):
            failed.append(f'{item["id"]}:duplicate_station_side');continue
        # Require coincident physical nodes: no extrapolation or smoothing
        # over support-induced shear jumps. Interpolation must be a separately
        # reviewed engineering step, never an implicit comparator fallback.
        index={(round(v['depthSiteM'],8),v['side']):v for v in candidates}
        if any((round(v['depthSiteM'],8),v['side']) not in index for v in reference_points):
            failed.append(f'{item["id"]}:station_grid_mismatch');continue
        entry={'stageId':item['id'],'count':len(reference_points),'channelRMSE':{}}
        for channel in ('displacementMm','momentKnM','shearKn'):
            sums=[(index[(round(x['depthSiteM'],8),x['side'])][channel]-x[channel])**2
                  for x in reference_points]
            entry['channelRMSE'][channel]=sqrt(sum(sums)/len(sums))
        outcomes.append(entry)
    if failed:
        return {'comparisonStatus':'blocked','pointwiseErrors':None,'rmse':None,
                'requiredEvidence':failed,'reason':'Original and computed curve data do not share all required stations and channels'}
    return {'comparisonStatus':'numerically_compared_peer_review_pending',
            'sourceHash':reference['fileSha256'],'stages':outcomes,
            'formalSameConditionVerification':False,
            'reason':'Same curve grids compared; independent engineering peer review still required'}
