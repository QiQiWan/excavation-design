"""JGJ120-2012 3.4 and 4.1 narrow effective-stress calculation kernels.

Positive depth down from SAME ground datum; pressures are SOIL ONLY.
Hydrostatic water difference enters the beam external vector once.
"""
from math import pi, radians, tan, sqrt
from app.section.norms.engine import _pile_width

def width(diameter_m: float, spacing_m: float) ->float:
    return _pile_width({'pile_width':diameter_m,'pile_spacing':spacing_m,'pile_section':'circular'})['b0M']

def vertical_effective(layers, depth, water, surface, gamma_w):
    if depth<=surface:return 0.
    stress=0.
    for layer in layers:
        lo=max(layer.top_m,surface);hi=min(layer.bottom_m,depth)
        if hi<=lo:continue
        dry=max(0.,min(hi,water)-lo)
        wet=max(0.,hi-max(lo,water))
        stress+=dry*layer.gamma_natural_kn_m3+wet*(layer.gamma_sat_kn_m3-gamma_w)
    return stress

def pressure_state(layer,sigma_eff,*,initial=False):
    ka=tan(pi/4-radians(layer.phi_effective_deg)/2)**2;kp=1/ka
    active=max(0.,ka*sigma_eff-2*layer.c_effective_kpa*sqrt(ka))
    passive=kp*sigma_eff+2*layer.c_effective_kpa*sqrt(kp)
    # JGJ120 soil ps0 includes the elastic initial soil reaction, excluding separately applied water.
    p0=ka*sigma_eff
    return {'active':active, 'passive':passive, 'p0':p0,
            'ka':ka, 'kp':kp, 'tension_crack':ka*sigma_eff-2*layer.c_effective_kpa*sqrt(ka)<0}
