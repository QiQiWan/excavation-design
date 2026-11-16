"""Independent JGJ 120-2012 clauses 3.4.7/3.4.8 load references.

Earth pressure transfer to a retaining wall is a *separate* 3.4.2 decision.
Coordinates z/a/b/d are measured from the retaining-wall top and in metres;
q, gamma, c in kPa, kN/m3 and kPa respectively. Explicit assumptions
prevent adding a ground surcharge twice when comparing different methods.
"""
from __future__ import annotations
from math import isfinite, radians, sqrt, tan

METHOD = "jgj120_2012_3.4.7_3.4.8/3.89.10"


def _finite(name: str, value: float, *, lower: float = 0.) -> float:
    if isinstance(value, bool):
        raise ValueError(name + " cannot be boolean")
    try: v = float(value)
    except (TypeError, ValueError) as exc: raise ValueError(name + " must be a number") from exc
    if not isfinite(v) or v < lower:
        raise ValueError(name + " outside finite positive engineering domain")
    return v


def local_surcharge_347(*, pressure_kpa: float, distance_m: float,
                        width_m: float, depth_m: float,
                        length_m: float | None = None,
                        foundation_depth_m: float = 0.,
                        diffusion_angle_deg: float = 45.) -> dict:
    """3.4.7(1)/(2) exact stress band and magnitudes. No elastic spread added."""
    q = _finite("pressure", pressure_kpa)
    a = _finite("distance", distance_m)
    b = _finite("width", width_m, lower=1e-9)
    z = _finite("depth", depth_m)
    d = _finite("foundation depth", foundation_depth_m)
    angle = _finite("diffusion angle", diffusion_angle_deg, lower=1e-9)
    if abs(angle-45.)>1e-9:
        raise ValueError("JGJ120 3.4.7 45-degree diffusion assumption is fixed; another angle needs its own method")
    slope = tan(radians(angle))
    start = d+a/slope
    end = d+(3*a+b)/slope
    if length_m is None:
        peak = q*b/(b+2*a)
        clause = "JGJ120-2012/3.4.7-1"
    else:
        length = _finite("length", length_m, lower=1e-9)
        peak = q*b*length/((b+2*a)*(length+2*a))
        clause = "JGJ120-2012/3.4.7-2"
    value = peak if start <= z <= end else 0.
    return {"method":METHOD,"clause":clause,"verticalIncrementKpa":value,
            "activeDepthBandFromTopM":[start,end],"insideInfluenceBand":start<=z<=end,
            "conversionToEarthPressure":"requires_layerwise_JGJ120_3.4.2",
            "engineeringQualified":False}


def upper_slope_348(*, depth_below_wall_top_m: float, bench_m: float,
                    slope_width_m: float, slope_height_m: float,
                    overlying_gamma_kn_m3: float, overlying_cohesion_kpa: float,
                    overlying_ka: float, diffusion_angle_deg: float = 45.) -> dict:
    """JGJ120 3.4.8 piecewise equivalent surcharge, only if tension cut is valid.

    Eak1 is the tension-cut active resultant of the *upper* wedge, and shall
    not be replaced by a guessed zero-cohesion value without explicit input.
    """
    z = _finite("depth", depth_below_wall_top_m)
    a = _finite("bench", bench_m)
    b = _finite("slope width", slope_width_m, lower=1e-9)
    h = _finite("height", slope_height_m)
    gamma = _finite("gamma", overlying_gamma_kn_m3, lower=1e-9)
    cohesion = _finite("cohesion", overlying_cohesion_kpa)
    ka = _finite("Ka", overlying_ka, lower=1e-9)
    if ka>1.:
        raise ValueError("Active Ka > 1 requires separately qualified soil mechanism")
    angle = _finite("diffusion angle", diffusion_angle_deg, lower=1e-9)
    if abs(angle-45.)>1e-9:
        raise ValueError("JGJ120 3.4.8 45-degree zone assumption is fixed; another angle needs its own method")
    start=a/tan(radians(angle))
    end=(a+b)/tan(radians(angle))
    tension_depth=2*cohesion/(gamma*sqrt(ka))
    valid=(h>tension_depth or cohesion==0.)
    # Upper-wedge tensile cutoff formula is applied only when it intersects
    # the retaining soil. Otherwise a Coulomb wedge/other qualified method is needed.
    eak1=(.5*gamma*h*h*ka-2*cohesion*h*sqrt(ka)+2*cohesion*cohesion/gamma) if valid else None
    if not valid:
        value=None
        status="blocked_tension_depth_exceeds_upper_slope_height"
    elif z < start:
        value=0.;status="conditional_numeric_reference"
    elif z <= end:
        value=gamma*h*(z*tan(radians(angle))-a)/b + (
            eak1/(ka*b*b))*(a+b-z*tan(radians(angle)))
        status="conditional_numeric_reference"
    else:
        value=gamma*h;status="conditional_numeric_reference"
    return {"method":METHOD,"clause":"JGJ120-2012/3.4.8-1..4",
            "status":status,"verticalIncrementKpa":value,
            "Eak1KnPerWallM":eak1,"tensionDepthM":tension_depth,
            "upperSlopeHeightM":h,"activeDepthBandM":[start,end],
            "conversionToEarthPressure":"requires_layerwise_JGJ120_3.4.2",
            "siteConditionVerified":False,"engineeringQualified":False}


def load_comparison(reference:dict, depths_m:list[float], *,
                    slope_cohesion_kpa:float|None=None) -> dict:
    """Independent distributions; no silent replacement of the staged model."""
    f=reference['facts']; terr=f['terrain']; adjacent=f['finiteAdjacentSurcharge']
    soil0=f['soilLayersOriginalGround'][0]
    from math import pi
    phi=soil0['phiDeg']*pi/180
    ka=tan(pi/4-phi/2)**2
    slope_c=soil0['cKpa'] if slope_cohesion_kpa is None else slope_cohesion_kpa
    profiles=[]
    for z in depths_m:
        slope=upper_slope_348(depth_below_wall_top_m=z,
          bench_m=terr['benchWidthM'],slope_width_m=terr['slopeWidthM'],
          slope_height_m=terr['slopeHeightM'],overlying_gamma_kn_m3=soil0['gammaNaturalKnM3'],
          overlying_cohesion_kpa=slope_c,overlying_ka=ka)
        q=local_surcharge_347(pressure_kpa=adjacent['pressureKpa'],
          distance_m=adjacent['nearDistanceM'],width_m=adjacent['widthM'],
          length_m=adjacent['lengthM'],depth_m=z)
        profiles.append({'localDepthM':z,'slope':slope,'adjacent':q,
          'combinedVerticalIncrementKpa':(slope['verticalIncrementKpa']+q['verticalIncrementKpa']
              if slope['verticalIncrementKpa'] is not None else None)})
    return {'schemaVersion':METHOD,'profiles':profiles,'slopeCohesionCandidateKpa':slope_c,
      'source':'JGJ120 section 3.4 horizontal loads, conditioned on unverified soil strength',
      'slopeMethodApplicable':all(p['slope']['verticalIncrementKpa'] is not None for p in profiles),
      'engineeringQualified':False}
