"""Deterministic geotechnical actions, explicitly not an FRWS implementation.

SI units: m, kPa, kN/m³; wedge resultants are kN per metre of wall.
Source/applicability register: docs/releases/V3_87_18_evidence/geotech_actions_sources.md.
No structural state or solver is imported. Pile-force conversion belongs to callers.
"""
from __future__ import annotations

from math import atan, atan2, degrees, fsum, hypot, isfinite, pi, radians, sqrt, tan
from typing import Mapping, Sequence


def _finite(*values: float) -> None:
    if any(not isfinite(v) for v in values):
        raise ValueError('Geotechnical action inputs must be finite')


def vertical_stress(layers: Sequence[Mapping], depth_m: float, surface_m: float,
                    water_m: float, gamma_w: float) -> dict:
    """Integrate total selfweight, then subtract the complete hydrostatic head.

    Layer water-analysis flags are deliberately ignored. Coordinates share one
    downward-positive datum. Water above the surface is explicit ponded water;
    its weight is included in total stress and cancels from effective stress.
    """
    _finite(depth_m, surface_m, water_m, gamma_w)
    if gamma_w <= 0 or depth_m < surface_m or not layers:
        raise ValueError('Positive water weight, soil column and depth >= surface required')
    total = gamma_w * max(0.0, surface_m - water_m)
    cursor = surface_m
    previous_bottom = None
    for layer in layers:
        lo, hi = float(layer['top_m']), float(layer['bottom_m'])
        dry, wet = float(layer['gamma_unsat_kn_m3']), float(layer['gamma_sat_kn_m3'])
        _finite(lo, hi, dry, wet)
        if hi <= lo or dry <= 0 or wet <= gamma_w or wet < dry:
            raise ValueError('Invalid layer geometry or unit weights')
        if previous_bottom is not None and abs(lo - previous_bottom) > 1e-9:
            raise ValueError('Layers must be ordered and contiguous')
        previous_bottom = hi
        a, b = max(surface_m, lo), min(depth_m, hi)
        if b <= a:
            continue
        if a > cursor + 1e-9:
            raise ValueError('Layers do not cover the complete soil column')
        total += dry * max(0.0, min(b, water_m) - a)
        total += wet * max(0.0, b - max(a, water_m))
        cursor = b
    if cursor < depth_m - 1e-9:
        raise ValueError('Layers do not cover the complete soil column')
    pore = gamma_w * max(0.0, depth_m - water_m)
    effective = total - pore
    if effective < -1e-8:
        raise ValueError('Negative effective vertical stress is unsupported')
    return {'total_vertical_kpa': total, 'effective_vertical_kpa': max(0.0, effective),
            'pore_pressure_kpa': pore, 'method_id': 'layered_selfweight_hydrostatic_v1'}


def _load_geometry(q: float, near: float, far: float, depth: float) -> None:
    _finite(q, near, far, depth)
    if q < 0 or near < 0 or far < near or depth < 0:
        raise ValueError('Load requires q >= 0, 0 <= near <= far and depth >= 0')


def strip_vertical_increment(q_kpa: float, near_m: float, far_m: float,
                             depth_m: float) -> float:
    """Boussinesq vertical stress from an infinite-along-wall strip.

    Horizontal distances are from the evaluation line; depth is below the load
    plane. At the surface an edge receives the half-plane limiting value q/2.
    """
    _load_geometry(q_kpa, near_m, far_m, depth_m)
    if q_kpa == 0 or far_m == near_m:
        return 0.0
    if depth_m == 0:
        return q_kpa / 2 if near_m == 0 else 0.0
    a, b, h = near_m, far_m, depth_m
    # atan2 avoids subtraction of nearly equal arctangents for distant strips.
    delta_angle = atan2((b-a) * h, h*h + a*b)
    value = q_kpa / pi * fsum((delta_angle, (b / hypot(b,h)) * (h / hypot(b,h)),
                               -(a / hypot(a,h)) * (h / hypot(a,h))))
    return max(0.0, value)


def _corner_vertical(x: float, y: float, h: float) -> float:
    """Signed rectangular corner influence, integrated vertical point kernel."""
    radius = hypot(x, y, h)
    xh, yh = hypot(x,h), hypot(y,h)
    return (atan2((x / radius)*y, h)
            + (x / xh)*(y / radius)*(h / xh)
            + (y / yh)*(x / radius)*(h / yh)) / (2*pi)


def rectangle_vertical_increment(q_kpa: float, near_m: float, far_m: float,
                                 along_start_m: float, along_end_m: float,
                                 depth_m: float) -> float:
    """Finite rectangle vertical stress by signed closed corner influences.

    Along-wall coordinates are relative to the section; no centering or
    infinite-length approximation is implicit. Elastic homogeneous half-space,
    horizontal load plane. This is not a slope or lateral-wall stress solution.
    """
    _load_geometry(q_kpa, near_m, far_m, depth_m)
    _finite(along_start_m, along_end_m)
    if along_end_m < along_start_m:
        raise ValueError('Along-wall end must not precede start')
    if q_kpa == 0 or near_m == far_m or along_start_m == along_end_m:
        return 0.0
    if depth_m == 0:
        if near_m > 0 or along_start_m > 0 or along_end_m < 0:
            return 0.0
        return q_kpa * (.25 if along_start_m == 0 or along_end_m == 0 else .5)
    a,b,c,d,h = near_m,far_m,along_start_m,along_end_m,depth_m
    influence = fsum((_corner_vertical(b,d,h), -_corner_vertical(a,d,h),
                      -_corner_vertical(b,c,h), _corner_vertical(a,c,h)))
    if influence < -1e-12 or influence > .5 + 1e-12:
        raise ArithmeticError('Rectangular influence outside physical retained-halfspace limits')
    return q_kpa * max(0.0, min(.5, influence))


def rankine_pressure_state(total_vertical_kpa: float, pore_pressure_kpa: float,
                           surcharge_vertical_kpa: float, phi_deg: float,
                           cohesion_kpa: float, k0: float, mode: str) -> dict:
    """Current-station pressure basis with exactly one explicit water term.

    Combined mode requires total-stress strength parameters supplied by caller.
    These conventional bounds do not infer the original software's conventions.
    Raw total/effective fields exclude surcharge; its contribution is separate.
    """
    _finite(total_vertical_kpa,pore_pressure_kpa,surcharge_vertical_kpa,phi_deg,cohesion_kpa,k0)
    if min(total_vertical_kpa,pore_pressure_kpa,surcharge_vertical_kpa,cohesion_kpa) < 0 or not 0 <= phi_deg < 90 or k0 <= 0:
        raise ValueError('Invalid pressure-state input')
    effective = total_vertical_kpa-pore_pressure_kpa
    if effective < -1e-8:
        raise ValueError('Negative effective vertical stress is unsupported')
    effective = max(0.0,effective)
    if mode == 'effective_separated':
        sigma, water, basis = effective+surcharge_vertical_kpa,pore_pressure_kpa,'effective'
    elif mode == 'total_combined':
        sigma, water, basis = total_vertical_kpa+surcharge_vertical_kpa,0.0,'total'
    else:
        raise ValueError('Unknown soil-water pressure mode')
    ka=tan(pi/4-radians(phi_deg)/2)**2
    kp=1/ka
    p0=k0*sigma
    pa=max(0.0,ka*sigma-2*cohesion_kpa*sqrt(ka))
    pp=kp*sigma+2*cohesion_kpa*sqrt(kp)
    if p0 < pa-1e-8 or p0 > pp+1e-8:
        raise ValueError('K0 initial pressure lies outside active/passive bounds; not clipped')
    return {'p0_kpa':p0,'active_kpa':pa,'passive_kpa':pp,'external_water_kpa':water,
            'total_vertical_kpa':total_vertical_kpa,'effective_vertical_kpa':effective,
            'surcharge_vertical_kpa':surcharge_vertical_kpa,'pressure_basis':basis,
            'method_id':f'rankine_{mode}_v1'}


def active_wedge_resultant(surface_vertices_m: Sequence[tuple[float,float]],
                           height_m: float, gamma_kn_m3: float, phi_deg: float,
                           uniform_surcharge_kpa: float = 0.0,
                           strip_loads: Sequence[Mapping] = ()) -> dict:
    """Dry homogeneous cohesionless active wedge, smooth vertical wall only.

    Vertices (x, elevation) are measured outward/upward from wall top (0,0).
    A horizontal tail follows the final vertex. Increasing x and nondecreasing
    elevation describe a terrace/rising berm; vertical steps are unsupported.
    Finite strips have keys q_kpa, near_m, far_m and are infinite along wall.
    No K0, passive law, pressure distribution, water or staged coupling inferred.
    """
    _finite(height_m,gamma_kn_m3,phi_deg,uniform_surcharge_kpa)
    if height_m <= 0 or gamma_kn_m3 <= 0 or not 0 <= phi_deg < 89 or uniform_surcharge_kpa < 0:
        raise ValueError('Invalid dry active-wedge inputs')
    vertices=tuple((float(x),float(y)) for x,y in surface_vertices_m)
    if not vertices or vertices[0] != (0.0,0.0):
        raise ValueError('Terrain must start at wall top (0,0)')
    for x,y in vertices:
        _finite(x,y)
    for (x0,y0),(x1,y1) in zip(vertices,vertices[1:]):
        if x1 <= x0 or y1 < y0:
            raise ValueError('Terrain x must increase and elevation cannot decrease')
    loads=[]
    for load in strip_loads:
        q,a,b=(float(load[k]) for k in ('q_kpa','near_m','far_m'))
        _load_geometry(q,a,b,height_m)
        loads.append((q,a,b))
    phi=radians(phi_deg)

    def geometry(alpha):
        slope=tan(alpha)
        contact=None
        polygon=[(0.0,0.0)]
        for (x0,y0),(x1,y1) in zip(vertices,vertices[1:]):
            terrain_slope=(y1-y0)/(x1-x0)
            denominator=slope-terrain_slope
            if denominator > 0:
                hit=(height_m+y0-terrain_slope*x0)/denominator
                if x0-1e-9 <= hit <= x1+1e-9:
                    contact=max(x0,min(x1,hit))
                    polygon.append((contact,-height_m+slope*contact))
                    break
            polygon.append((x1,y1))
        if contact is None:
            contact=(height_m+vertices[-1][1])/slope
            if contact < vertices[-1][0]-1e-8:
                raise ValueError('Invalid trial ray intersection')
            polygon.append((contact,vertices[-1][1]))
        area=fsum(((y0+height_m-slope*x0)+(y1+height_m-slope*x1))*(x1-x0)/2
                  for (x0,y0),(x1,y1) in zip(polygon,polygon[1:]))
        if area < -1e-8 or contact <= 0:
            raise ValueError('Invalid trial wedge polygon')
        area=max(0.0,area)
        surcharge=uniform_surcharge_kpa*contact+fsum(q*max(0,min(b,contact)-a) for q,a,b in loads)
        weight=gamma_kn_m3*area+surcharge
        pressure=weight*tan(alpha-phi)
        return pressure,contact,area,weight,surcharge

    # Surface and load-edge rays are all branch boundaries of the exact
    # polygon/footprint integration. Search each smooth interval independently.
    low=max(phi,1e-9)+1e-10
    high=pi/2-1e-9
    breaks={low,high}
    def elevation(x):
        for (a,ya),(b,yb) in zip(vertices,vertices[1:]):
            if a <= x <= b:
                return ya+(yb-ya)*(x-a)/(b-a)
        return vertices[-1][1]
    for x,y in vertices[1:]:
        angle=atan((height_m+y)/x)
        if low < angle < high: breaks.add(angle)
    for _,a,b in loads:
        for x in (a,b):
            if x>0:
                angle=atan((height_m+elevation(x))/x)
                if low < angle < high: breaks.add(angle)
    boundaries=sorted(breaks)
    candidates=list(boundaries)
    ratio=(sqrt(5)-1)/2
    for a,b in zip(boundaries,boundaries[1:]):
        c=b-ratio*(b-a); d=a+ratio*(b-a)
        fc=geometry(c)[0]; fd=geometry(d)[0]
        for _ in range(90):
            if b-a < 1e-12: break
            if fc>fd:
                b,d,fd=d,c,fc
                c=b-ratio*(b-a); fc=geometry(c)[0]
            else:
                a,c,fc=c,d,fd
                d=a+ratio*(b-a); fd=geometry(d)[0]
        candidates.append((a+b)/2)
    angle=max(candidates,key=lambda x:geometry(x)[0])
    force,contact,area,weight,surcharge=geometry(angle)
    return {'active_resultant_kn_per_m':force,'critical_angle_deg':degrees(angle),
            'contact_x_m':contact,'wedge_area_m2':area,'wedge_weight_kn_per_m':gamma_kn_m3*area,
            'surcharge_force_kn_per_m':surcharge,'total_vertical_force_kn_per_m':weight,
            'method_id':'coulomb_active_broken_backfill_dry_homogeneous_v1',
            'coupling_status':'standalone_resultant_only',
            'surface_vertices_m':vertices,'height_m':height_m,'gamma_kn_m3':gamma_kn_m3,
            'phi_deg':phi_deg,'uniform_surcharge_kpa':uniform_surcharge_kpa,
            'strip_loads':tuple({'q_kpa':q,'near_m':a,'far_m':b} for q,a,b in loads),
            'search_interval_count':len(boundaries)-1,'search_tolerance_rad':1e-12,
            'limitations':('dry homogeneous cohesionless soil','smooth vertical wall',
                           'two-dimensional infinite-along-wall loading',
                           'no at-rest/passive/distributed/staged wall coupling')}
