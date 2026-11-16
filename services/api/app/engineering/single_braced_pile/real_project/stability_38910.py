"""JGJ120-2012 soil-stability research checks with explicit applicability gates.

4.2.3 overall circular method: direct clause applies anchored/cantilever/
double-row retaining systems, not the Luoyu one-strut wall. Circular searches
here are OPTIONAL diagnostics, not a formal 4.2.3 certificate.
4.2.5 soft-bottom fixed-pivot sum uses separately supplied slice geometry.
Appendix C water modes require their corresponding hydrogeological conditions.
"""
from __future__ import annotations
from math import sqrt,pi,cos,sin,tan,radians,hypot,isfinite
from .normative_stability import layer_at_site,soil_overburden

SCHEMA='pitguard.stability-independent-slices-and-water/3.89.10'


def _number(v,name,low=0.):
    if isinstance(v,bool): raise ValueError(name+' must be numeric')
    try: x=float(v)
    except (ValueError,TypeError) as exc: raise ValueError(name+' must be numeric') from exc
    if not isfinite(x) or x<low: raise ValueError(name+' outside physical range')
    return x


def _safety_from_slices(slices, *, clause, min_grade_iii, numerator_pore=False):
    if not isinstance(slices,list) or len(slices)<2:
        raise ValueError('Slip surface needs two or more independently defined strips')
    resistance=drive=0.
    evaluated=[]
    for i,s in enumerate(slices):
        for key in ('widthM','arcLengthM','weightKnPerM','cohesionKpa','phiDeg','thetaDeg'):
            if key not in s:raise ValueError('Missing independent strip field: '+key)
        b=_number(s['widthM'],'width',1e-9)
        length=_number(s['arcLengthM'],'length',b)
        w=_number(s['weightKnPerM'],'weight',0.)
        c=_number(s['cohesionKpa'],'cohesion')
        phi=_number(s['phiDeg'],'phi')
        angle=float(s['thetaDeg'])
        if phi>45 or not isfinite(angle) or abs(angle)>=85:
            raise ValueError('Invalid friction or steep slip geometry')
        q=_number(s.get('surchargeKpa',0.),'surcharge')
        u=_number(s.get('porePressureKpa',0.),'pore pressure')
        if not numerator_pore and u>0:
            raise ValueError("This declared natural-weight strip method has no pore pressure term; provide a separately qualified effective-stress method")
        if abs(length-b/cos(radians(angle)))>1e-4*max(1,length):
            raise ValueError('Strip arc length is inconsistent with base inclination')
        total=w+q*b
        normal=total*cos(radians(angle))-(u*length if numerator_pore else 0.)
        friction=max(0.,normal)*tan(radians(phi))
        local_resistance=c*length+friction
        local_drive=-total*sin(radians(angle))
        resistance+=local_resistance
        drive+=local_drive
        evaluated.append({'stripIndex':i,'resistingKnPerM':local_resistance,
             'drivingSignedKnPerM':local_drive,'normalEffectiveKnPerM':normal})
    if drive<=1e-8:
        return {'clause':clause,'status':'blocked_no_admissible_positive_downhill_driving_moment',
          'conditionalFactor':None,'drivingSignedKnPerM':drive,'resistingKnPerM':resistance,
          'engineeringQualified':False,'stripSummaries':evaluated}
    return {'clause':clause,'status':'calculated_research_only',
          'conditionalFactor':resistance/drive,'drivingSignedKnPerM':drive,
          'resistingKnPerM':resistance,'thresholdGradeIIIIfApplicable':min_grade_iii,
          'numericalScreenAboveGradeIII':resistance/drive>=min_grade_iii,
          'engineeringQualified':False,'stripSummaries':evaluated}


def soft_bottom_pivot_425(*,slices:list[dict]|None=None, stage_id:str|None=None,
                          excavation_base_soil_class:str|None=None,
                          support_depth_m:float|None=None,
                          geometry_evidence_sha256:str|None=None)->dict:
    base={'clause':'JGJ120-2012/4.2.5','schemaVersion':SCHEMA,
        'stageId':stage_id,'supportDepthM':support_depth_m,'applicableWhen':'supported_excavation_with_soft_soil_below_pit_bottom',
        'centerMustCoincideWith':'lowest_active_support_level',
        'engineeringQualified':False}
    if excavation_base_soil_class is None or support_depth_m is None:
        return dict(base,status='blocked_missing_soft_layer_classification_or_support_level',conditionalFactor=None)
    if excavation_base_soil_class not in ('soft','other'):
        raise ValueError('Unknown explicit soil classification')
    _number(support_depth_m,'support depth',1e-8)
    if excavation_base_soil_class=='other':
        return dict(base,status='not_applicable_under_declared_non_soft_soil',conditionalFactor=None)
    if not slices:
        return dict(base,status='blocked_missing_fixed_pivot_slip_strip_geometry',conditionalFactor=None)
    if geometry_evidence_sha256 is not None:
        import re
        if not isinstance(geometry_evidence_sha256,str) or re.fullmatch(r'[a-f0-9]{64}',geometry_evidence_sha256) is None:
            raise ValueError('Independent pivot-geometry evidence SHA-256 must be 64 lowercase hexadecimal characters')
    result=_safety_from_slices(slices,clause='JGJ120-2012/4.2.5',min_grade_iii=1.7)
    return dict(base,**{k:v for k,v in result.items() if k not in ('clause','status')},
        geometryEvidenceSha256=geometry_evidence_sha256,
        geometryEvidenceBytesAuthenticated=False,
        status=('blocked_no_verified_pivot_geometry' if geometry_evidence_sha256 is None
                and result['conditionalFactor'] is not None else result['status']))


def _surface_depth(x:float,excavation_depth:float,terr:dict,wall_top_depth:float):
    # x<0 outside, x>0 excavated side. Original report has a bench and a ramp.
    if x>=0:return excavation_depth
    distance=-x
    if distance<=terr['benchWidthM']:return wall_top_depth
    if distance<=terr['benchWidthM']+terr['slopeWidthM']:
        return wall_top_depth*(1-(distance-terr['benchWidthM'])/terr['slopeWidthM'])
    return 0.


def _circle_slices(facts:dict,depth:float,left:float,right:float,offset:float,
                   n:int=50)->list[dict]|None:
    terr=facts['terrain'];wall_top=facts['pileTopOriginalGroundDepthM']
    xl,xr=-left,right
    yl=-_surface_depth(xl,depth,terr,wall_top)
    yr=-_surface_depth(xr,depth,terr,wall_top)
    chord=hypot(xr-xl,yr-yl)
    # Circular centre on the uphill side of the chord. Exposed centre and R
    # are *geometry assumptions*, not a hidden numerical calibration.
    cx=(xl+xr)/2+offset*(yr-yl)/chord*-1
    cy=(yl+yr)/2+offset*(xr-xl)/chord
    r=hypot(cx-xl,cy-yl)
    slices=[];dx=(xr-xl)/n
    for i in range(n):
        x=xl+(i+.5)*dx
        rad=r*r-(x-cx)**2
        if rad<=0:return None
        ybase=cy-sqrt(rad)
        topdepth=_surface_depth(x,depth,terr,wall_top)
        zbase=-ybase
        if zbase<=topdepth+1e-6:return None
        sin_theta=(x-cx)/r
        cos_theta=sqrt(rad)/r
        if cos_theta<.05:return None
        weight=soil_overburden(facts,topdepth,zbase)*dx
        layer=layer_at_site(facts,zbase)
        # Real adjacent rectangle is NOT a whole-domain surcharge: only add
        # its declared finite 4.3..16.3m horizontal influence footprint.
        adj=facts['finiteAdjacentSurcharge'];at=-x
        q=adj['pressureKpa'] if (at>=adj['nearDistanceM'] and
                                at<=adj['nearDistanceM']+adj['widthM']) else 0.
        theta=__import__('math').degrees(__import__('math').asin(sin_theta))
        slices.append({'widthM':dx,'arcLengthM':dx/cos_theta,
           'weightKnPerM':weight,'cohesionKpa':layer['cKpa'],
           'phiDeg':layer['phiDeg'],'thetaDeg':theta,'surchargeKpa':q,
           'porePressureKpa':0.,'baseDepthM':zbase})
    return slices


def overall_circular_search(facts:dict,excavation_depth_m:float,
                            *,max_candidates:int=144,number_of_slices:int=40)->dict:
    h=_number(excavation_depth_m,'excavation depth',.01)
    if not 10<=number_of_slices<=100 or not 1<=max_candidates<=300:
        raise ValueError('Circular search resolution outside tested research budget')
    results=[];rejected=0
    for left in (4.,6.,8.,10.,12.,16.):
        for right in (3.,5.,8.,12.):
            for ratio in (.30,.55,.85,1.2,1.8,2.5):
                if len(results)+rejected>=max_candidates:break
                sl=_circle_slices(facts,h,left,right,(left+right)*ratio,n=number_of_slices)
                if not sl:
                    rejected+=1;continue
                res=_safety_from_slices(sl,clause='JGJ120-2012/4.2.3 research analogue',
                     min_grade_iii=1.25)
                if res['conditionalFactor'] is None:
                    rejected+=1;continue
                results.append({'leftReachM':left,'rightReachM':right,'circleOffsetM':(left+right)*ratio,
                    'factor':res['conditionalFactor'],'resistingKnPerM':res['resistingKnPerM'],
                    'drivingKnPerM':res['drivingSignedKnPerM']})
    critical=min(results,key=lambda x:x['factor']) if results else None
    return {'schemaVersion':SCHEMA,'clause':'JGJ120-2012/4.2.3',
       'originalSystemType':'one-strut supported piles',
       'directClauseApplicability':'not_directly_specified_for_this_support_type',
       'method':'ordinary_circular_slices_with_piecewise_terrain_conditional',
       'slicesPerCircle':number_of_slices,'candidateCount':len(results),
       'rejectedGeometryCount':rejected,'searchBudgetCandidates':max_candidates,
       'critical':critical,'status':('supplementary_screen_unverified_water_and_3d_geometry'
           if critical else 'blocked_no_admissible_circle_in_search_domain'),
       'noGuaranteeGlobalMinimum':True,'porePressureModel':'omitted_unverified',
       'engineeringQualified':False}


def annex_c_checks(*,scenario:str,grade:str='III',cover_thickness_m:float|None=None,
                  cover_gamma_kn_m3:float|None=None,delta_head_m:float|None=None,
                  cutoff_penetration_m:float|None=None,water_table_offset_m:float|None=None,
                  submerged_gamma_kn_m3:float|None=None,water_gamma_kn_m3:float=10.,
                  groundwater_evidence_sha256:str|None=None,
                  grading_discontinuous:bool|None=None)->dict:
    """C.0.1 and C.0.2 independent formula; no hydrogeological certificate."""
    if grade not in {'I','II','III'}:raise ValueError('Unsupported safety grade')
    if scenario not in {'none','confined_uncut','hanging_cutoff_homogeneous','piping_screen'}:
        raise ValueError('Unknown hydraulic mechanism')
    result={'schemaVersion':SCHEMA,'scenario':scenario,
            'clause':None,'conditionalFactor':None,'numericallyAboveThreshold':None,
            'hydrogeologicalEvidenceSha256':groundwater_evidence_sha256,
            'engineeringQualified':False}
    if scenario=='none':
        return dict(result,status='blocked_need_hydrogeological_applicability_assessment')
    if scenario=='piping_screen':
        if grading_discontinuous is None:
            return dict(result,clause='JGJ120-2012/C.0.3',status='blocked_missing_grain_size_distribution')
        return dict(result,clause='JGJ120-2012/C.0.3',
                    status=('requires_piping_susceptibility_assessment' if grading_discontinuous
                           else 'screened_not_discontinuous_by_declared_gradation'))
    head=_number(delta_head_m,'head difference',0.) if delta_head_m is not None else None
    gammaw=_number(water_gamma_kn_m3,'water unit weight',1e-9)
    if scenario=='confined_uncut':
        result['clause']='JGJ120-2012/C.0.1'
        if None in (cover_thickness_m,cover_gamma_kn_m3,head):
            return dict(result,status='blocked_missing_confined_aquifer_or_piezometric_head')
        d=_number(cover_thickness_m,'confining thickness',1e-9)
        gamma=_number(cover_gamma_kn_m3,'cover gamma',1e-9)
        k=d*gamma/((d+head)*gammaw)
        bound=1.1
    else:
        result['clause']='JGJ120-2012/C.0.2'
        if None in (cutoff_penetration_m,water_table_offset_m,submerged_gamma_kn_m3,head):
            return dict(result,status='blocked_missing_hanging_cutoff_or_homogeneous_aquifer')
        d=_number(cutoff_penetration_m,'cutoff below pit',1e-9)
        d1=_number(water_table_offset_m,'water table offset')
        gamma=_number(submerged_gamma_kn_m3,'submerged gamma',1e-9)
        if head==0:return dict(result,status='blocked_zero_head_gradient_use_hydrogeological_explanation')
        k=(2*d+.8*d1)*gamma/(head*gammaw)
        bound={'I':1.6,'II':1.5,'III':1.4}[grade]
    result.update(conditionalFactor=k,threshold=bound,numericallyAboveThreshold=k>=bound,
        status='conditional_numeric_reference_unverified_source_and_applicability',
        methodApplicabilityUnverified=True)
    return result


def extended_stability_review(facts:dict,stages:list[dict], *,search_budget:int=60)->dict:
    results=[]
    for s in stages:
        h=s['siteExcavationDepthM']
        if h is None or h<=0:raise ValueError('Missing excavation depth')
        layer=layer_at_site(facts,h+.02)
        is_soft=(layer['name']=='淤泥')
        pivot=soft_bottom_pivot_425(slices=None,stage_id=s['id'],
           excavation_base_soil_class='soft' if is_soft else 'other',
           support_depth_m=(facts['siteSupportDepthM'] if s['id'] in {'S2','S3'} else
                 (facts['siteReplacementLevelM'] if s['id'] in {'S4','S5'} else None)) )
        if s['id']=='S1':
            pivot['status']='not_applicable_cantilever_stage_no_support_pivot'
        results.append({'stageId':s['id'],
           'overallSearch':overall_circular_search(facts,h,max_candidates=search_budget),
           'softPivot425':pivot,
           'hydraulicAnnexC':annex_c_checks(scenario='none',grade='III')})
    return {'schemaVersion':SCHEMA,'project': 'Luoyu 3-3',
       'stageResults':results,'supportsFullStabilityCertification':False,
       'actualPorePressureAndSeepageDataPresent':False,
       'methodsIndependentOfElasticBeam':True,
       'engineeringQualified':False,'formalConstructionIssue':False}
