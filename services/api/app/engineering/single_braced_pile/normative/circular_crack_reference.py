"""JTG 3362-2018 circular eccentric-compression crack-width *reference*.

The bridge standard explicitly provides circular-section rho_te and steel
stress formulae (6.4.3--6.4.5). This module deliberately cannot certify a
JGJ120 building excavation design. Its load combination is the JTG frequent
combination, not silently translated from GB/T50010 quasi-permanent SLS.
"""
from __future__ import annotations
from math import isfinite, pi

SOURCE='https://xxgk.mot.gov.cn/jigou/glj/202006/P020230330561291441977.pdf'


def circular_bridge_width_reference(*,diameter_mm:float, bar_count:int, bar_diameter_mm:float,
    bar_axis_cover_mm:float, steel_modulus_mpa:float, frequent_axial_compression_kn:float,
    frequent_moment_knm:float, effective_length_m:float, long_term_ratio:float,
    steel_surface:str, project_width_limit_mm:float, combination_basis:str,
    geometry_basis:str)->dict:
    values=(diameter_mm,bar_diameter_mm,bar_axis_cover_mm,steel_modulus_mpa,
        frequent_axial_compression_kn,frequent_moment_knm,effective_length_m,
        long_term_ratio,project_width_limit_mm)
    if (not all(isfinite(float(x)) for x in values) or bar_count<6 or bar_count>80
            or diameter_mm<=0 or bar_diameter_mm<=0 or steel_modulus_mpa<=0
            or frequent_axial_compression_kn<=0 or frequent_moment_knm<0
            or effective_length_m<=0 or project_width_limit_mm<=0
            or long_term_ratio<0 or long_term_ratio>1):
        raise ValueError('invalid_circle_bridge_reference_inputs')
    if steel_surface not in {'deformed','plain','epoxy_deformed'}:
        raise ValueError('unsupported_steel_surface')
    if len(combination_basis.strip())<12 or len(geometry_basis.strip())<12:
        raise ValueError('explicit_source_basis_required')
    r=diameter_mm/2
    if not 0<bar_axis_cover_mm<r/2:
        raise ValueError('circular_effective_tension_ring_outside_scope')
    rs=r-bar_axis_cover_mm
    As=bar_count*pi*bar_diameter_mm**2/4
    rho=As/(pi*r*r)
    e0=frequent_moment_knm*1e6/(frequent_axial_compression_kn*1000)
    e_ratio=e0/r
    slenderness=effective_length_m*1000/(2*r)
    # JTG 3362-2018 6.4.3 exempts e0/r <= 0.55 within BRIDGE scope.
    # Do not calculate the nonapplicable sigma_ss formula or divide by e0=0.
    if e_ratio<=.55:
        return {'schemaVersion':'pitguard.circular-reference-width/1',
            'status':'reference_not_required_bridge_scope',
            'method':'JTG3362-2018_6.4.3_6.4.4-9_6.4.5_circle',
            'source':SOURCE,'projectMethodApproved':False,
            'smallEccentricityBridgeException':True,
            'e0Mm':e0,'radiusMm':r,'frequentAxialCompressionKn':frequent_axial_compression_kn,
            'frequentMomentKnM':frequent_moment_knm,'effectiveLengthM':effective_length_m,
            'crackWidthReferenceMm':None,'withinReferenceLimit':None,
            'declaredProjectWidthLimitMm':project_width_limit_mm,
            'combinationBasis':combination_basis,'geometryBasis':geometry_basis,
            'GB50010CircularWidthComplianceProved':False,
            'formalDesignReady':False,'formalConstructionIssue':False,
            'limitations':['Bridge eccentricity exemption cannot be transferred to excavation design',
               'Second-order analysis and project-specific normal-service combinations remain unverified']}
    eta=(1. if slenderness<=14 else
         1+slenderness**2/(4000*(e0/(2*r-bar_axis_cover_mm))))
    effective_e_ratio=eta*e_ratio
    if not isfinite(effective_e_ratio) or effective_e_ratio<=0:
        raise ValueError('invalid_jtg_6_4_4_eccentricity')
    C1={'deformed':1.,'plain':1.4,'epoxy_deformed':1.15}[steel_surface]
    C2=1+.5*long_term_ratio
    C3=.75
    r1=r-2*bar_axis_cover_mm
    beta=(.4+2.5*rho)*(1+.353/(effective_e_ratio**2))
    rho_te_unclipped=beta*As/(pi*(r*r-r1*r1))
    rho_te=max(.01,min(.1,rho_te_unclipped))
    # Ministry of Transport original PDF p.64: (eta_s * e0 / r - 0.1)^3.
    # A popular HTML transcription omits -0.1 and is NOT used as the source.
    stress_factor=.6*(effective_e_ratio-.1)**3 / ((.45+.26*rs/r)*(effective_e_ratio+.2)**2)
    steel_stress=stress_factor*frequent_axial_compression_kn*1000/As
    c=min(50.,bar_axis_cover_mm-bar_diameter_mm/2)
    if c<=0:raise ValueError('invalid_clear_cover_in_reference')
    width=C1*C2*C3*steel_stress/steel_modulus_mpa * (c+bar_diameter_mm)/(.3+1.4*rho_te)
    if not isfinite(width) or width<0:
        raise ValueError('invalid_computed_width')
    return {'schemaVersion':'pitguard.circular-reference-width/1',
        'status':'reference_calculated',
        'method':'JTG3362-2018_6.4.3_6.4.4-9_6.4.5_circle',
        'source':SOURCE,'projectMethodApproved':False,
        'frequencyCombinationRequired':True,
        'frequentAxialCompressionKn':frequent_axial_compression_kn,
        'frequentMomentKnM':frequent_moment_knm,
        'effectiveLengthM':effective_length_m,
        'e0Mm':e0,'eta':eta,'radiusMm':r,'steelRadiusMm':rs,
        'steelStressMpa':steel_stress,'steelAreaMm2':As,
        'rho':rho,'effectiveRho':rho_te,'unboundedEffectiveRho':rho_te_unclipped,
        'C1':C1,'C2':C2,'C3':C3,'coverUsedMm':c,
        'crackWidthReferenceMm':width,'declaredProjectWidthLimitMm':project_width_limit_mm,
        'withinReferenceLimit':width<=project_width_limit_mm,
        'smallEccentricityBridgeException':False,
        'combinationBasis':combination_basis,'geometryBasis':geometry_basis,
        'GB50010CircularWidthComplianceProved':False,
        'formalDesignReady':False,'formalConstructionIssue':False,
        'limitations':['Applies under JTG 3362 bridge frequent load combination; not auto-adopted for JGJ 120 projects',
            'Reference equation assumes circular eccentric compression, suitable material and reinforcement',
            'Cracking from shrinkage, construction joints, restraint and durability not covered',
            'Independent project method approval and professional checker remain mandatory']}


def circular_width_reference_matrix(model,result_stages):
    """Independent optional research study, never upgrades statutory crack check."""
    raw=(model.engineering_design or {}).get('circular_width_reference')
    if raw is None:
        return {'status':'missing_input','requiredInputs':['engineering_design.circular_width_reference'],
            'cases':[],'formalDesignReady':False,'formalConstructionIssue':False}
    from app.section.engineering_models import CircularWidthReference
    spec=CircularWidthReference.model_validate(raw)
    by_stage={s['id']:s for s in result_stages}
    rows=[];missing=[]
    cage=model.cage
    if cage.steel_modulus_mpa is None:
        missing.append('cage.steel_modulus_mpa')
    for case in spec.cases:
        stage=by_stage.get(case.stage_id)
        if not stage or stage.get('status')!='success':
            missing.append(f'stages.{case.stage_id}.successful_points');continue
        matched=[p for p in stage.get('points',[]) if
                 p['side']==case.side and abs(p['depthM']-case.depth_m)<=1e-8]
        if len(matched)!=1:
            missing.append(f'stages.{case.stage_id}.station_at_depth_{case.depth_m}_{case.side}');continue
        expected=matched[0]['momentKnM']*case.frequent_effect_scale
        if abs(expected-case.frequent_moment_knm)>max(.001,abs(expected)*1e-7):
            missing.append(f'circular_width_reference.{case.stage_id}.moment_not_same_station');continue
        if cage.steel_modulus_mpa is None:continue
        output=circular_bridge_width_reference(diameter_mm=model.pile.diameter_m*1000,
            bar_count=cage.bar_count,bar_diameter_mm=cage.bar_diameter_mm,
            bar_axis_cover_mm=cage.bar_axis_cover_mm,
            steel_modulus_mpa=cage.steel_modulus_mpa,
            frequent_axial_compression_kn=case.axial_frequent_kn,
            frequent_moment_knm=abs(case.frequent_moment_knm),
            effective_length_m=case.effective_length_m,
            long_term_ratio=case.long_term_effect_ratio,
            steel_surface=spec.steel_surface,
            project_width_limit_mm=spec.project_width_limit_mm,
            combination_basis=case.combination_basis,geometry_basis=spec.geometry_basis)
        rows.append({'stageId':case.stage_id,'depthM':case.depth_m,'side':case.side,
                     'sourcePointer':'/snapshot/engineering_design/circular_width_reference',
                     **output})
    return {'schemaVersion':'pitguard.circular-reference-width-matrix/1',
       'status':'missing_input' if missing else 'outside_method_scope',
       'assessment':'bridge_reference_only_not_project_code_compliance',
       'cases':rows,'requiredInputs':sorted(set(missing)),
       'anyReferenceLimitExceeded':any(r['withinReferenceLimit'] is False for r in rows),
       'projectMethodApproved':False,'engineeringQualified':False,'formalDesignReady':False,
       'formalConstructionIssue':False}
