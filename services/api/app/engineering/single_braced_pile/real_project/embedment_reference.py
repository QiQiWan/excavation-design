"""Independent depth quadrature of Rankine pressure resultant moments.

This is a *pressure-method comparison* for JGJ120 4.2.2, not a claim that the
real recessed-slope, nearby-building and fractured rock case is Rankine-valid.
The staged beam spring stiffness and solution are not used here. Both wall
loads and water assumptions are recorded and their source unapproved.
"""
from __future__ import annotations

from math import pi, radians, sqrt, sin, tan

from .normative_stability import layer_at_site, soil_overburden
from .terrain_pressure import trapezoidal_fill_increment
from app.section.geotech_actions import rectangle_vertical_increment


def pressure_state(facts, z, *, from_depth, excavation_depth, exterior_water=.5,
                   inside_water=None, include_terrain=True):
    layer=layer_at_site(facts,z)
    angle=radians(float(layer['phiDeg']))
    ka=tan(pi/4-angle/2)**2
    kp=1./ka
    c=float(layer['cKpa'])
    site_offset=float(facts['pileTopOriginalGroundDepthM'])
    if inside_water is None: inside_water=excavation_depth+.5
    # The reported natural weight is used even under water as a declared
    # approximation. This function is thus NUMERIC only, not true effective
    # stress analysis based on measured gammaSat.
    sigma_out=soil_overburden(facts,0,z)
    sigma_in=soil_overburden(facts,excavation_depth,z) if z>excavation_depth else 0.
    if layer['stressBasis']=='separated':
        uo=9.81*max(0,z-exterior_water)
        ui=9.81*max(0,z-inside_water) if z>excavation_depth else 0.
        sigma_out=max(0.,sigma_out-uo)
        sigma_in=max(0.,sigma_in-ui)
    else:
        uo=ui=0.
    surcharge=0.  # explicit finite adjacent load computed below
    if z>site_offset:
        dist=z-site_offset
        surcharge=rectangle_vertical_increment(30.,4.3,16.3,-32.5,32.5,dist)
        if include_terrain:
            geo=facts['terrain']
            surcharge+=trapezoidal_fill_increment(
                depth_below_wall_top_m=dist,bench_m=geo['benchWidthM'],
                ramp_m=geo['slopeWidthM'],fill_height_m=geo['slopeHeightM'],
                fill_gamma_kn_m3=18.)['verticalIncrementKpa']
    pa=max(0.,ka*(sigma_out+surcharge)-2*c*sqrt(ka))+uo
    pp=max(0.,kp*sigma_in+2*c*sqrt(kp))+ui if z>excavation_depth else 0.
    return pa,pp


def moment_reference(facts, *, excavation_depth:float, support_site_depth:float,
                     include_terrain:bool=True, steps_per_layer:int=36)->dict:
    h=float(excavation_depth)
    s=float(support_site_depth)
    toe=float(facts['pileTopOriginalGroundDepthM'])+float(facts['pileLengthM'])
    if not 0<s<h<toe or not isinstance(steps_per_layer,int) or not 8<=steps_per_layer<=200:
        raise ValueError('Supported excavation needs 0 < support < excavation < toe')
    wall_top=float(facts['pileTopOriginalGroundDepthM'])
    points={wall_top,s,h,toe}
    pos=0.
    for lay in facts['soilLayersOriginalGround']:
        pos+=lay['thicknessM']
        if wall_top<pos<toe:points.add(pos)
    points=sorted(points)
    # Both one-piece and depth-refined Simpson quadrature are independent of
    # the elastic beam stiffness matrix and station weighting scheme.
    ap=pp=am=pm=0.
    for a,b in zip(points,points[1:]):
        n=steps_per_layer
        dz=(b-a)/n
        for j in range(n):
            zz=a+(j+.5)*dz
            active,passive=pressure_state(facts,zz,from_depth=wall_top,
                excavation_depth=h,include_terrain=include_terrain)
            ap+=active*dz
            pp+=passive*dz
            am+=active*(zz-s)*dz
            pm+=passive*(zz-s)*dz
    ratio=pm/am if am>0 and pm>0 else None
    return {'clause':'JGJ120-2012/4.2.2','method':'layer_midpoint_Rankine_pressure_moments',
      'sources':'2020 original report parameters; saturation, slope wedge and lateral pressure mapping unverified',
      'pressureVariant':'integrated_terrain_vertical_surcharge' if include_terrain else 'terrain_omitted_negative_control',
      'activeResultantKnPerWallM':ap,'passiveResultantKnPerWallM':pp,
      'activeMomentAboutSupportKnMPerWallM':am,'passiveMomentAboutSupportKnMPerWallM':pm,
      'embedmentFactorCandidate':ratio,'gradeIIIRequiredFactor':1.15,
      'numericallyExceedsThreshold':ratio is not None and ratio>=1.15,
      'independentOfStagedBeamDisplacements':True,
      'engineeringStatus':'blocked_slope_water_rock_soil_conventional_method_unapproved',
      'independentEngineeringStabilityQualified':False}
