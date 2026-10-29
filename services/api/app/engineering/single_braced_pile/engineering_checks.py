"""Stable facade for the independent normative and member-check modules.

Keep previous import paths working. Every item has its own applicability and
verification status; a successful structural solve cannot hide incomplete checks.
"""
from __future__ import annotations
from math import pi,sin
from .normative.stability import (embedment_single_strut,heave_prandtl,
    minimal_toe_depth,soft_soil_additional)
from .normative.groundwater import groundwater_checks
from .normative.support import support_checks
from .normative.reinforcement import circular_checks,synchronized_combination_sections
from .normative.actions import combinations
from .normative.crack_scope import crack_matrix


def embedment_moment_screen(model,stage):
    """Compatibility facade; now returns an explicit normative check record."""
    return embedment_single_strut(model,stage)


def circular_pure_bending_screen(model,demand_knm):
    """Compatibility facade: geometry only; N-M is evaluated separately."""
    cage=model.cage;radius=model.pile.diameter_m*500
    rr=radius-cage.bar_axis_cover_mm
    return {'status':'geometry_checked_capacity_pending',
        'method':'circular_cage_exact_geometry',
        'demandMomentKnM':demand_knm,
        'providedAreaMm2':cage.bar_count*pi*cage.bar_diameter_mm**2/4,
        'outerHoopClearCoverMm':cage.bar_axis_cover_mm-cage.bar_diameter_mm/2-cage.stirrup_diameter_mm,
        'longitudinalChordClearSpacingMm':2*rr*sin(pi/cage.bar_count)-cage.bar_diameter_mm,
        'capacityKnM':None}


def normative_coverage(model,stage_results,demand,action_result=None):
    action_result=action_result or combinations(model,stage_results)
    results=[];installed=False
    for stage,calc in zip(model.stages,stage_results):
        if stage.event=='install_strut':installed=True
        if stage.excavation_depth_m<=0:continue
        for method in (heave_prandtl,minimal_toe_depth):
            results.append(method(model,stage))
        if installed:
            results.append(embedment_single_strut(model,stage))
            results.append(soft_soil_additional(model,stage))
        results.extend(groundwater_checks(model,stage))
    member=support_checks(model,stage_results,action_result)
    rebar=circular_checks(model,stage_results,action_result)
    matched=synchronized_combination_sections(model,action_result.get('explicit',{}))
    cracks=crack_matrix(model,action_result.get('explicit',{}))
    buckets={}
    for r in results:
        buckets.setdefault(r['code'],[]).append(r)
    def group(key):
        rows=buckets.get(key,[])
        if not rows:return {'status':'not_calculated','stages':[]}
        states={x['status'] for x in rows}
        status=next((s for s in ('fail','missing_input','outside_method_scope','not_calculated','screening_only','pass')
                     if s in states),'not_applicable')
        return {'status':status,'stages':rows,'governingStage':min(
          (r for r in rows if r.get('safetyFactor') is not None),
          key=lambda x:x['safetyFactor'],default=None)}
    mapping={'4.2.2_embedment':group('embedment'),
        '4.2.3_global_sliding':{'status':'not_generally_mandated_for_supported_type',
             'reason':'本工作台仅单层内支撑，4.2.3适用对象和特殊边坡须另审'},
        '4.2.4_basal_heave':group('basal_heave'),
        '4.2.5_soft_soil_heave':group('soft_soil_heave'),
        '4.2.6_groundwater':group('groundwater') if model.groundwater_check.mode in ('unknown','no_trigger')
          else {'status': next((state for state in ('fail','missing_input','outside_method_scope','not_calculated','pass')
                 if any(x['status']==state for x in results if 'Appendix C' in x['clause'])),'not_calculated'),
                 'stages':[x for x in results if 'Appendix C' in x['clause']]},
        '4.2.7_minimum_embedment':group('minimum_embedment'),
        '3.1_load_combinations':{'status':action_result.get('explicit',{}).get('status','missing_input'), 'result':action_result},
        'construction_adverse_stability':{
            'status':'not_calculated' if not action_result.get('constructionAdverse')
                  else ('fail' if any(c['status']!='calculated' or any(x['status']=='fail' for x in c.get('stabilityChecks',[]))
                    for c in action_result['constructionAdverse'])
                    else ('missing_input' if any(any(x['status'] in ('missing_input','not_calculated','outside_method_scope')
                              for x in c.get('stabilityChecks',[])) for c in action_result['constructionAdverse'])
                         else 'calculated_scope_limited')),
            'result':action_result.get('constructionAdverse',[]),
            'reason':'不利工况稳定性逐项检查；已验证公式的局部通过不能代替未覆盖专项'},
        '4.9_internal_brace':{'status':member['status'],'result':member},
        'concrete_circular_pile':{'status':rebar['status'],'result':rebar},
        'concrete_circular_matched_nm_v':{'status':matched['status'],'result':matched},
        'concrete_circular_crack_control':{'status':cracks['status'],'result':cracks}}
    blocking={k:v for k,v in mapping.items() if v['status'] not in ('pass','not_applicable','not_generally_mandated_for_supported_type')}
    # A numerical success is not enough for structural approvals. Explicit design
    # file reviews remain required regardless of the result of these checks.
    return {'schemaVersion':'pitguard.one-strut-normative-coverage/2',
        'formalDesignReady':False,'requiredChecks':mapping,'blockingCodes':sorted(blocking),
        'fullNormativeVerificationComplete':False,
        'scope':'Single pile / one strut / horizontal ground / homogeneous Rankine separated water; engineering peer review required'}
