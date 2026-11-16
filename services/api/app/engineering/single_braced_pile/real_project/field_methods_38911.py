"""Independent, bounded physical-input checks: earth loads, toe, two walls, water and 4.2.5.

Original PDF values remain DESIGN-REPORT facts. Field submissions prove the bytes
of the submission only; the original laboratory and engineering sign-off must
be separately authenticated before any professional design is released.
"""
from __future__ import annotations
from datetime import datetime
from math import cos, radians, sqrt, atan2, degrees, isfinite
from .field_evidence_38911 import finite
from .terrain_pressure import polyline_fill_increment, trapezoidal_fill_increment
from .stability_38910 import annex_c_checks, soft_bottom_pivot_425

STAGES=('S1','S2','S3','S4','S5')
SCHEMA='pitguard.luoyu-3-3-physical-source-check/3.89.11'


def report_geometry_method(reference:dict,source:dict|None=None)->dict:
    f=reference['facts'];t=f['terrain'];q=f['finiteAdjacentSurcharge']
    original={'profilePointsM':[[0,0],[t['benchWidthM'],0],
       [t['benchWidthM']+t['slopeWidthM'],t['slopeHeightM']]],
       'fillGammaKnM3':f['soilLayersOriginalGround'][0]['gammaNaturalKnM3'],
       'surcharge':{'nearM':q['nearDistanceM'],'widthM':q['widthM'],
         'alongStartM':-q['lengthM']/2,'alongEndM':q['lengthM']/2,
         'pressureKpa':q['pressureKpa']}}
    data=original if source is None else source
    if not isinstance(data,dict) or not {'profilePointsM','fillGammaKnM3','surcharge'}<=set(data):
        raise ValueError('Surveyed geometry requires terrain points, fill gamma and full adjacent load footprint')
    points=data['profilePointsM'];gamma=finite(data['fillGammaKnM3'],'fill gamma',10.,30.)
    stress=[]
    for z in (.3,1.,2.5,4.,6.):
        current=polyline_fill_increment(depth_below_wall_top_m=z,
          points_m=points,fill_gamma_kn_m3=gamma)
        stress.append({'depthLocalM':z,'terrainVerticalIncrementKpa':current['verticalIncrementKpa']})
    a=data['surcharge']
    if not isinstance(a,dict) or not {'nearM','widthM','alongStartM','alongEndM','pressureKpa'}<=set(a):
        raise ValueError('Surcharge footprint needs near/width/alongStart/alongEnd and pressure')
    near=finite(a['nearM'],'load near distance',0.,200.)
    width=finite(a['widthM'],'load width',.01,200.)
    start=finite(a['alongStartM'],'along start',-200.,200.)
    end=finite(a['alongEndM'],'along end',-200.,200.)
    load=finite(a['pressureKpa'],'surcharge pressure',0.,300.)
    if end<=start or not start<=0<=end:
        raise ValueError('Actual section must intersect finite neighboring load footprint')
    from app.section.geotech_actions import rectangle_vertical_increment
    for row in stress:
        row['neighborVerticalIncrementKpa']=rectangle_vertical_increment(
            load,near,near+width,start,end,row['depthLocalM'])
        row['totalIncrementKpa']=row['terrainVerticalIncrementKpa']+row['neighborVerticalIncrementKpa']
    report_top=t['benchWidthM']+t['slopeWidthM']
    geometrically_different=(len(points)!=3 or any(abs(float(a)-float(b))>1e-6
          for p,qv in zip(points,original['profilePointsM']) for a,b in zip(p,qv))
          or any(abs(float(a[k])-float(original['surcharge'][k]))>1e-6
             for k in ('nearM','widthM','alongStartM','alongEndM','pressureKpa')))
    return {'schemaVersion':SCHEMA,'method':'piecewise_survey_or_report_surcharge_stress',
      'geometryBasis':'report_schematic' if source is None else 'uploaded_geometry_source_self_declared',
      'reportTerrain':original,'usedGeometry':data,
      'reportedPlateauFromWallM':report_top,
      'reportedGapBetweenPlateauAndSurchargeM':q['nearDistanceM']-report_top,
      'geometryDifferentFromOriginalReport':geometrically_different,
      'stations':stress,'horizontalActivePressureMapping':'requires_JGJ120_3.4.2_soil_layer_and_water_basis',
      'sameConditionsAsOriginal':False,'engineeringQualified':False}


def rock_socket_method(reference:dict,source:dict|None=None)->dict:
    f=reference['facts'];toe=f['pileTopOriginalGroundDepthM']+f['pileLengthM']
    intersections=[];z=0.
    for layer in f['soilLayersOriginalGround']:
        top=z;z+=layer['thicknessM']
        if '风化岩' in layer['name']:
            overlap=max(0., min(z,toe)-top)
            if overlap:
                intersections.append({'layer':layer['name'],'fromSiteDepthM':top,
                    'toSiteDepthM':min(z,toe),'intersectedLengthM':overlap})
    base={'schemaVersion':SCHEMA,'reportPileToeSiteDepthM':toe,
      'reportRockIntersectionTotalM':sum(x['intersectedLengthM'] for x in intersections),
      'reportRockIntersections':intersections,'reportRockClassAloneDoesNotSetStiffness':True,
      'engineeringQualified':False}
    if source is None:
        return dict(base,status='blocked_missing_measured_horizontal_and_rotational_stiffness',
              candidateToeElasticBoundary=None)
    if not isinstance(source,dict) or not {'pileToeSiteDepthM','horizontalKnPerM','rotationKnMPerRad',
                                           'stiffnessDerivation'}<=set(source):
        raise ValueError('Rock source requires toe datum, both stiffnesses and derivation')
    stated_toe=finite(source['pileToeSiteDepthM'],'rock test toe',0.,100.)
    if abs(stated_toe-toe)>1e-5:
        raise ValueError('Rock test belongs to different toe elevation / pile version')
    kh=finite(source['horizontalKnPerM'],'rock Khorizontal',0,1e12)
    kr=finite(source['rotationKnMPerRad'],'rock Krotation',0,1e12)
    if kh+kr<=0 or not isinstance(source['stiffnessDerivation'],str) or len(source['stiffnessDerivation'].strip())<8:
        raise ValueError('Positive sourced rock stiffness and documented derivation required')
    return dict(base,status='conditional_stage_calculation_with_submitted_stiffness',
      candidateToeElasticBoundary={'horizontalKnPerM':kh,'rotationKnMPerRad':kr},
      measurementMethodReviewRequired=True,sourceDerivedFromRockName=False)


def brace_two_wall_method(reference:dict,source:dict|None=None)->dict:
    """EA/L and horizontal projection per pile; do not double-count reported K."""
    f=reference['facts']
    dims={'memberLengthM':14.,'memberWidthM':.8,'memberHeightM':.65,
          'angleDeg':45.,'baySpacingM':10.,'pileSpacingM':f['pileSpacingM'],
          'EConcreteMpaCandidate':30000.,'reportedNoMotionPointFactor':.5}
    E=dims['EConcreteMpaCandidate']*1000.
    k_member=E*dims['memberWidthM']*dims['memberHeightM']/dims['memberLengthM']
    cos2=cos(radians(dims['angleDeg']))**2
    projected=k_member*cos2*dims['pileSpacingM']/dims['baySpacingM']
    reported=f['strutStiffnessMNPerM2']*1000*f['pileSpacingM']
    base={'schemaVersion':SCHEMA,'source':'PDF physical page 33, structural material E(C30) still a candidate',
      'reportMemberGeometry':dims,'reportEquivalentSupportKnPerPileCandidate':reported,
      'memberEAoverLKnPerMBeforeTributaryScaling':k_member,
      'memberHorizontalKnPerPileUpperBound':projected,
      'reportedEquivalentIncludesUnknownWallAndJointCompliance':True,
      'jointAndColumnStiffnessNotModelled':True,'engineeringQualified':False}
    if reported<projected:
        base['oppositeWallKAlgebraicallyImpliedKnPerPileNotMeasured']=reported*projected/(projected-reported)
    else:
        base['reportVsMemberCompatibility']='reported equivalent greater than elastic member bound: investigate units and sources'
    if source is None:
        return dict(base,status='blocked_missing_opposite_wall_stage_stiffness_and_displacements',
                    stageInputs=None)
    if not isinstance(source,dict) or not {'stageFreeDisplacementMm','oppositeWallStiffnessKnPerPile',
            'materialEMpa','lengthM','widthM','heightM','angleDeg','baySpacingM'}<=set(source):
        raise ValueError('Two-wall input requires verified member dimensions, E and S2..S5 wall response')
    disp=source['stageFreeDisplacementMm']; stiffness=source['oppositeWallStiffnessKnPerPile']
    if not isinstance(disp,dict) or not isinstance(stiffness,dict) or set(disp)!=set(STAGES[1:]) or set(stiffness)!=set(STAGES[1:]):
        raise ValueError('Two-wall history must include all S2..S5 wall stiffness and movement')
    gap=finite(source['lengthM'],'brace length',.01,100.)
    width=finite(source['widthM'],'brace width',.01,10.)
    height=finite(source['heightM'],'brace height',.01,10.)
    e=finite(source['materialEMpa'],'brace material E',100.,200000.)
    angle=finite(source['angleDeg'],'brace angle',0.,79.)
    bay=finite(source['baySpacingM'],'brace bay spacing',.1,100.)
    values={k:finite(disp[k],k+' movement mm',-200,200)/1000 for k in STAGES[1:]}
    K={k:finite(stiffness[k],k+' stiffness kN/pile',1e-9,1e12) for k in STAGES[1:]}
    axial=e*1000*width*height/gap* (f['pileSpacingM']/bay)
    kproj=axial*cos(radians(angle))**2
    keff={s:kproj*K[s]/(kproj+K[s]) for s in STAGES[1:]}
    return dict(base,status='conditional_two_wall_staged_recalculation',
      memberAxialKnPerPile=axial,projectedKnPerPile=kproj,
      effectiveStiffnessKnPerPileByStage=keff,
      stageInputs={'brace_angle_deg':angle,'brace_axial_stiffness_kn_m':axial,
        'opposite_wall_stiffness_kn_m':K['S2'],
        'opposite_wall_stage_stiffness_kn_m':K,
        'opposite_wall_stage_free_displacements_m':values},
      stiffnessAndWallMotionMustBeIndependentlyVerified=True)


def groundwater_stage_method(reference:dict,source:dict|None=None)->dict:
    from .stability_38910 import annex_c_checks
    base={'schemaVersion':SCHEMA,'stages':[],
       'engineerHydraulicApplicabilityReviewRequired':True,
       'engineeringQualified':False}
    if source is None:
        return dict(base,status='blocked_no_measured_piezometric_heads_or_aquifer_profile',
              missingStages=list(STAGES))
    if not isinstance(source,dict) or source.get('hydraulicMechanism') not in {
          'confined_uncut','hanging_cutoff_homogeneous','none'}:
        raise ValueError('Groundwater requires explicit applicable hydraulic mechanism')
    mechanism=source['hydraulicMechanism']
    observations=source.get('observations')
    if not isinstance(observations,list) or len(observations)!=5:
        raise ValueError('Water monitoring requires one source observation per S1..S5')
    present=set()
    stage_by_id={s['id']:s for s in reference['stages']}
    timestamps=[]
    from datetime import datetime
    for row in observations:
        if not isinstance(row,dict) or not {'stageId','timestamp','insideDepthSiteM','outsideDepthSiteM'}<=set(row):
            raise ValueError('Water stage requires stage/timestamp/inside/outside measurements')
        sid=row['stageId']
        if sid not in STAGES or sid in present:
            raise ValueError('Repeated or unsupported water stage')
        present.add(sid)
        timestamp=row['timestamp']
        if not isinstance(timestamp,str): raise ValueError('Water observation timestamp must be ISO8601')
        try: parsed=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        except ValueError as exc:raise ValueError('Invalid water observation timestamp') from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('Hydro observation lacks timezone')
        timestamps.append((STAGES.index(sid),parsed.timestamp()))
        if not {'outsideMeasurementType','insideMeasurementType','outsideWellId','insideWellId'}<=set(row):
            raise ValueError('Water sources need both measurement types and identified wells')
        expected_out=('confined_piezometric' if mechanism=='confined_uncut' else 'phreatic')
        if mechanism!='none' and (row['outsideMeasurementType']!=expected_out
                  or row['insideMeasurementType']!='pit_water_level'):
            raise ValueError('Water observation source does not match declared aquifer mechanism')
        if not all(isinstance(row[k],str) and row[k].strip() for k in ('outsideWellId','insideWellId')):
            raise ValueError('Water observations require nonempty identified wells')
        inside=finite(row['insideDepthSiteM'],sid+' inside depth',-100.,200.)
        outside=finite(row['outsideDepthSiteM'],sid+' outside depth',-100.,200.)
        if inside<outside:
            raise ValueError('Water gradient reversal requires a separately qualified flow method')
        delta=inside-outside
        grade=source.get('safetyGrade','III')
        excavation=stage_by_id[sid]['siteExcavationDepthM']
        if mechanism=='confined_uncut':
            aquifer_top=finite(source.get('aquiferTopSiteDepthM'),'aquifer top',0.,100.)
            cover=aquifer_top-excavation
            gamma=finite(source.get('confiningGammaKnM3'),'cover gamma',10.,35.)
            calc=(annex_c_checks(scenario=mechanism,grade=grade,cover_thickness_m=cover,
                  cover_gamma_kn_m3=gamma,delta_head_m=delta)
                  if cover>0 else {'status':'blocked_aquifer_top_intersects_excavation',
                                    'conditionalFactor':None,'engineeringQualified':False})
        elif mechanism=='hanging_cutoff_homogeneous':
            cutoff_toe=finite(source.get('cutoffToeSiteDepthM'),'cutoff toe',0.,100.)
            d=cutoff_toe-excavation
            d1=max(0.,excavation-outside)
            g=finite(source.get('submergedGammaKnM3'),'submerged gamma',.01,30.)
            calc=(annex_c_checks(scenario=mechanism,grade=grade,cutoff_penetration_m=d,
                 water_table_offset_m=d1,submerged_gamma_kn_m3=g,delta_head_m=delta)
                 if d>0 else {'status':'blocked_cutoff_toe_not_below_excavation',
                                  'conditionalFactor':None,'engineeringQualified':False})
        else:
            calc=annex_c_checks(scenario='none',grade=grade)
        base['stages'].append({'stageId':sid,'measurementTimestamp':timestamp,
           'insideDepthSiteM':inside,'outsideDepthSiteM':outside,
           'headDifferenceM':delta,'excavationDepthSiteM':excavation,
           'conditionalHydraulicCheck':calc})
    if [x[1] for x in sorted(timestamps)] != sorted(x[1] for x in timestamps):
        raise ValueError('Water observation timestamps must increase in stage order')
    base['stages'].sort(key=lambda x:x['stageId'])
    return dict(base,status='conditional_stage_hydro_numeric_checks_not_site_approved',
               missingStages=[],hydraulicMechanism=mechanism)


def fixed_pivot_425_method(reference:dict,source:dict|None=None)->dict:
    base={'schemaVersion':SCHEMA,'method':'JGJ120-2012/4.2.5',
          'engineeringQualified':False,'stages':[]}
    if source is None:
        return dict(base,status='blocked_missing_pivot_centered_soft_soil_arc_geometry',
                    missingStages=['S2','S3','S4','S5'])
    if not isinstance(source,dict) or not isinstance(source.get('stageArcs'),dict):
        raise ValueError('Pivot evidence requires S2..S5 arcs')
    arcs=source['stageArcs']
    if set(arcs)!=set(STAGES[1:]):
        raise ValueError('Fixed pivot evidence must cover all supported stages')
    f=reference['facts']
    stages={s['id']:s for s in reference['stages']}
    from .normative_stability import soil_overburden
    for sid in STAGES[1:]:
        pivot=f['siteSupportDepthM'] if sid in {'S2','S3'} else f['siteReplacementLevelM']
        arc=arcs[sid]
        if not isinstance(arc,dict) or not {'centerXWallM','centerSiteDepthM','radiusM','strips'}<=set(arc):
            raise ValueError('Pivot arc requires center, radius and independent strips')
        cx=finite(arc['centerXWallM'],'circle x',-100,100)
        cz=finite(arc['centerSiteDepthM'],'circle z',0,100)
        radius=finite(arc['radiusM'],'circle R',.1,100)
        if abs(cx)>1e-5 or abs(cz-pivot)>1e-5:
            raise ValueError('4.2.5 arc center must exactly coincide with lowest active support')
        strips=arc['strips']
        if not isinstance(strips,list) or not 2<=len(strips)<=100:
            raise ValueError('Arc requires 2..100 source strips')
        cleaned=[]
        weight_gaps=[]
        for item in strips:
            if not isinstance(item,dict) or not {'centerXWallM','baseSiteDepthM','widthM','weightKnPerM','cohesionKpa','phiDeg'}<=set(item):
                raise ValueError('Strip needs coordinate, weight and soil strength')
            x=finite(item['centerXWallM'],'strip x',-radius,radius)
            z=finite(item['baseSiteDepthM'],'strip z',0,100.)
            half=finite(item['widthM'],'strip width',1e-6,10.)/2
            if abs(x)+half>=radius:
                raise ValueError('Strip crosses arc vertical asymptote')
            expected=pivot+sqrt(radius*radius-x*x)
            if abs(z-expected)>.002:
                raise ValueError('Strip base lies off source-defined circle')
            cos_theta=sqrt(radius*radius-x*x)/radius
            theta=degrees(atan2(-x,sqrt(radius*radius-x*x)))
            row={'widthM':2*half,'arcLengthM':2*half/cos_theta,
                 'weightKnPerM':finite(item['weightKnPerM'],'strip weight',0,1e6),
                 'cohesionKpa':finite(item['cohesionKpa'],'strip c',0,500),
                 'phiDeg':finite(item['phiDeg'],'strip phi',0,45),
                 'thetaDeg':theta,'porePressureKpa':finite(item.get('porePressureKpa',0.),'pore',0,1000),
                 'surchargeKpa':finite(item.get('surchargeKpa',0.),'surcharge',0,500)}
            # Cross-reference the supplied strip weight against independent
            # integration of the REPORT layers and REPORT terrain; this
            # comparison is informative unless the strip author explicitly
            # declares the same report-based soil column convention.
            h=stages[sid]['siteExcavationDepthM']
            if x>=0:
                soil_top=h
            else:
                from .stability_38910 import _surface_depth
                soil_top=_surface_depth(x,h,f['terrain'],f['pileTopOriginalGroundDepthM'])
            report_weight=soil_overburden(f,soil_top,z)*row['widthM'] if z>soil_top else 0.
            discrepancy=row['weightKnPerM']-report_weight
            weight_gaps.append({'stripCenterXWallM':x,'soilTopSiteDepthM':soil_top,
                 'reportBasedIndependentWeightKnPerM':report_weight,
                 'submittedWeightKnPerM':row['weightKnPerM'],
                 'weightDifferenceKnPerM':discrepancy,
                 'methodAndWaterBasisStillNeedReview':True})
            if source.get('weightBasis')=='report_layer_natural_unit_weight' and abs(discrepancy) > max(.05,.02*max(1.,report_weight)):
                raise ValueError('Strip declares report-based weights but contradicts independent soil-column integration')
            cleaned.append(row)
        if any(row['porePressureKpa']>0 for row in cleaned):
            outcome={'status':'blocked_effective_stress_groundwater_method_required',
                     'conditionalFactor':None,'engineeringQualified':False}
        else:
            outcome=soft_bottom_pivot_425(slices=cleaned,stage_id=sid,
                    excavation_base_soil_class='soft',support_depth_m=pivot)
        base['stages'].append({'stageId':sid,'verifiedPivotGeometryAgainstSourceNumbers':True,
                 'pivotSiteDepthM':pivot,'sourceArcRadiusM':radius,
                 'sourceStripsCount':len(cleaned),
                 'independentReportSoilWeightComparison':weight_gaps,
                 'conditionalCheck':outcome})
    return dict(base,status='conditional_staged_fixed_pivot_geometry_and_numeric_checks',
                missingStages=[])
