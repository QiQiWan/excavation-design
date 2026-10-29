#!/usr/bin/env python3
"""Probe installed ElasticPP yield band; no solver changes or tolerance overrides."""
import json, math, sys
from pathlib import Path
import openseespy.opensees as ops
root=Path(__file__).resolve().parents[1]
epsilon=math.ulp(1.0)
rows=[]
# Binary-exact unit stiffness and yield strain expose the strain epsilon band.
for side in ('upper','lower'):
 for multiples in (0.,.25,.5,1.,2.,4.,8.):
  ops.wipe()
  ops.uniaxialMaterial('ElasticPP',1,1.,.5,-.5)
  ops.testUniaxialMaterial(1)
  strain=(.5-multiples*epsilon)*(1 if side=='upper' else -1)
  ops.setStrain(strain)
  rows.append({'side':side,'requestedInteriorDistanceOverEpsilon':multiples,
   'strain':strain,'actualInteriorDistanceOverEpsilon':(.5-abs(strain))/epsilon,
   'stress':ops.getStress(),'tangent':ops.getTangent()})
record={'schemaVersion':'pitguard.native-yield-band-probe/1','openseesVersion':ops.version(),
 'pythonVersion':sys.version,'DBL_EPSILON':epsilon,'E':1.,'yieldStrainPositive':.5,'yieldStrainNegative':-.5,
 'source':'https://github.com/OpenSees/OpenSees/blob/master/SRC/material/uniaxial/ElasticPPMaterial.cpp',
 'rule':'fYieldSurface=-E*DBL_EPSILON; f<=fYieldSurface => elastic tangent E, else yielded tangent zero',
 'results':rows}
record['sourceRuleMatches']=all(row['tangent']==(1. if row['actualInteriorDistanceOverEpsilon']>=1. else 0.) for row in rows)
path=root/'docs/releases/V3_87_18_evidence/native_yield_band_probe.json'
path.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
raise SystemExit(0 if record['sourceRuleMatches'] else 1)
