"""Cage constructability check with explicit criteria and no fabricated joints.

Scope: longitudinal cage geometry and provided hoop distribution. A clear
geometric check does not prove welded laps, anchorage or node installation.
"""
from __future__ import annotations
from math import pi,sin,isfinite


def circular_cage_audit(model)->dict:
    cage=model.cage
    raw=(model.engineering_design or {}).get('cage_construction')
    if not raw:
        return {'status':'missing_input','requiredInputs':['engineering_design.cage_construction'],
               'checked':{},'engineeringQualified':False,'formalDesignReady':False}
    from app.section.engineering_models import CageConstruction
    d=CageConstruction.model_validate(raw)
    radius=model.pile.diameter_m*500
    bar_axis=radius-cage.bar_axis_cover_mm
    clear_bar=2*bar_axis*sin(pi/cage.bar_count)-cage.bar_diameter_mm
    cover=cage.bar_axis_cover_mm-cage.bar_diameter_mm/2-cage.stirrup_diameter_mm
    steel_area=cage.bar_count*pi*cage.bar_diameter_mm**2/4
    ratio=steel_area/(pi*radius**2)
    checks={
        'actual_longitudinal_clear_spacing':{'actualMm':clear_bar,'minimumMm':d.minimum_clear_bar_spacing_mm},
        'outer_hoop_clear_cover':{'actualMm':cover,'minimumMm':d.minimum_hoop_cover_mm},
        'hoop_diameter':{'actualMm':cage.stirrup_diameter_mm,'minimumMm':d.minimum_hoop_diameter_mm},
        'ordinary_hoop_max_pitch':{'actualMm':cage.stirrup_spacing_mm,'maximumMm':d.maximum_middle_pitch_mm},
        'top_confined_zone_pitch':{'actualMm':d.actual_top_end_pitch_mm,'maximumMm':d.maximum_end_pitch_mm},
        'bottom_confined_zone_pitch':{'actualMm':d.actual_bottom_end_pitch_mm,'maximumMm':d.maximum_end_pitch_mm},
        'top_zone_length':{'actualMm':d.actual_top_end_zone_length_mm,'minimumMm':d.required_top_end_zone_length_mm},
        'bottom_zone_length':{'actualMm':d.actual_bottom_end_zone_length_mm,'minimumMm':d.required_bottom_end_zone_length_mm},
        # Declared end-zone lengths must fit the actual total steel cage length.
        # Simultaneous end-zone overlap cannot silently become constructible.
        'two_end_zones_fit_in_pile':{'actualMm':d.actual_top_end_zone_length_mm+d.actual_bottom_end_zone_length_mm,
                                     'maximumMm':model.pile.length_m*1000},
    }
    for row in checks.values():
        x=row['actualMm'];bound=row.get('minimumMm',row.get('maximumMm'))
        if not isfinite(x) or not isfinite(bound):raise ValueError('nonfinite_cage_dimensions')
        row['status']='pass' if (x>=bound if 'minimumMm' in row else x<=bound) else 'fail'
    # End-zone hoop pitch and arrangement are explicitly declared project data;
    # current BIM/DXF exporters still do NOT model each installed hoop station.
    fail=any(row['status']=='fail' for row in checks.values())
    unverified=['physical_closed_hoop_joint_or_weld','real_hoop_station_BIM_DXF_alignment',
        'cage_splice_and_lift_handling','support_to_crown_node_confinement',
        'actual_pile_cage_position_and_installation']
    return {'schemaVersion':'pitguard.circular-cage-construction/1',
        'status':'fail' if fail else 'calculated_scope_limited',
        'checked':checks,'longitudinalSteelAreaMm2':steel_area,'longitudinalRatio':ratio,
        'criteriaBasis':d.criteria_basis,'fabricationBasis':d.fabrication_basis,
        'remainingChecks':unverified,'engineeringQualified':False,
        'formalDesignReady':False,'formalConstructionIssue':False,
        'limitations':['End-zone dimensions are declared values; no as-built survey or full hoop-by-hoop geometry available',
            'Minimum geometric checks do not establish lap, hooks, anchorage, splice or construction drawing compliance']}
