"""Evidence-led five-stage research reconstruction of Luoyu 3-3.

No original nodal arrays exist and several original stiffness/soil method
options are not documented. This adapter builds an *explicitly conditional*
standalone calculation, including actual reported slope, finite rectangle,
mixed pressure bases, local elevation, crown, first support and replacement.
It must never be labelled same-condition validation or substitute for a design.
"""
from __future__ import annotations
from math import ceil, pi, sin, sqrt, radians, tan, cos
from app.section.geotech_actions import rectangle_vertical_increment,strip_vertical_increment
from app.section.solver import solve_stages
from .terrain_pressure import trapezoidal_fill_increment,terrain_refinement,polyline_fill_increment
from .normative_actions_38910 import upper_slope_348,local_surcharge_347
from .bracing_compatibility import coupled_two_end_brace
from ..normative.reference_review import luoyu_three_three
from ..pressure import width as normative_pile_reaction_width


def _layer_at(layers,z):
    return next((l for l in layers if z<l['bottomSiteM']),layers[-1])


def _soil_column(layers,from_depth,to_depth,water_depth,water_gamma=9.81):
    total=effective=0.
    if to_depth<=from_depth:return (0.,0.)
    for lay in layers:
        a=max(from_depth,lay['topSiteM']); b=min(to_depth,lay['bottomSiteM'])
        if b<=a:continue
        dry=max(0,min(b,water_depth)-a)
        wet=max(0,b-max(a,water_depth))
        # gammaSat is an explicitly recorded saturation model assumption. No
        # covert max(natural, water+1) correction is allowed.
        total+=dry*lay['gamma']+wet*lay['gammaSat']
        effective+=dry*lay['gamma']+wet*(lay['gammaSat']-water_gamma)
    return total,effective


def _rankine_strength(lay,stress):
    c=lay['cohesion']; phi=radians(lay['phi']);ka=tan(pi/4-phi/2)**2;kp=1/ka
    return max(0,ka*stress-2*c*sqrt(ka)),kp*stress+2*c*sqrt(kp),ka


def _terrain_increment(z,geo,layers,fill_gamma_kn_m3):
    # Strict integration of the ramp shape and infinite plateau as an explicit
    # additional vertical surcharge. The separate lateral-pressure mapping
    # remains a research hypothesis (Rankine effective/combined layerwise).
    return trapezoidal_fill_increment(
        depth_below_wall_top_m=z,bench_m=geo['benchWidthM'],
        ramp_m=geo['slopeWidthM'],fill_height_m=geo['slopeHeightM'],
        fill_gamma_kn_m3=fill_gamma_kn_m3)['verticalIncrementKpa']


def _stations(nodes):
    r=sqrt(3/5)
    gauss=((-r,5/9),(0.,8/9),(r,5/9))
    stations=[]
    for e,(a,b) in enumerate(zip(nodes,nodes[1:])):
        for i,(x,w) in enumerate(gauss):
            zz=(a+b)/2+x*(b-a)/2
            stations.append((f'{e}:{i}:0',e,zz,w*(b-a)/2*(1-x)/2))
            stations.append((f'{e}:{i}:1',e+1,zz,w*(b-a)/2*(1+x)/2))
    return stations


def produce_luoyu_research_curves(*, elastic_modulus_mpa=30000.,
                                 inertia_factor=1.,crown_multiplier=1.,
                                 support_multiplier=1.,mesh_max_m=.20,
                                 toe_boundary='free',terrain_fill_gamma_kn_m3=18.,
                                 opposite_wall_stiffness_kn_m=None,
                                 member_axial_stiffness_kn_m=None,
                                 left_wall_stiffness_kn_m=None,
                                 left_wall_free_displacement_m=None,
                                 right_wall_free_displacement_m=None,
                                 crown_stiffness_kn_m=None,
                                 saturated_gammas_kn_m3=None,
                                 exterior_water_depth_m=.5,
                                 adjacent_surcharge_kpa=30.,
                                 terrain_mode="elastic_vertical_stress",
                                 local_passive_cap=True,
                                 adjacent_load_mode="boussinesq_reference",
                                 terrain_cohesion_override_kpa=None,
                                 toe_elastic_horizontal_kn_m=None,
                                 toe_elastic_rotation_knm_rad=None,
                                 brace_angle_deg=None,
                                 brace_axial_stiffness_kn_m=None,
                                 opposite_wall_stage_free_displacements_m=None,
                                 opposite_wall_stage_stiffness_kn_m=None,
                                 terrain_profile_points_m=None,
                                 adjacent_rectangle_geometry_m=None):
    from ..analysis import canonical_hash
    ref=luoyu_three_three();fact=ref['facts'];geo=fact['terrain'];offset=fact['pileTopOriginalGroundDepthM']
    length=fact['pileLengthM'];diam=fact['pileDiameterM'];spacing=fact['pileSpacingM']
    if not 1e3<=elastic_modulus_mpa<=1e5 or not 0<inertia_factor<=1:
        raise ValueError('Material E and EI reduction must be physically bounded')
    if toe_boundary not in ('free','pinned','fixed'):
        raise ValueError('Toe boundary must be a declared free, pinned or fixed model')
    if not (0 <= exterior_water_depth_m <= 4) or not (0 <= adjacent_surcharge_kpa <= 80):
        raise ValueError("Water level and adjacent surcharge must stay within stated research bounds")
    if terrain_mode not in ("elastic_vertical_stress", "omitted_diagnostic", "jgj120_3_4_8"):
        raise ValueError("Unknown terrain pressure research model")
    if adjacent_load_mode not in {"boussinesq_reference", "jgj120_3_4_7"}:
        raise ValueError("Invalid adjacent load analysis method")
    if terrain_profile_points_m is not None:
        if terrain_mode != 'elastic_vertical_stress':
            raise ValueError('Survey polyline requires the elastic stress model; normative slope is a different method')
        polyline_fill_increment(depth_below_wall_top_m=.3,
            points_m=terrain_profile_points_m,fill_gamma_kn_m3=terrain_fill_gamma_kn_m3)
    if adjacent_rectangle_geometry_m is not None:
        if not isinstance(adjacent_rectangle_geometry_m, dict) or set(adjacent_rectangle_geometry_m) != {
                'nearM','widthM','alongStartM','alongEndM'}:
            raise ValueError('Survey load footprint needs near, width and both along-wall coordinates')
        from math import isfinite
        q=adjacent_rectangle_geometry_m
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in q.values()):
            raise ValueError('Survey load footprint must be finite numeric metres')
        if not (q['nearM']>=0 and q['widthM']>0 and q['alongStartM']<q['alongEndM']
                and q['alongStartM']<=0<=q['alongEndM']):
            raise ValueError('Section location must intersect the declared surcharge span')
    rock_values=(toe_elastic_horizontal_kn_m,toe_elastic_rotation_knm_rad)
    if any(x is not None for x in rock_values):
        if toe_boundary != 'free' or not all(x is not None for x in rock_values):
            raise ValueError("Toe elastic rock springs need a free toe and both stiffnesses")
        if min(rock_values)<0 or max(rock_values)>1e12 or sum(rock_values)==0:
            raise ValueError("Toe elastic rock stiffness must be nonnegative and nontrivial")
    brace_values=(brace_angle_deg,brace_axial_stiffness_kn_m,opposite_wall_stiffness_kn_m,
                  opposite_wall_stage_free_displacements_m)
    have_brace=any(x is not None for x in brace_values)
    if opposite_wall_stage_stiffness_kn_m is not None:
        if not isinstance(opposite_wall_stage_stiffness_kn_m,dict) or set(opposite_wall_stage_stiffness_kn_m)!={'S2','S3','S4','S5'}:
            raise ValueError('Opposite-wall stiffness history requires every brace stage S2..S5')
        from math import isfinite
        if any(isinstance(k,bool) or not isinstance(k,(int,float)) or not isfinite(k)
               or not 0<k<=1e12 for k in opposite_wall_stage_stiffness_kn_m.values()):
            raise ValueError('Opposite-wall stage K must be positive and finite')
        if not have_brace:
            raise ValueError('Stage wall stiffness needs the full inclined two-wall mode')
    if have_brace:
        if not all(x is not None for x in brace_values):
            # Legacy two-end reference path is only allowed without a stage history.
            if brace_angle_deg is not None or opposite_wall_stage_free_displacements_m is not None:
                raise ValueError("Stage brace requires angle, EA/L, opposite-wall K and stage history")
            have_brace=False
        else:
            if not 0<=brace_angle_deg<80 or brace_axial_stiffness_kn_m<=0 or opposite_wall_stiffness_kn_m<=0:
                raise ValueError("Inclined brace angle and stiffness invalid")
            if set(opposite_wall_stage_free_displacements_m)!={'S2','S3','S4','S5'}:
                raise ValueError("Brace opposite-wall free displacement history requires S2..S5")
            from math import isfinite,cos
            if any(not isfinite(v) or abs(v)>.2 for v in opposite_wall_stage_free_displacements_m.values()):
                raise ValueError("Opposite-wall movement must be a bounded, finite displacement")
            if any(x is not None for x in (member_axial_stiffness_kn_m,left_wall_stiffness_kn_m,
                       left_wall_free_displacement_m,right_wall_free_displacement_m)):
                raise ValueError("Stage brace mode must not mix legacy static two-end assumption")
    if not isinstance(local_passive_cap, bool):
        raise ValueError("Passive cap flag must be boolean")
    if terrain_fill_gamma_kn_m3<=0:
        raise ValueError('Fill gamma must be a positive sourced engineering input')
    both=(opposite_wall_stiffness_kn_m,member_axial_stiffness_kn_m,
          left_wall_stiffness_kn_m,left_wall_free_displacement_m,
          right_wall_free_displacement_m)
    if not have_brace and any(v is not None for v in both) and not all(v is not None for v in both):
        raise ValueError('Two-end check requires both wall K, member EA/L and both free displacements')
    if crown_stiffness_kn_m is not None and crown_stiffness_kn_m<=0:
        raise ValueError('Explicit crown equivalent stiffness must be positive')
    if min(crown_multiplier,support_multiplier)<=0 or not .08<=mesh_max_m<=.4:
        raise ValueError('Explicit support multipliers and mesh required')
    source=fact['soilLayersOriginalGround'];layers=[];position=0.
    if saturated_gammas_kn_m3 is not None and len(saturated_gammas_kn_m3)!=len(source):
        raise ValueError('Each original soil layer requires a declared saturated unit weight')
    for i,l in enumerate(source):
        layers.append({'id':str(i),'name':l['name'],'topSiteM':position,
           'bottomSiteM':position+l['thicknessM'],'gamma':l['gammaNaturalKnM3'],
           'gammaSat':(saturated_gammas_kn_m3[i] if saturated_gammas_kn_m3 is not None
                        else l['gammaNaturalKnM3']),
           'cohesion':l['cKpa'],'phi':l['phiDeg'],'combined':l['stressBasis']=='combined',
           'm':1000*l['mMPaPerM2']})
        if layers[-1]['gammaSat']<=9.81:
            raise ValueError('Saturated unit weight must exceed water unit weight')
        position+=l['thicknessM']
    # Reconstruct in the local pile-top datum, explicitly retaining the site
    # mapping. Mesh boundaries include all soil boundaries and support events.
    anchors={0.,length,fact['siteSupportDepthM']-offset,
             fact['siteReplacementLevelM']-offset,2.05-offset,4.9-offset}
    for l in layers:
        local=l['bottomSiteM']-offset
        if 0<local<length:anchors.add(local)
    anchors=sorted(anchors);nodes=[anchors[0]]
    for a,b in zip(anchors,anchors[1:]):
        n=ceil((b-a)/mesh_max_m-1e-9)
        nodes.extend(round(a+(b-a)*j/n,10) for j in range(1,n+1))
    if len(nodes)>150:raise ValueError('Too many nodes for exploratory review')
    stations=_stations(nodes)
    # JGJ 120-2012 4.1.7: circular pile b0=0.9(1.5d+0.5) for d<=1m, capped by spacing.
    # The previous min(3d,1.5s) overstated this case by 50%.
    b0=normative_pile_reaction_width(diam,spacing)
    i_circ=pi*diam**4/64
    crown_k=(crown_stiffness_kn_m if crown_stiffness_kn_m is not None
             else 22.7*1000*spacing*crown_multiplier)
    support_k=fact['strutStiffnessMNPerM2']*1000*spacing*support_multiplier
    two_end=None
    if opposite_wall_stiffness_kn_m is not None and not have_brace:
        # Exact 2-node axial compatibility requires both wall stiffnesses.
        # The conditional wall model can ONLY consume a fixed opposite wall
        # incremental reference; time-varying opposite-wall excavation is not
        # simulated by this single-wall solver.
        two_end=coupled_two_end_brace(
          member_axial_stiffness_kn_m=member_axial_stiffness_kn_m,
          left_wall_stiffness_kn_m=left_wall_stiffness_kn_m,
          right_wall_stiffness_kn_m=opposite_wall_stiffness_kn_m,
          left_free_displacement_m=left_wall_free_displacement_m,
          right_free_displacement_m=right_wall_free_displacement_m)
        support_k=two_end['effectiveLeftWallSupportStiffnessKnM']
    stage_brace=None
    if have_brace:
        horizontal_brace_k=brace_axial_stiffness_kn_m*cos(radians(brace_angle_deg))**2
        brace_right_k=(opposite_wall_stage_stiffness_kn_m['S2']
            if opposite_wall_stage_stiffness_kn_m is not None else opposite_wall_stiffness_kn_m)
        support_k=horizontal_brace_k*brace_right_k/(horizontal_brace_k+brace_right_k)
        stage_brace={'angleDeg':brace_angle_deg,
             'memberAxialKnPerM':brace_axial_stiffness_kn_m,
             'projectedHorizontalKnPerM':horizontal_brace_k,
             'oppositeWallHorizontalKnPerM':opposite_wall_stiffness_kn_m,
             'leftWallEffectiveKnPerM':support_k,
             'oppositeWallFreeMotionHistoryM':opposite_wall_stage_free_displacements_m,
             'oppositeWallStiffnessByStageKnPerM':opposite_wall_stage_stiffness_kn_m,
             'method':'two_wall_series_compatibility_with_inclined_projection_reference',
             'conditionalModelOnly':True}
    support_node=nodes.index(round(fact['siteSupportDepthM']-offset,10))
    replace_node=nodes.index(round(fact['siteReplacementLevelM']-offset,10))
    stages_info=[
        ('S0','initial',0.,.5,None),
        ('S1','excavate',2.05,2.55,None),
        ('S2','install_original',2.05,2.55,'original'),
        ('S3','excavate',4.9,5.4,None),
        ('S4','install_replacement',4.9,5.4,'replacement'),
        ('S5','remove_original',4.9,5.4,'remove')]
    stages=[];exterior_water=exterior_water_depth_m
    for stage_id,event,site_h,site_inside_water,support_event in stages_info:
        hlocal=max(0,site_h-offset) if event!='initial' else 0.
        external=[0.]*len(nodes);springs=[]
        for identity,node,z,weight in stations:
            site_z=offset+z;layer=_layer_at(layers,site_z)
            total_out,effective_out=_soil_column(layers,offset,site_z,exterior_water)
            total_in,effective_in=_soil_column(layers,max(offset,site_h),site_z,site_inside_water)
            if event=='initial':
                q_terrain=q_adj=0.
            else:
                if terrain_mode=='elastic_vertical_stress':
                    if terrain_profile_points_m is None:
                        q_terrain=_terrain_increment(z,geo,layers,terrain_fill_gamma_kn_m3)
                    else:
                        q_terrain=polyline_fill_increment(
                            depth_below_wall_top_m=z,points_m=terrain_profile_points_m,
                            fill_gamma_kn_m3=terrain_fill_gamma_kn_m3)['verticalIncrementKpa']
                elif terrain_mode=='jgj120_3_4_8':
                    top=fact['soilLayersOriginalGround'][0]
                    ka_top=tan(pi/4-top['phiDeg']*pi/360)**2
                    candidate=upper_slope_348(depth_below_wall_top_m=z,
                        bench_m=geo['benchWidthM'],slope_width_m=geo['slopeWidthM'],
                        slope_height_m=geo['slopeHeightM'],
                        overlying_gamma_kn_m3=terrain_fill_gamma_kn_m3,
                        overlying_cohesion_kpa=(top['cKpa'] if terrain_cohesion_override_kpa is None
                            else terrain_cohesion_override_kpa),overlying_ka=ka_top)
                    if candidate['verticalIncrementKpa'] is None:
                        raise ValueError('JGJ120 3.4.8 upper-slope tensile zone invalid: confirm original slope strength/method')
                    q_terrain=candidate['verticalIncrementKpa']
                else:
                    q_terrain=0.
                adjacent=fact['finiteAdjacentSurcharge']
                footprint=adjacent_rectangle_geometry_m or {
                    'nearM':adjacent['nearDistanceM'], 'widthM':adjacent['widthM'],
                    'alongStartM':-adjacent['lengthM']/2,'alongEndM':adjacent['lengthM']/2}
                if adjacent_load_mode=='jgj120_3_4_7':
                    q_adj=local_surcharge_347(pressure_kpa=adjacent_surcharge_kpa,
                       distance_m=footprint['nearM'],width_m=footprint['widthM'],
                       length_m=footprint['alongEndM']-footprint['alongStartM'],
                       depth_m=z)['verticalIncrementKpa']
                else:
                    q_adj=rectangle_vertical_increment(adjacent_surcharge_kpa,
                        footprint['nearM'],footprint['nearM']+footprint['widthM'],
                        footprint['alongStartM'],footprint['alongEndM'],z)
            # Original terrain is a physical berm outside the recessed pile
            # top, so overburden starts at wall top and berm is a separate load.
            p_base=(total_out if layer['combined'] else effective_out)
            p_in=(total_in if layer['combined'] else effective_in)
            pa,pp,ka=_rankine_strength(layer,p_base+q_terrain+q_adj)
            k0=1-sin(radians(layer['phi']))
            p0=max(0.,k0*p_in)
            if event=='initial':
                pa=p0
            springs.append({'id':'outside:'+identity,'node':node,'depthM':z,'layerId':layer['id'],
                'weightM':weight,'side':'outside','sign':-1,'p0Kn':pa*spacing*weight,
                'kKnPerM':0.,'lowerKn':pa*spacing*weight,'upperKn':pa*spacing*weight})
            if event=='initial':
                # One equilibrium baseline by equal characteristic pressures.
                spring_p0=pa*spacing*weight
                springs.append({'id':'inside:'+identity,'node':node,'depthM':z,'layerId':layer['id'],
                    'weightM':weight,'side':'inside','sign':1,'p0Kn':spring_p0,
                    'kKnPerM':layer['m']*z*b0*weight,'lowerKn':0.,
                    'upperKn':pp*b0*weight if local_passive_cap else 1e9,
                    'passiveReferenceKn':pp*b0*weight})
            elif z>hlocal:
                p0_inside=p0*b0*weight
                springs.append({'id':'inside:'+identity,'node':node,'depthM':z,'layerId':layer['id'],
                    'weightM':weight,'side':'inside','sign':1,'p0Kn':p0_inside,
                    'kKnPerM':layer['m']*(z-hlocal)*b0*weight,'lowerKn':0.,
                    'upperKn':pp*b0*weight if local_passive_cap else 1e9,
                    'passiveReferenceKn':pp*b0*weight})
            # In combined layers do not add physical pore pressure again.
            if not layer['combined']:
                outer_pore=9.81*max(0,site_z-exterior_water)
                inner_pore=9.81*max(0,site_z-site_inside_water) if z>hlocal else 0.
                external[node]+=(outer_pore-inner_pore)*spacing*weight
        installs=[];removes=[]
        if event=='initial':
            installs=[{'id':'crown','node':0,'kind':'elastic','stiffnessKnPerM':crown_k,
               'compressionOnly':False,'preloadKn':0.}]
        elif support_event=='original':
            installs=[{'id':'temporary','node':support_node,'kind':'elastic',
                       'stiffnessKnPerM':support_k,'compressionOnly':False,'preloadKn':0.}]
        elif support_event=='replacement':
            installs=[{'id':'replacement','node':replace_node,'kind':'rigid',
                       'stiffnessKnPerM':None,'compressionOnly':False,'preloadKn':0.}]
        elif support_event=='remove':removes=['temporary']
        stage_data={'id':stage_id,'name':stage_id,'event':event,'externalForcesKn':external,
            'soilSprings':springs,'installSupports':installs,'removeSupports':removes,
            'excavationDepthM':hlocal}
        if stage_brace and stage_id in {'S2','S3','S4'}:
            delta=(opposite_wall_stage_free_displacements_m[stage_id]
                   -opposite_wall_stage_free_displacements_m['S2'])
            stage_data['supportAnchorOffsetsM']={'temporary':delta}
        if stage_brace and opposite_wall_stage_stiffness_kn_m is not None and stage_id in {'S3','S4'}:
            kright=opposite_wall_stage_stiffness_kn_m[stage_id]
            stage_data['supportStiffnessOverridesKnPerM']={
                'temporary':horizontal_brace_k*kright/(horizontal_brace_k+kright)}
        stages.append(stage_data)
    disc={'nodesM':nodes,'eiKnM2':elastic_modulus_mpa*1000*inertia_factor*i_circ,
       'toeBoundary':toe_boundary,'forceBasis':'single_pile_research','stages':stages,
       'schemaVersion':'pitguard.research-luoyu-3-3/1'}
    if any(x is not None for x in rock_values):
        disc['toeElasticBoundary']={
          'horizontalKnPerM':toe_elastic_horizontal_kn_m,
          'rotationKnMPerRad':toe_elastic_rotation_knm_rad}
    solver=solve_stages(disc)
    results=[]
    for obj,ans in zip(stages,solver['stages']):
        if ans['status']!='success':
            results.append({'id':obj['id'],'status':ans['status'],
                'error':ans.get('failureReason'),'curve':None});continue
        points=[]
        for e,(a,b) in enumerate(zip(nodes,nodes[1:])):
            f1,m1,_,m2=ans['elementEndForces'][e]
            points.extend(({'depthSiteM':offset+a,'depthLocalM':a,'side':'below',
                'displacementMm':ans['nodalDisplacementM'][e]*1000,'momentKnM':-m1,'shearKn':f1},
              {'depthSiteM':offset+b,'depthLocalM':b,'side':'above',
                'displacementMm':ans['nodalDisplacementM'][e+1]*1000,'momentKnM':m2,'shearKn':f1}))
        # JGJ120-2012 4.1.4(2): bounded TOTAL mobilized inside pressure,
        # independently from stage solver convergence and local spring signs.
        inside_states=[x for x in ans['soilStates'] if x['side']=='inside']
        mobilized=sum(x['pressureKn'] for x in inside_states)
        available=sum(x.get('passiveReferenceKn',0.) for x in inside_states)
        passive_ok=mobilized <= available + 1e-7*max(1.,available)
        inside_limit_points=([x for x in obj['soilSprings'] if x['side']=='inside'])
        inside_by_id={x['id']:x for x in obj['soilSprings'] if x['side']=='inside'}
        locally_over_limit=sum(1 for x in inside_states
            if x['pressureKn']>inside_by_id[x['id']]['passiveReferenceKn']+
                1e-6*max(1.,inside_by_id[x['id']]['passiveReferenceKn']))
        results.append({'id':obj['id'],'status':'success','curve':points,
            'mobilizedInsideReactionKnPerPile':mobilized,
            'passiveReferenceKnPerPile':available,
            'passiveResultantLimitSatisfied':passive_ok,
            'localPassiveCapEnabled':local_passive_cap,
            'localPassiveExceedanceCount':locally_over_limit,
            'strutReactions':[{'id':r['id'],'reactionKnPerPile':r['reactionKn'],
                 'reactionKnPerWallM':r['reactionKn']/spacing,
                 'referenceMm':r['referenceDisplacementM']*1000} for r in ans['supportStates']],
            'soilPoints':[{'side':r['side'],'depthSiteM':offset+r['depthM'],
                 'pressureKn':r['pressureKn'],'weightM':r['weightM'],
                 'projectionWidthM':spacing if r['side']=='outside' else b0,
                 'pressureKpa':r['pressureKn']/((spacing if r['side']=='outside' else b0)*r['weightM'])
                 if r['weightM']>0 else None,
                 'pressureKind':'active_earth' if r['side']=='outside' else 'mobilized_inside_soil_reaction'}
                 for r in ans['soilStates']],
            'peakDisplacementMm':max(abs(v['displacementMm']) for v in points),
            'peakMomentKnM':max(abs(v['momentKnM']) for v in points),
            'peakShearKn':max(abs(v['shearKn']) for v in points),
            'equilibrium':ans.get('equilibrium')})
    return {'schemaVersion':'pitguard.original-project-conditional-calculation/1',
        'modelStatus':'conditional_research_only','sameConditionsAsOriginal':False,
        'originalCurveComparison':'not_possible_without_original_node_arrays',
        'terrainIntegrationMethod':'piecewise_ramp_gaussian_quadrature_plus_analytic_infinite_plateau',
        'twoEndActualTransferValidated':False,
        'sourceReference':ref['source'],'sourceProject':ref['project'],
        'factsHash':canonical_hash(ref['facts']),'discreteHash':canonical_hash(disc),
        'solverStatus':solver['status'],'parameters':{
            'elasticModulusMpaCandidate':elastic_modulus_mpa,'inertiaFactorUnconfirmed':inertia_factor,
            'crownStiffnessMappingMultiplierUnconfirmed':crown_multiplier,
            'supportStiffnessMappingMultiplierUnconfirmed':support_multiplier,
            'meshMaxM':mesh_max_m,'pileTopOffsetSiteM':offset,
            'finiteLoadAlongPositionAssumed':'centered +/- 32.5 m',
          'pileToeBoundaryUnconfirmed':toe_boundary,
          'toeElasticRockSocketUnverified':disc.get('toeElasticBoundary'),
          'inclinedOppositeWallBoundaryUnverified':stage_brace,
          'adjacentLoadMethodCandidate':adjacent_load_mode,
          'terrainCohesionOverriddenAsHypothesisKpa':terrain_cohesion_override_kpa,
          'terrainFillGammaKnM3Unconfirmed':terrain_fill_gamma_kn_m3,
          'twoEndEquivalentBoundary':two_end,
          'actualCrownStiffnessPerAreaMNPerM2InReport':22.7,
          'crownStiffnessToPerPileMappingUnconfirmed':'22.7 MN/m2 x 1000 x pile_spacing x multiplier',
          'crownStiffnessUsedKnM':crown_k,
          'crownStiffnessSourceState':'explicit_unverified_input' if crown_stiffness_kn_m is not None else 'report_mapping_candidate',
          'saturationModel':'explicit_saturated_gammas' if saturated_gammas_kn_m3 is not None
                            else 'natural_gamma_used_as_saturated_candidate',
          'saturatedGammaKnM3':[l['gammaSat'] for l in layers],
          'outsideSoilPressureProjectionWidthM':spacing,
          'insideSoilReactionProjectionWidthM':b0,
          'soilReactionWidthMethod':'JGJ120-2012 4.1.7 capped circular pile formula',
          'exteriorWaterDepthCandidateM':exterior_water_depth_m,
          'adjacentSurchargeCandidateKpa':adjacent_surcharge_kpa,
          'terrainMethodCandidate':terrain_mode,
          'siteProfileSurveyCandidate':terrain_profile_points_m,
          'adjacentRectSurveyCandidate':adjacent_rectangle_geometry_m,
          'localPassiveCapEnabled':local_passive_cap},
        'modelLimitations':[
          '坡台使用已积分的连续折线Boussinesq竖向应力，按分层c/φ映射；仍不是规范坡面主动楔体真实压力解',
          '原工程土水合算/分算、逐层饱和重度、原水位及渗流作用尚未逐项确认',
          'E、EI折减、K0、桩底约束、冠梁及内支撑MN/m²到单桩kN/m映射均为待确认的建模假设',
          '岩体作为等效弹性土弹簧进行研究，原岩体节理、加固、土拱与围檩三维约束未校核',
          '原项目PDF仅含曲线图及峰值，未提供可逐点对照的原始数值数组'],
        'stages':[r for r in results if r['id']!='S0'],
        'initialState':results[0]}
