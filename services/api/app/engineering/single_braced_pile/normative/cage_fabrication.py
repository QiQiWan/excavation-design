"""V3.88.6 deterministic, element-addressable reinforcing cage geometry.

Coordinates in millimetres. z=0 is pile top, positive downward. The reported
mass is the theoretical reinforcing steel mass using project-declared density,
without ties, connectors, cages chairs, tolerances or waste. Not a verified
shop drawing, anchorage design, lifting plan or weld/connector approval.
"""
from __future__ import annotations
from math import ceil,cos,isfinite,pi,sin
from app.section.engineering_models import CageFabrication


def _stations(start:float,end:float,pitch:float)->list[float]:
    if end < start or pitch<=0:raise ValueError('Invalid hoop interval')
    if end==start:return [round(start,4)]
    intervals=ceil((end-start)/pitch)
    if intervals>2000:raise ValueError('Hoop count exceeds verifiable limit')
    return [round(start+(end-start)*i/intervals,4) for i in range(intervals+1)]


def cage_fabrication_review(model)->dict:
    design=model.engineering_design or {}
    raw=design.get('cage_fabrication')
    if not raw:
        return {'status':'missing_input','requiredInputs':['engineering_design.cage_fabrication'],
          'geometry':None,'formalDesignReady':False,'formalConstructionIssue':False}
    spec=CageFabrication.model_validate(raw)
    base=design.get('cage_construction')
    if not base:
        return {'status':'missing_input','requiredInputs':['engineering_design.cage_construction'],
          'geometry':None,'formalDesignReady':False,'formalConstructionIssue':False}
    from app.section.engineering_models import CageConstruction
    layout=CageConstruction.model_validate(base)
    cage=model.cage
    L=model.pile.length_m*1000
    R=model.pile.diameter_m*500
    bar_r=R-cage.bar_axis_cover_mm
    hoop_r=bar_r+cage.bar_diameter_mm/2+cage.stirrup_diameter_mm/2
    if hoop_r+cage.stirrup_diameter_mm/2>=R:raise ValueError('Hoop geometrically outside pile')
    theta0=pi*(cage.bar_start_angle_deg or 0)/180
    bars=[{'id':f'LG-{i+1:03d}','barIndex':i,'xMm':round(bar_r*cos(theta0+2*pi*i/cage.bar_count),4),
           'yMm':round(bar_r*sin(theta0+2*pi*i/cage.bar_count),4),
           'zStartMm':0.,'zEndMm':L,'diameterMm':cage.bar_diameter_mm}
          for i in range(cage.bar_count)]
    top=layout.actual_top_end_zone_length_mm
    bottom=layout.actual_bottom_end_zone_length_mm
    checks=[]
    def check(code,actual,operator,limit):
        okay=(actual<=limit+1e-8 if operator=='<=' else actual>=limit-1e-8)
        checks.append({'code':code,'actual':actual,'operator':operator,'limit':limit,
            'status':'pass' if okay else 'fail'})
    check('confinement_zones_within_pile',top+bottom,'<=',L)
    if top+bottom>L:
        # An impossible geometry is not drawn or granted a nominal station count.
        return {'schemaVersion':'pitguard.cage-fabrication/3.88.6','status':'fail',
          'checks':checks,'geometry':None,'formalDesignReady':False,
          'formalConstructionIssue':False}
    hoop_pts=_stations(0,top,layout.actual_top_end_pitch_mm)
    hoop_pts+=_stations(top,L-bottom,cage.stirrup_spacing_mm)[1:]
    hoop_pts+=_stations(L-bottom,L,layout.actual_bottom_end_pitch_mm)[1:]
    hoop_pts=sorted(set(hoop_pts))
    # The actual loop position and bar radius are explicit. No arbitrary
    # drawing-only longitudinal bar substitution is permitted.
    hoops=[{'id':f'HP-{i+1:04d}','zMm':z,'centerlineRadiusMm':round(hoop_r,4),
         'diameterMm':cage.stirrup_diameter_mm} for i,z in enumerate(hoop_pts)]
    check('total_segment_length',sum(spec.segment_lengths_mm),'>=',L-.001)
    check('segment_length_not_exceed_pile',sum(spec.segment_lengths_mm),'<=',L+.001)
    for i,l in enumerate(spec.segment_lengths_mm):
        check(f'segment_{i+1}_transport_length',l,'<=',spec.max_transport_length_mm)
        check(f'segment_{i+1}_stock_length',l,'<=',spec.max_bar_stock_length_mm)
    check('top_anchor_available',spec.available_top_anchorage_mm,'>=',spec.required_top_anchorage_mm)
    check('bottom_anchor_available',spec.available_bottom_anchorage_mm,'>=',spec.required_bottom_anchorage_mm)
    check('node_clearance',spec.available_node_clearance_mm,'>=',spec.required_node_clearance_mm)
    # Hoop stations are native model data, not merely a pitch attribute.
    A_long=cage.bar_count*pi*cage.bar_diameter_mm**2/4
    A_hoop=pi*cage.stirrup_diameter_mm**2/4
    hoop_length=2*pi*hoop_r
    kg_per_mm3=spec.steel_density_kg_m3/1e9
    total_long_mass=A_long*L*kg_per_mm3
    total_hoop_mass=len(hoop_pts)*A_hoop*hoop_length*kg_per_mm3
    cumulative=0.;boundaries=[];segment_masses=[]
    for i,l in enumerate(spec.segment_lengths_mm):
        first=cumulative;last=cumulative+l;cumulative=last
        n_hoops=sum(1 for z in hoop_pts if first-1e-8<=z<(last-1e-8 if i<len(spec.segment_lengths_mm)-1 else last+1e-8))
        lower_bound=(A_long*l+n_hoops*A_hoop*hoop_length)*kg_per_mm3
        segment_masses.append(round(lower_bound,5))
        check(f'segment_{i+1}_minimum_steel_mass_for_lift',lower_bound,'<=',spec.max_lift_segment_mass_kg)
        if i<len(spec.segment_lengths_mm)-1:boundaries.append(last)
    # Every longitudinal bar must receive one documented splice within the
    # window of each cage-section interface. Unknown connectors block approval.
    splices=[{'barIndex':s.bar_index,'stationMm':s.station_mm,'method':s.method,
              'sourceReference':s.source_reference} for s in spec.splices]
    check('splice_indices_in_cage',sum(s['barIndex']>=cage.bar_count for s in splices),'<=',0)
    check('duplicate_splice_identifiers',len(splices)-len({(s['barIndex'],s['stationMm']) for s in splices}),'<=',0)
    for i,b in enumerate(boundaries):
        relevant=[s for s in splices if abs(s['stationMm']-b)<=spec.splice_interface_window_mm]
        for bar in range(cage.bar_count):
            count=sum(s['barIndex']==bar for s in relevant)
            check(f'interface_{i+1}_bar_{bar+1}_exactly_one_splice',count,'>=',1)
            check(f'interface_{i+1}_bar_{bar+1}_no_duplicate_splice',count,'<=',1)
        positions=sorted(s['stationMm'] for s in relevant)
        max_group=0
        for j,p in enumerate(positions):
            max_group=max(max_group,sum(x<p+spec.minimum_splice_stagger_mm for x in positions[j:]))
        check(f'interface_{i+1}_simultaneous_splice_ratio',
              max_group/max(1,cage.bar_count),'<=',spec.max_same_station_fraction)
    for j,s in enumerate(splices):
        check(f'splice_{j+1}_within_cage',s['stationMm'],'<=',L)
        check(f'splice_{j+1}_away_from_top_anchor',s['stationMm'],'>=',spec.minimum_splice_end_distance_mm)
        check(f'splice_{j+1}_away_from_bottom_anchor',L-s['stationMm'],'>=',spec.minimum_splice_end_distance_mm)
        check(f'splice_{j+1}_has_matching_method',int(s['method']==spec.connection_method),'>=',1)
    failed=[r['code'] for r in checks if r['status']=='fail']
    return {'schemaVersion':'pitguard.cage-fabrication/3.88.6',
      'status':'fail' if failed else 'calculated_scope_limited',
      'checks':checks,'failedChecks':failed,
      'geometry':{'coordinateSystem':'pile_top_z_positive_down_mm',
                  'longitudinalBars':bars,'hoops':hoops,'splices':splices,
                  'hoopCount':len(hoops),'barCount':len(bars),
                  'steelVolumeMm3':(A_long*L+len(hoops)*A_hoop*hoop_length)},
      'quantities':{'theoreticalLongitudinalKg':round(total_long_mass,5),
                    'theoreticalHoopKg':round(total_hoop_mass,5),
                    'theoreticalTotalKg':round(total_long_mass+total_hoop_mass,5),
                    'minimumSegmentMassKg':segment_masses,
                    'unitBasis':'kg using explicit steel density; no accessories, lap or waste'},
      'criteriaBasis':spec.criteria_basis,'fabricationBasis':spec.fabrication_basis,
      'connectionBasis':spec.connection_basis,'nodeBasis':spec.node_basis,
      'unverified':['connector_or_weld_capacity_and_acceptance',
                    'actual_hoop_bending_hooks_and_closed_weld',
                    'lift_transport_rigging_and_cage_deformation',
                    'BIM_DXF_shop_drawing_station_alignment',
                    'as_built_joint_and_concrete_cover_measurement'],
      'engineeringQualified':False,'formalDesignReady':False,'formalConstructionIssue':False}
