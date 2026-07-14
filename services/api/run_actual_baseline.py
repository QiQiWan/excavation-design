import json
from pathlib import Path
from collections import Counter, defaultdict
from app.schemas.domain import Project, DesignSettings, Polyline2D, Point2D
from app.services.borehole_import import read_csv_bytes, parse_borehole_rows
from app.geology.model_builder import build_geological_model_from_boreholes
from app.services.excavation_service import make_excavation_model
from app.services.design_service import auto_diaphragm_wall, auto_supports, support_layout_config_from_settings
from app.calculation.engine import build_default_construction_cases, run_calculation

root=Path('/mnt/data/actual_import_bundle/actual-project')
print('read csv', flush=True)
rows=read_csv_bytes((root/'actual_project_boreholes_24x6layers.csv').read_bytes())
print('parse', flush=True)
imp=parse_borehole_rows(rows, source_file='actual_project_boreholes_24x6layers.csv')
assert imp.success, imp.errors
payload=json.loads((root/'actual_project_excavation_payload.json').read_text(encoding='utf-8'))
settings=DesignSettings(
    autoCenterExcavationOnGeology=False,
    groundwaterLevel=-20.0,
    surcharge=20.0,
    minimumSegmentLength=0.5,
    supportLevelDepthsM=[0.0,4.0,7.2,10.3,13.3],
)
p=Project(name='丰收湖项目基坑工程',location='Local engineering coordinates',designSettings=settings)
p.boreholes=imp.boreholes;p.strata=imp.strata
print('geology', flush=True)
p.geological_model=build_geological_model_from_boreholes(p.boreholes,grid_size=10)
outline=Polyline2D(points=[Point2D(**pt) for pt in payload['outline']['points']],closed=True)
print('excavation', flush=True)
p.excavation=make_excavation_model(payload['name'],outline,payload['topElevation'],payload['bottomElevation'],settings.minimum_segment_length)
p.excavation.explicit_placement=True
p.excavation.support_axis_offset=payload['supportAxisOffset'];p.excavation.basement_wall_offset=payload['basementWallOffset']
print('wall', flush=True)
p.retaining_system=auto_diaphragm_wall(p.excavation,None)
print('supports', flush=True)
p.retaining_system=auto_supports(p.excavation,p.retaining_system,support_layout_config_from_settings(settings))
for w in p.retaining_system.diaphragm_walls:w.bottom_elevation=-32.8
print('cases', flush=True)
p.calculation_cases=build_default_construction_cases(p)
print('segments',len(p.excavation.segments),'walls',len(p.retaining_system.diaphragm_walls),'supports',len(p.retaining_system.supports),'columns',len(p.retaining_system.columns),'cases',len(p.calculation_cases))
print('calculate', flush=True)
r=run_calculation(p,None,auto_repair=True,include_candidate_comparison=False)
p.calculation_results.append(r)
print('summary',r.check_summary)
print('gov',r.governing_values.model_dump(by_alias=True))
print('design review',r.design_review_summary.model_dump(by_alias=True) if r.design_review_summary else None)
fail=[c for c in r.checks if c.get('status')=='fail']
print('fail rules',Counter(c.get('ruleId') for c in fail))
print('fail objs',Counter(c.get('objectId') for c in fail).most_common(20))
for c in fail[:60]:
    print(c.get('ruleId'),c.get('objectId'),c.get('stageName'),c.get('calculatedValue'),c.get('limitValue'),c.get('unit'),c.get('message'))
print('walls:')
for w in p.retaining_system.diaphragm_walls:
    dr=w.design_results
    print(w.panel_code,w.segment_id,w.thickness,w.concrete_grade,dr.max_moment_design,dr.required_reinforcement_area,dr.provided_reinforcement_area,dr.moment_capacity,dr.max_displacement,dr.check_status)
Path('/mnt/data/fengshou_baseline_project_v313.json').write_text(p.model_dump_json(by_alias=True),encoding='utf-8')
