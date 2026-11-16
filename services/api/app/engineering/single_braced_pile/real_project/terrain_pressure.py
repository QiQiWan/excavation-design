"""Layer-aware terrace surcharge stress under the Boussinesq half-space model.

The *terrain integration* is geometric and quadrature converged. The physical
map from a non-horizontal, partly saturated active wedge to lateral pressure
remains a separate (unverified) hypothesis; do not call this a Coulomb wedge or
a JGJ120 code-exact slope pressure solution.
"""
from __future__ import annotations
from math import atan,pi,hypot
from app.section.geotech_actions import strip_vertical_increment

# Six-point Gauss-Legendre nodes and weights, dimensionless on [-1,1].
_GAUSS_6 = ((-.932469514203152,.171324492379170),
            (-.661209386466265,.360761573048139),
            (-.238619186083197,.467913934572691),
            (.238619186083197,.467913934572691),
            (.661209386466265,.360761573048139),
            (.932469514203152,.171324492379170))


def infinite_outer_strip(q_kpa:float,start_m:float,depth_m:float)->float:
    if q_kpa<0 or start_m<0 or depth_m<=0:
        raise ValueError('Outer plateau requires nonnegative surcharge, distance and positive depth')
    z=depth_m;a=start_m
    return (q_kpa/pi)*(pi/2-atan(a/z)-(a*z)/(a*a+z*z))


def trapezoidal_fill_increment(*,depth_below_wall_top_m:float,bench_m:float,
                              ramp_m:float,fill_height_m:float,
                              fill_gamma_kn_m3:float,quadrature_segments:int=24)->dict:
    """Integrate the physical ramp and infinite plateau without 100-m truncation.

    q(x) = gamma_fill * h(x); stress = integral[0, inf] q(x) *
    2*z^3/[pi*(x^2+z^2)^2] dx. The ramp is integrated with Gaussian nodes and
    the plateau integral is analytic. q units kPa, sigma_z units kPa.
    """
    z=depth_below_wall_top_m
    if z<=0 or bench_m<0 or ramp_m<=0 or fill_height_m<0 or fill_gamma_kn_m3<=0:
        raise ValueError('Terrain coordinates and fill weight must be physical')
    if not isinstance(quadrature_segments,int) or quadrature_segments<2 or quadrature_segments>500:
        raise ValueError('Quadrature segments must be integers in [2, 500]')
    if fill_height_m==0:
        return {'verticalIncrementKpa':0.,'rampIncrementKpa':0.,'plateauIncrementKpa':0.,
                'method':'piecewise_terrain_elastic_vertical_stress_v2'}
    dx=ramp_m/quadrature_segments
    ramp_sigma=0.
    for j in range(quadrature_segments):
        a=bench_m+j*dx;b=a+dx
        for xi,w in _GAUSS_6:
            x=(a+b)/2+xi*dx/2
            height=fill_height_m*(x-bench_m)/ramp_m
            q=fill_gamma_kn_m3*height
            kernel=2*z**3/(pi*(x*x+z*z)**2)
            ramp_sigma+=q*kernel*w*dx/2
    plateau=infinite_outer_strip(fill_gamma_kn_m3*fill_height_m,bench_m+ramp_m,z)
    return {'verticalIncrementKpa':ramp_sigma+plateau,
            'rampIncrementKpa':ramp_sigma,'plateauIncrementKpa':plateau,
            'method':'piecewise_terrain_elastic_vertical_stress_v2',
            'quadratureSegments':quadrature_segments,'integralToInfinitePlateau':True,
            'inputVerticalFillGammaKnM3':fill_gamma_kn_m3,
            'limitations':['填土单位重必须有土源依据',
              '坡面压力转换为侧压力仍需原项目模型或独立岩土理论校核；本式仅为垂直应力影响']}


def terrain_refinement(*,depth_below_wall_top_m:float,bench_m:float,ramp_m:float,
                       fill_height_m:float,fill_gamma_kn_m3:float)->dict:
    args=dict(depth_below_wall_top_m=depth_below_wall_top_m,bench_m=bench_m,
              ramp_m=ramp_m,fill_height_m=fill_height_m,fill_gamma_kn_m3=fill_gamma_kn_m3)
    coarse=trapezoidal_fill_increment(**args,quadrature_segments=12)
    fine=trapezoidal_fill_increment(**args,quadrature_segments=48)
    diff=abs(fine['verticalIncrementKpa']-coarse['verticalIncrementKpa'])
    ratio=diff/max(1e-9,abs(fine['verticalIncrementKpa']))
    return {'coarse':coarse['verticalIncrementKpa'],'fine':fine['verticalIncrementKpa'],
            'relativeDifference':ratio,'integrationVerified':ratio<=.001}


def polyline_fill_increment(*, depth_below_wall_top_m:float,
                            points_m:list[tuple[float,float]],
                            fill_gamma_kn_m3:float,
                            quadrature_segments:int=18)->dict:
    """Piecewise surveyed (x, height) surface and an analytic outer plateau.

    ``x`` is horizontal distance AWAY from the wall, and height is ABOVE
    pile-top level. Both use metres. Monotone upper ground is a restricted
    research geometry; terraces with cuts, overhangs or multiple benches need
    a separate method qualification. This returns vertical stress only.
    """
    from math import isfinite
    z=depth_below_wall_top_m
    if not (isfinite(z) and z>0 and isfinite(fill_gamma_kn_m3) and fill_gamma_kn_m3>0):
        raise ValueError('Positive finite depth and soil unit weight required')
    if not isinstance(points_m,list) or not 3<=len(points_m)<=80:
        raise ValueError('Survey polyline must have 3..80 ordered profile points')
    if not isinstance(quadrature_segments,int) or not 2<=quadrature_segments<=100:
        raise ValueError('Invalid geometry quadrature resolution')
    pts=[]
    for entry in points_m:
        if not isinstance(entry,(list,tuple)) or len(entry)!=2 or any(
            isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in entry):
            raise ValueError('Each geometric vertex must contain two finite numbers')
        x,y=map(float,entry)
        if x<0 or y<0 or (pts and (x<=pts[-1][0] or y<pts[-1][1])):
            raise ValueError('Profile x increases and top elevation cannot decrease')
        pts.append((x,y))
    if pts[0]!=(0.,0.) or pts[-1][1]<=0:
        raise ValueError('Wall datum must start at (0,0) and far field must be elevated')
    value=0.
    component=[]
    for (xa,ya),(xb,yb) in zip(pts,pts[1:]):
        # Fixed Gaussian quadrature independently repeated per measured segment.
        dx=(xb-xa)/quadrature_segments
        partial=0.
        for i in range(quadrature_segments):
            lo=xa+i*dx;hi=lo+dx
            for xi,w in _GAUSS_6:
                x=(lo+hi)/2+xi*(hi-lo)/2
                h=ya+(yb-ya)*(x-xa)/(xb-xa)
                kernel=2*z**3/(pi*(x*x+z*z)**2)
                partial+=fill_gamma_kn_m3*h*kernel*w*(hi-lo)/2
        component.append(partial);value+=partial
    plateau=infinite_outer_strip(fill_gamma_kn_m3*pts[-1][1],pts[-1][0],z)
    return {'verticalIncrementKpa':value+plateau,
        'segmentIncrementsKpa':component,'outerPlateauIncrementKpa':plateau,
        'method':'source_traced_monotone_piecewise_terrain_elastic_vertical_stress/3.89.11',
        'horizontalStressMapping':'requires_separate_code_method_decision',
        'engineeringQualified':False}
