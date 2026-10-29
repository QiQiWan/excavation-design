#!/usr/bin/env python3
"""Reproduce first mixed-method failure without bypassing the restraint guard.
Set PYTHONPATH to an existing OpenSees installation. On fixed versions no suspect
stations will be emitted; historical diagnostic JSON is retained separately.
"""
import sys,json,runpy,math
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'services/api'))
from app.section.models import freeze_section
from app.section.preprocess import build_solver_model
import app.section.independent as independent
fixture=runpy.run_path(str(root/'scripts/validate-section-method-matrix.py'))['fixture']
physical=build_solver_model(freeze_section(fixture('carry_plastic_history_v1')))
physical['stages']=physical['stages'][:3]
(root/'docs/releases/V3_87_18_evidence/mixed_failure_physical.json').write_text(json.dumps(physical,indent=2))
original=independent._check_final_tangent_restraint
captured=[]
def diagnose(nodes,toe,soils,supports):
 try:return original(nodes,toe,soils,supports)
 except Exception:
  for soil in soils:
   lo,up,tr=soil['lowerKn'],soil['upperKn'],soil['trialPressureKn']
   scale=max(abs(tr),abs(lo),abs(up),abs(soil['p0Kn']),abs(soil['kKnPerM']*soil['localDisplacementM']),abs(soil['kKnPerM']*soil['previousPlasticDisplacementM']),math.ulp(0.))
   distance=min(tr-lo,up-tr)
   if up!=lo and lo<=tr<=up and distance>64*math.ulp(1.)*scale and soil['tangentKnPerM']<=0:
    captured.append({**soil,'scaleKn':scale,'boundDistanceKn':distance,'roundoff64UlpKn':64*math.ulp(1.)*scale,
      'boundDistanceOverGuardTolerance':distance/(64*math.ulp(1.)*scale),
      'eps0':soil['previousPlasticDisplacementM']-(soil['p0Kn']-lo)/soil['kKnPerM'],
      'yieldStrain':(up-lo)/soil['kKnPerM'],
      'nativePressureDifferenceKn':soil['pressureKn']-tr})
  raise
independent._check_final_tangent_restraint=diagnose
result=independent.solve_stages(physical)
record={'diagnosticOnly':True,'guardNotBypassed':True,'failedStage':next((s['id'] for s in result['stages'] if s['status']=='failed'),None),'failureReason':result.get('failureReason'),'suspectStations':captured,'physicalFile':'mixed_failure_physical.json'}
(root/'docs/releases/V3_87_18_evidence/mixed_failure_diagnostic_rerun.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record,indent=2))
