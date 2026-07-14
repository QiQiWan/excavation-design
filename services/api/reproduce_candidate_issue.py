import json, time
from pathlib import Path
from collections import Counter
from app.schemas.domain import Project, DesignSettings, Polyline2D, Point2D
from app.services.borehole_import import read_csv_bytes, parse_borehole_rows
from app.geology.model_builder import build_geological_model_from_boreholes
from app.services.excavation_service import make_excavation_model
from app.services.design_service import auto_diaphragm_wall, auto_supports, support_layout_config_from_settings
from app.services.support_layout_repair import auto_repair_support_layout, adopt_support_layout_candidate
from app.calculation.engine import build_default_construction_cases, run_calculation, run_single_candidate_calculation
root=Path('/mnt/data/actual_import_bundle/actual-project')
imp=parse_borehole_rows(read_csv_bytes((root/'actual_project_boreholes_24x6layers.csv').read_bytes()), source_file='actual.csv')
payload=json.loads((root/'actual_project_excavation_payload.json').read_text())
settings=DesignSettings(autoCenterExcavationOnGeology=False,groundwaterLevel=-20.0,surcharge=20.0,minimumSegmentLength=0.5,supportLevelDepthsM=[0.0,4.0,7.2,10.3,13.3])
p=Project(name='丰收湖复现',designSettings=settings)
p.boreholes=imp.boreholes;p.strata=imp.strata
p.geological_model=build_geological_model_from_boreholes(p.boreholes,grid_size=30)
p.excavation=make_excavation_model(payload['name'],Polyline2D(points=[Point2D(**x) for x in payload['outline']['points']],closed=True),0,-16.6,0.5)
p.excavation.explicit_placement=True;p.excavation.support_axis_offset=1.0;p.excavation.basement_wall_offset=1.5
p.retaining_system=auto_diaphragm_wall(p.excavation)
p.retaining_system=auto_supports(p.excavation,p.retaining_system,support_layout_config_from_settings(settings))
for w in p.retaining_system.diaphragm_walls:w.bottom_elevation=-32.8
p.calculation_cases=build_default_construction_cases(p)
print('initial',len(p.retaining_system.supports),len(p.retaining_system.columns))
t=time.time(); rep=auto_repair_support_layout(p); print('repair',time.time()-t,rep.status,rep.score_before,rep.score_after,len(rep.candidates),len(p.retaining_system.supports),len(p.retaining_system.columns))
for i,c in enumerate(rep.candidates[:3]):
 t=time.time(); row=run_single_candidate_calculation(p,c,index=i,use_cache=False); print('candidate',i,time.time()-t,row); c.full_calculation=row
sel=rep.candidates[0]
print('adopt',sel.id)
adopt_support_layout_candidate(p,sel.id)
print('after adopt',len(p.retaining_system.supports),len(p.retaining_system.columns),'cases',len(p.calculation_cases))
t=time.time(); r=run_calculation(p,None,auto_repair=True); print('calc',time.time()-t,r.check_summary,r.governing_values.model_dump(by_alias=True))
print('diag',r.design_iteration_summary.get('calculationDiagnostics',{}).get('supportTopologySynchronization'))
print('fail rules',Counter(c.get('ruleId') for c in r.checks if c.get('status')=='fail'))
Path('/mnt/data/reproduce_v314_project.json').write_text(p.model_dump_json(by_alias=True),encoding='utf-8')
