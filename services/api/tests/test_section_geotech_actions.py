"""Analytic and independently integrated action checks, not FRWS equivalence."""
import math

import numpy as np
import pytest

from app.section.geotech_actions import (
    active_wedge_resultant, rankine_pressure_state, rectangle_vertical_increment,
    strip_vertical_increment, vertical_stress,
)


def test_strip_analytic_and_surface_limits():
    assert strip_vertical_increment(30, 0, 2, 2) == pytest.approx(30 * (.25 + 1 / (2 * math.pi)))
    assert strip_vertical_increment(30, 4.3, 16.3, 0) == 0
    assert strip_vertical_increment(30, 0, 12, 0) == 15
    assert strip_vertical_increment(0, 1, 2, 3) == 0
    assert strip_vertical_increment(30, 2, 2, 3) == 0


def kernel_integral(q, a, b, c, d, h, n=100):
    """Independent tensor Gauss integration of the original elemental kernel."""
    t, w = np.polynomial.legendre.leggauss(n)
    x = (a + b) / 2 + (b - a) * t[:, None] / 2
    y = (c + d) / 2 + (d - c) * t[None, :] / 2
    kernel = 3 * q * h ** 3 / (2 * math.pi * (x*x + y*y + h*h)**2.5)
    return float(np.sum(kernel * w[:, None] * w[None, :]) * (b-a)*(d-c)/4)


@pytest.mark.parametrize('geometry', [(4.3,16.3,-32.5,32.5,4.9), (0,2,0,3,2), (1,2,3,4,.2), (0,2,-1,3,1)])
def test_rectangle_against_independent_kernel(geometry):
    assert rectangle_vertical_increment(30,*geometry) == pytest.approx(kernel_integral(30,*geometry), rel=1e-10, abs=1e-11)


def test_rectangle_additivity_symmetry_and_strip_limit():
    a=rectangle_vertical_increment(30,4.3,16.3,-32.5,32.5,4.9)
    halves=sum(rectangle_vertical_increment(30,4.3,16.3,c,d,4.9) for c,d in [(-32.5,0),(0,32.5)])
    assert a == pytest.approx(halves)
    assert rectangle_vertical_increment(30,1,3,-4,-2,2) == pytest.approx(rectangle_vertical_increment(30,1,3,2,4,2))
    assert rectangle_vertical_increment(30,4.3,16.3,-1e6,1e6,4.9) == pytest.approx(strip_vertical_increment(30,4.3,16.3,4.9), rel=1e-10)
    assert rectangle_vertical_increment(30,0,3,-4,4,0)==15
    assert rectangle_vertical_increment(30,0,3,0,4,0)==7.5
    assert rectangle_vertical_increment(30,1,3,-4,4,0)==0


def test_total_effective_flags_do_not_remove_head():
    layers=[dict(top_m=0,bottom_m=2,gamma_unsat_kn_m3=18,gamma_sat_kn_m3=20,water_mode='total_combined'),
            dict(top_m=2,bottom_m=8,gamma_unsat_kn_m3=18,gamma_sat_kn_m3=20,water_mode='effective_separated')]
    stress=vertical_stress(layers,4,0,0,10)
    assert stress['total_vertical_kpa']==80
    assert stress['effective_vertical_kpa']==40
    assert stress['pore_pressure_kpa']==40
    for mode in ('effective_separated','total_combined'):
        state=rankine_pressure_state(80,40,0,0,0,1,mode)
        assert state['p0_kpa']+state['external_water_kpa']==80
        assert state['active_kpa']+state['external_water_kpa']==pytest.approx(80)
        assert state['passive_kpa']+state['external_water_kpa']==pytest.approx(80)
    assert vertical_stress(layers,4,2,3,10)['total_vertical_kpa']==38
    assert vertical_stress(layers,4,2,3,10)['effective_vertical_kpa']==28


def test_rankine_hand_check_and_no_silent_clipping():
    s=rankine_pressure_state(100,20,10,30,0,.5,'effective_separated')
    assert (s['p0_kpa'],s['active_kpa'],s['passive_kpa'],s['external_water_kpa']) == pytest.approx((45,30,270,20))
    with pytest.raises(ValueError): rankine_pressure_state(1,2,0,30,0,.5,'effective_separated')
    with pytest.raises(ValueError): rankine_pressure_state(100,0,0,30,0,.1,'effective_separated')
    with pytest.raises(ValueError): rankine_pressure_state(100,0,0,30,0,.5,'unknown')


def test_flat_wedge_analytic():
    r=active_wedge_resultant(((0,0),),6,18,30)
    assert r['active_resultant_kn_per_m']==pytest.approx(108,rel=1e-10)
    assert r['critical_angle_deg']==pytest.approx(60,abs=2e-5)
    assert r['wedge_area_m2']==pytest.approx(18/math.sqrt(3),rel=1e-6)
    assert active_wedge_resultant(((0,0),),6,18,30,10)['active_resultant_kn_per_m']==pytest.approx(128,rel=1e-10)


def test_berm_wedge_independent_dense_force_polygon():
    vertices=((0,0),(1.7,0),(3,1.3))
    r=active_wedge_resultant(vertices,6,18,30,strip_loads=({'q_kpa':30,'near_m':4.3,'far_m':16.3},))
    # Separate vectorized construction: intersect each ray with terrace/ramp/tail,
    # integrate clipped polygon by trapezoids, then resolve N and P in x/y.
    alpha=np.linspace(math.radians(30.001),math.radians(89.99),20000)
    values=[]
    for t in alpha:
        m=math.tan(t)
        if 6/m<=1.7: x=6/m
        elif m>1 and 1.7<4.3/(m-1)<=3: x=4.3/(m-1)
        else: x=7.3/m
        xs=[0]+[v for v in (1.7,3) if v<x]+[x]
        elev=lambda xx: min(1.3,max(0,xx-1.7))
        area=sum((elev(a)+6-m*a+elev(b)+6-m*b)*(b-a)/2 for a,b in zip(xs,xs[1:]))
        w=18*area+30*max(0,min(x,16.3)-4.3)
        normal=w/(math.cos(t)+math.tan(math.pi/6)*math.sin(t))
        values.append(normal*(math.sin(t)-math.tan(math.pi/6)*math.cos(t)))
    assert r['active_resultant_kn_per_m']==pytest.approx(max(values),rel=2e-7)
    assert r['active_resultant_kn_per_m']>108
    assert r['coupling_status']=='standalone_resultant_only'


@pytest.mark.parametrize('fn,args',[(strip_vertical_increment,(30,-1,2,3)),(rectangle_vertical_increment,(30,0,1,3,2,1)),(active_wedge_resultant,(((0,0),(1,2),(1,3)),6,18,30)),(strip_vertical_increment,(float('nan'),0,1,1))])
def test_invalid_geometry_and_numbers(fn,args):
    with pytest.raises(ValueError): fn(*args)


def test_ponded_water_and_complete_column_validation():
    layer=dict(top_m=0,bottom_m=10,gamma_unsat_kn_m3=18,gamma_sat_kn_m3=20)
    s=vertical_stress([layer],4,0,-1,10)
    assert (s['total_vertical_kpa'],s['effective_vertical_kpa'],s['pore_pressure_kpa'])==(90,40,50)
    with pytest.raises(ValueError): vertical_stress([dict(layer,top_m=1)],4,0,0,10)
    with pytest.raises(ValueError): vertical_stress([dict(layer,bottom_m=2)],4,0,0,10)
    with pytest.raises(ValueError): vertical_stress([layer],1,2,0,10)


def test_water_ledger_independent_resultant_and_first_moment():
    # Deliberately alternate current-station modes. phi=0 and zero effective
    # overburden isolate hydrostatic total pressure, including embedded water.
    t,w=np.polynomial.legendre.leggauss(12)
    force=moment=0
    for lo,hi,mode in [(0,1,'effective_separated'),(1,3,'total_combined'),(3,4,'effective_separated')]:
        for xi,wi in zip(t,w):
            z=(lo+hi)/2+xi*(hi-lo)/2
            r=rankine_pressure_state(10*z,10*z,0,0,0,1,mode)
            p=r['p0_kpa']+r['external_water_kpa']
            force+=p*wi*(hi-lo)/2
            moment+=p*z*wi*(hi-lo)/2
    assert force==pytest.approx(10*4**2/2)
    assert moment==pytest.approx(10*4**3/3)


def test_wedge_scaling_and_far_berm_flat_limit():
    reference=active_wedge_resultant(((0,0),(1.7,0),(3,1.3)),6,18,30)
    scaled=active_wedge_resultant(((0,0),(3.4,0),(6,2.6)),12,18,30)
    assert scaled['active_resultant_kn_per_m']==pytest.approx(4*reference['active_resultant_kn_per_m'])
    far=active_wedge_resultant(((0,0),(100,0),(101.3,1.3)),6,18,30)
    assert far['active_resultant_kn_per_m']==pytest.approx(108,rel=1e-10)
    finite=active_wedge_resultant(((0,0),),6,18,30,strip_loads=({'q_kpa':10,'near_m':0,'far_m':100},))
    assert finite['active_resultant_kn_per_m']==pytest.approx(128,rel=1e-10)


def test_rectangle_small_depth_and_remote_load_positivity():
    # A tiny depth at a load edge has a finite q/2 limit; no arbitrary epsilon.
    assert rectangle_vertical_increment(30,0,12,-32.5,32.5,1e-8)==pytest.approx(15)
    assert rectangle_vertical_increment(30,4.3,16.3,-32.5,32.5,1e-8)>=0
    assert rectangle_vertical_increment(30,4.3,16.3,32.5,32.5,4)==0
