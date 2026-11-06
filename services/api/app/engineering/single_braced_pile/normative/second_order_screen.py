"""Euler ideal-column reference, explicitly unqualified for soil-spring pile design."""
from __future__ import annotations
from math import pi,isfinite


def euler_ideal_column_critical_kn(*, flexural_rigidity_kn_m2:float,
                                   effective_length_m:float)->float:
    if any(type(x) is bool or not isinstance(x,(int,float)) or not isfinite(x) or x<=0
           for x in (flexural_rigidity_kn_m2,effective_length_m)):
        raise ValueError('Euler reference requires positive finite EI [kN.m2] and L_eff [m]')
    return pi*pi*flexural_rigidity_kn_m2/(effective_length_m**2)
