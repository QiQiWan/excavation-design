"""Original project evidence only. Never fabricate reference nodal arrays."""
from pathlib import Path
import json


def luoyu_three_three():
    p=Path(__file__).resolve().parent.parent/'references'/'luoyu_3_3.json'
    return json.loads(p.read_text(encoding='utf-8'))


def audit_original_report_arithmetic(ref):
    """Only verify relationships explicitly available in the original report.

    This cannot establish method identity or a finite-element comparison.
    """
    f=ref['facts'];offset=f['pileTopOriginalGroundDepthM']
    toe_site=offset+f['pileLengthM']
    depth_below_final=toe_site-f['siteFinalExcavationDepthM']
    conversion=ref['conversionToPileTopLocal']
    peak_factor=f['reportedMomentDesignKnM']/f['reportedMomentCharacteristicKnM']
    report_rounded=round(1.25*f['reportedMomentCharacteristicKnM'],1)
    return {
        'auditStatus':'original_report_arithmetic_only',
        'checkedRelationships':[
            {'name':'first_excavation_local','reported':conversion['firstExcavationLocalM'],
             'recalculated':2.05-offset,'consistent':abs(conversion['firstExcavationLocalM']-(2.05-offset))<1e-10},
            {'name':'final_excavation_local','reported':conversion['finalExcavationLocalM'],
             'recalculated':f['siteFinalExcavationDepthM']-offset,
             'consistent':abs(conversion['finalExcavationLocalM']-(f['siteFinalExcavationDepthM']-offset))<1e-10},
            {'name':'first_support_local','reported':conversion['initialSupportLocalM'],
             'recalculated':f['siteSupportDepthM']-offset,
             'consistent':abs(conversion['initialSupportLocalM']-(f['siteSupportDepthM']-offset))<1e-10},
            {'name':'embedment_toe_depth_below_excavation','recalculated':depth_below_final,
             'consistent':abs(depth_below_final-3.0)<1e-10},
            {'name':'reported_design_moment_rounded_1_25x','reported':f['reportedMomentDesignKnM'],
             'recalculated':report_rounded,'consistent':abs(report_rounded-f['reportedMomentDesignKnM'])<.11},
        ],
        'observedDesignMomentMultiplier':peak_factor,
        'doesNotVerify':['original_pressure_curves','full_stage_structural_response',
                         'actual_concrete_design_factor','original_method_parameters'],
    }

def assess_real_project_comparability(model):
    ref=luoyu_three_three()
    mismatches=list(ref['limitations'])
    if model.purpose=='verification_example':
        mismatches.insert(0,'当前输入为合成验证例，不可代替原3-3工程')
    if not model.project_id or model.section_id!='3-3':
        mismatches.insert(0,'工程或剖面身份不一致')
    return {'project':ref['project'],'reference':ref['section'],
       'sourcePhysicalPages':ref['source']['physicalPages'],
       'referenceStages':ref['stages'],
       'originalFacts':ref['facts'],'originalArithmeticAudit':audit_original_report_arithmetic(ref),
       'comparisonStatus':'not_same_conditions',
       'fullCurveComparisonStatus':'missing_original_numeric_curves',
       'pointwiseErrors':None,'rmse':None,'peakErrorPercent':None,
       'whyNotComparable':mismatches,
       'instructions':'统一地面/桩顶坐标、坡台和邻载、混合土水、换撑及结构约束，取得各阶段数值节点后调用参考曲线协议比较'}
