import json
from pathlib import Path
from collections import Counter
from app.schemas.domain import Project, DesignSettings, Polyline2D, Point2D, ConstructionStage, CalculationCase
from app.services.borehole_import import read_csv_bytes, parse_borehole_rows
from app.geology.model_builder import build_geological_model_from_boreholes
from app.services.excavation_service import make_excavation_model
from app.services.design_service import auto_diaphragm_wall, auto_supports, support_layout_config_from_settings
from app.calculation.engine import run_calculation, _support_topology_hash
root=Path('/mnt/data/actual_import_bundle/actual-project')
imp=parse_borehole_rows(read_csv_bytes((root/'actual_project_boreholes_24x6layers.csv').read_bytes()), source_file='actual.csv')
payload=json.loads((root/'actual_project_excavation_payload.json').read_text())
settings=DesignSettings(autoCenterExcavationOnGeology=False,groundwaterLevel=-20.0,surcharge=20.0,minimumSegmentLength=0.5,supportLevelDepthsM=[0.0,4.0,7.2,10.3,13.3])
p=Project(name='丰收湖项目基坑工程',designSettings=settings)
p.boreholes=imp.boreholes;p.strata=imp.strata
p.geological_model=build_geological_model_from_boreholes(p.boreholes,grid_size=30)
p.excavation=make_excavation_model(payload['name'],Polyline2D(points=[Point2D(**x) for x in payload['outline']['points']],closed=True),0,-16.6,0.5)
p.excavation.explicit_placement=True;p.excavation.support_axis_offset=1.0;p.excavation.basement_wall_offset=1.5
p.retaining_system=auto_diaphragm_wall(p.excavation)
p.retaining_system=auto_supports(p.excavation,p.retaining_system,support_layout_config_from_settings(settings))
for w in p.retaining_system.diaphragm_walls:w.bottom_elevation=-32.8
h=_support_topology_hash(p)
st=ConstructionStage(name='Final excavation',excavationElevation=-16.6,activeSupportIds=[s.id for s in p.retaining_system.supports],activeSupportLevels=sorted({s.level_index for s in p.retaining_system.supports}),supportTopologyHash=h,stageType='final',groundwaterLevelInside=-20.0,groundwaterLevelOutside=-20.0,surcharge=20.0)
case=CalculationCase(name='final only',stages=[st],supportTopologyHash=h)
p.calculation_cases=[case]
print('prepared',len(p.retaining_system.supports), flush=True)
r=run_calculation(p,case,auto_repair=False)
print('summary',r.check_summary)
print('gov',r.governing_values.model_dump(by_alias=True))
fail=[c for c in r.checks if c.get('status')=='fail']
print('rules',Counter(c.get('ruleId') for c in fail))
for c in fail[:80]: print(c.get('ruleId'),c.get('objectId'),c.get('calculatedValue'),c.get('limitValue'),c.get('message'))
for w in p.retaining_system.diaphragm_walls:
 d=w.design_results
 print('W',w.segment_id,w.thickness,w.concrete_grade,d.max_moment_design,d.required_reinforcement_area,d.provided_reinforcement_area,d.moment_capacity,d.max_displacement,d.check_status)
Path('/mnt/data/fengshou_final_only_v313.json').write_text(p.model_dump_json(by_alias=True),encoding='utf-8')
