"""JGJ 120-2012 Appendix C groundwater uplift and flow checks.

Applicability is explicit; absence of groundwater investigation cannot pass.
"""
from ..contracts import Section
from .stability import _result,_integrate,_layer


def groundwater_checks(model:Section,stage):
    context=model.groundwater_check;mode=context.mode
    if mode=='unknown':return [_result('groundwater','4.2.6 / Appendix C','missing_input',
        requiredInputs=['groundwater_check.mode','groundwater_check.assessment_basis'],
        reason='尚未核定是否存在承压水或悬挂式截水帷幕')]
    if mode=='no_trigger':
        return [_result('groundwater','4.2.6 / Appendix C','not_applicable',
            evidence=context.assessment_basis,reason='已提供无触发条件的项目判断，仍须审核水文依据')]
    if not context.assessment_basis:
        return [_result('groundwater','4.2.6 / Appendix C','missing_input',requiredInputs=['groundwater_check.assessment_basis'])]
    checks=[];h=stage.excavation_depth_m;wg=model.water_unit_weight_kn_m3
    if mode in ('confined','both'):
        top=context.confined_aquifer_top_depth_m;delta=context.confined_head_difference_m
        if top is None or delta is None or top<=h or top>model.layers[-1].bottom_m:
            checks.append(_result('confined_uplift','Appendix C C.0.1','missing_input',
                    requiredInputs=['confined_aquifer_top_depth_m (> excavation depth)','confined_head_difference_m']))
        else:
            D=top-h; gamma=_integrate(sorted({h,top,*[x for l in model.layers for x in (l.top_m,l.bottom_m) if h<=x<=top]}),
                            lambda z:_layer(model.layers,z).gamma_natural_kn_m3)/D
            numerator=D*gamma;denom=(delta+D)*wg;factor=numerator/denom
            checks.append(_result('confined_uplift','Appendix C C.0.1','pass' if factor>=1.1 else 'fail',
                stageId=stage.id,safetyFactor=factor,requiredFactor=1.1,weightedNaturalUnitWeightKnM3=gamma,
                separatingLayerThicknessM=D,headDifferenceM=delta, resistingStressKpa=numerator,drivingStressKpa=denom))
    if mode in ('hanging_curtain','both'):
        toe=context.hanging_curtain_toe_depth_m;water=context.aquifer_top_or_water_depth_m
        gamma=context.submerged_unit_weight_kn_m3;delta=context.confined_head_difference_m
        if context.homogeneous_aquifer is False:
            # Heterogeneous strata require a separate hydraulic boundary model.
            # Do not force unrelated homogeneous C.0.2 quantities as prerequisites.
            if context.numerical_seepage is None:
                checks.append(_result('seepage_critical_gradient','Appendix C C.0.2','missing_input',
                    requiredInputs=['groundwater_check.numerical_seepage'],
                    reason='非均质含水层需要数值域边界、水头、各层渗透性和浮重度'))
            else:
                from .hydraulic_assurance import hydraulic_assurance
                item=context.numerical_seepage
                if item.curtain_embedment_below_pit_m<=0 or item.curtain_embedment_below_pit_m>=item.depth_below_pit_m:
                    checks.append(_result('seepage_critical_gradient','Appendix C C.0.2','missing_input',
                         requiredInputs=['numerical_seepage.curtain_embedment_below_pit_m'],
                         reason='截水帷幕趾部应位于渗流计算域内'))
                else:
                    domain={'widthInsideM':item.width_inside_m,
                      'widthOutsideM':item.width_outside_m,
                      'depthBelowPitM':item.depth_below_pit_m,
                      'curtainEmbedmentBelowPitM':item.curtain_embedment_below_pit_m,
                      'insideHeadM':item.inside_total_head_m,
                      'outsideHeadM':item.outside_total_head_m,
                      'waterGammaKnM3':wg,
                      'layers':[{'topM':l.top_m,'bottomM':l.bottom_m,
                        'khMPerS':l.kh_m_s,'kvMPerS':l.kv_m_s,
                        'gammaSubKnM3':l.gamma_sub_kn_m3} for l in item.layers]}
                    numerical=hydraulic_assurance(domain,safety_grade=model.safety_grade,
                       boundary_verified=(item.far_field_boundary_verified and item.bottom_boundary_verified
                           and bool(item.hydraulic_evidence)),
                       convergence_limit=item.mesh_refinement_limit)
                    assurance_status=numerical['status']
                    # Preserve the existing field contract for clients that
                    # identify which numerical method ran. The new independent
                    # decision is carried separately and cannot be confused
                    # with a regulatory PASS.
                    numerical['assuranceStatus']=assurance_status
                    numerical['status']='numerical_screening_only' if assurance_status!='solver_failure' else 'solver_failure'
                    status=('fail' if assurance_status=='fail_screening' else
                       'not_calculated' if assurance_status=='solver_failure' else
                       'screening_only')
                    checks.append(_result('seepage_critical_gradient','Appendix C C.0.2',status,
                       stageId=stage.id,numerical=numerical,
                       reason='非均质渗流须开展独立边界与模型核定；本数值筛查不得自动变成规范通过'))
        elif context.homogeneous_aquifer is True:
            if (toe is None or water is None or gamma is None or delta is None or toe<=h or water>h or delta<=0):
                checks.append(_result('seepage_critical_gradient','Appendix C C.0.2','missing_input',
                    requiredInputs=['hanging_curtain_toe_depth_m','aquifer_top_or_water_depth_m',
                        'submerged_unit_weight_kn_m3','confined_head_difference_m']))
            else:
                D=toe-h;D1=h-water;factor=(2*D+.8*D1)*gamma/(delta*wg)
                req={1:1.6,2:1.5,3:1.4}.get(model.safety_grade)
                checks.append(_result('seepage_critical_gradient','Appendix C C.0.2',
                  'missing_input' if req is None else ('pass' if factor>=req else 'fail'),
                  safetyFactor=factor,requiredFactor=req,curtainEmbedmentM=D,waterToPitM=D1,
                  headDifferenceM=delta,requiredInputs=['safety_grade'] if req is None else []))
        else:
            checks.append(_result('seepage_critical_gradient','Appendix C C.0.2','missing_input',
               requiredInputs=['groundwater_check.homogeneous_aquifer'],
               reason='未核定含水层是否满足均质公式适用条件'))

    return checks
