"""Engineering-identity gates for the five-stage Luoyu Village 3-3 review.

No PDF extremum or reconstructed chart is accepted as original node data.
"""
from __future__ import annotations
from pydantic import Field,model_validator
from .model import Strict,EvidenceBundle,audit_required
from ..real_project.boundary_evidence import luoyu_boundary_register,compare_complete_curves
from ..normative.reference_review import luoyu_three_three
from ..real_project.complete_curve_protocol import compare_five_stage_full_channels


class LuoyuAuditInput(Strict):
    evidence:EvidenceBundle
    candidate:dict|None=None
    original_numeric_reference:dict|None=None
    input_revision:str=Field(min_length=1)
    @model_validator(mode='after')
    def scoped(self):
        if self.evidence.section_id!='3-3':
            raise ValueError('This original-project audit is restricted to Luoyu 3-3')
        return self


def luoyu_template()->dict:
    register=luoyu_boundary_register();ref=luoyu_three_three()['facts']
    return {'schemaVersion':'pitguard.luoyu-input-admission/1',
      'reference':register['reference'],'source':register['source'],
      'directReport':{'diameterM':ref['pileDiameterM'],'spacingM':ref['pileSpacingM'],
        'pileLengthM':ref['pileLengthM'],'pileTopBelowOriginalGroundM':ref['pileTopOriginalGroundDepthM']},
      'conditions':register['conditions'],
      'requiredStages':['S1','S2','S3','S4','S5'],
      'pressureProtocol':{'requiredStationFields':['depthSiteM','side','pressureKind','pressureKpa'],'sides':['inside','outside'],'stationMatching':'exact_depth_and_side_no_implicit_interpolation'},
      'braceProtocol':{'requiredFields':['id','reactionKnPerPile','referenceMm'],'mustMatchEveryInstalledBrace':True},
      'requiredOutputChannels':['both_side_soil_pressure','displacement','signed_moment',
         'signed_shear_above_and_below','brace_force_and_install_reference'],
      'conditionSourceRequirements':{
        'soil_pressure_method':'verified mathematical method and project-specific slope/soil/water inputs',
        'crown_per_pile_stiffness':'original MN/m2 to single-pile kN/m conversion & load tributary area',
        'pile_toe_support':'source or structurally justified boundary condition',
        'brace_EA_over_L':'member length, section and elastic modulus',
        'brace_other_wall_stiffness':'opposite wall structural and soil stiffness',
        'brace_initial_gap':'installed reference and preload, per stage',
        'native_curve_arrays':'raw five-stage signed numeric data, original file digest, coordinate and force units'},
      'claimPolicy':'Never mark same-condition comparison as passed without complete reviewed data and compatible methods'}


def audit_luoyu(request:LuoyuAuditInput)->dict:
    conditions=luoyu_boundary_register()['conditions']
    direct={x['field']:x for x in conditions}
    proof={
        'luoyu.pile_toe_support':(None,'-'),
        'luoyu.crown_per_pile_stiffness_kn_m':(None,'kN/m'),
        'luoyu.brace_other_wall_stiffness_kn_m':(None,'kN/m'),
        'luoyu.brace_EA_over_L_kn_m':(None,'kN/m'),
        'luoyu.brace_initial_gap_m':(None,'m'),
        'luoyu.soil_pressure_method_id':(None,'-'),
        'luoyu.fill_gamma_kn_m3':(None,'kN/m3'),
        'luoyu.mixed_water_earth_method_id':(None,'-'),
        'luoyu.native_curve_source':(None,'-'),
    }
    # Known evidence is a *claim* until checked against a frozen calculation
    # input or an admissible source document. Do not compare to None as a value.
    idx=request.evidence.index();rows=[]
    for key,(_,unit) in proof.items():
        r=idx.get(key)
        status=('missing_evidence' if r is None else
                'not_confirmed' if r.state!='confirmed' else
                'unit_mismatch' if r.unit!=unit else 'reviewed_claim_pending_model_binding')
        rows.append({'key':key,'status':status,'value':r.value if r else None,
          'sourceSha256':r.source_sha256 if r else None,
          'reference':r.source_locator if r else None})
    candidate=request.candidate
    candidate_complete=bool(candidate and candidate.get('solverStatus')=='success' and
        candidate.get('modelStatus')=='conditional_research_only' and
        {x.get('id') for x in candidate.get('stages',[])}=={'S1','S2','S3','S4','S5'} and
        all(x.get('status')=='success' and x.get('curve') for x in candidate['stages']))
    if request.original_numeric_reference and candidate_complete:
        comparison=compare_five_stage_full_channels(candidate,request.original_numeric_reference)
    else:
        comparison={'comparisonStatus':'blocked','rmse':None,
           'requiredEvidence':['five_complete_candidate_stages','native_five_stage_curves']}
    all_reviewed=all(x['status']=='reviewed_claim_pending_model_binding' for x in rows)
    claim={'methodAndBoundaryConfirmed':False,
      'requestedComparison':comparison.get('comparisonStatus'),
      'eligibleForCurveComparison':False,
      'formalDesignReady':False}
    # Even if all source declarations are reviewed, this version's terrain
    # pressure projection, crown mapping and time-dependent two-wall stiffness
    # remain conditional model choices, not an independently qualified model.
    status=('missing_reference_curves' if not request.original_numeric_reference else
        'missing_project_conditions' if not all_reviewed else
        'model_method_not_independently_qualified')
    return {'schemaVersion':'pitguard.luoyu-admission-audit/1',
      'projectId':request.evidence.project_id,'sectionId':'3-3',
      'referenceRevision':request.input_revision,
      'assessmentStatus':status,'conditionEvidence':rows,
      'candidateFiveStagesComplete':candidate_complete,
      'nativeCurveComparison':comparison,
      'claimGate':claim,
      'recognizedOriginalFacts':{'pile_geometry':direct['pile_geometry']['value'],
            'pile_top_offset_m':direct['pile_top_offset']['valueM']},
      'knownEngineeringGaps':['坡台主动侧压力的规范方法和每层强度口径',
            '冠梁 22.7 MN/m2 到单桩弹簧的实际分担映射',
            '桩底边界、换撑时支点安装参考和对侧围护的全阶段响应',
            '原生五阶段两侧压力、位移、弯矩、剪力和支撑反力的完整数值数组'],
      'nextActions':['确认原项目输入来源、强度、水头和桩顶局部坐标',
          '以两端围护墙完整阶段响应验证支撑传力',
          '先比较全部曲线及阶段身份，再进行工程结论审核']}
