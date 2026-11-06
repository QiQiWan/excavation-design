#!/usr/bin/env python3
"""Three synthetic mixed-water/history checks with real independent OpenSees.

No production changes, mesh campaigns, parameter fitting, plots or packaging.
Run with the primary Python and --opensees-path pointing to the installed optional
backend. Native MPI/OpenSees may require an unrestricted subprocess environment.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services'/'api'))

POLICIES=('carry_plastic_history_v1','reset_plastic_offset_to_zero_v1',
          'rebase_soil_at_current_displacement_v1')


def fixture(policy):
    from app.section.examples import verification_example_v2
    data=verification_example_v2()
    data.update(section_id='MIXED-HISTORY-'+policy,name='Synthetic mixed-water history: '+policy,
                comparison_reference=None,engineering_design=None,reference_contract_hash=None)
    # This is a defined fixture boundary, not numerical regularization or a
    # claim about the historical project's toe. It prevents rigid mechanisms
    # even when phi_u=0 soils mobilize both pressure bounds.
    data['pile']['toe_boundary']='fixed'
    layer=data['soil_layers'][1]
    layer.update(name='Synthetic total-stress undrained layer',phi_deg=0.,cohesion_kpa=10.,k0=1.)
    methods=data['method_extensions']
    methods['layer_stress_modes'][1].update(mode='total_combined',parameter_basis='total',
        basis='Synthetic total-stress phi_u=0, c_u=10 kPa and K0=1; not inferred from A3-3 effective parameters.')
    checkpoint=deepcopy(data['stages'][3])
    checkpoint.update(id='HISTORY',name='Independent history checkpoint: '+policy,
        event='water_or_load' if policy=='carry_plastic_history_v1' else 'soil_reset')
    checkpoint['install_support_ids']=[];checkpoint['remove_support_ids']=[]
    data['stages'].insert(4,checkpoint)
    methods['stage_options'].insert(4,{'stage_id':'HISTORY','active_load_ids':['Q-RECT'],
        'soil_history_action':policy,'history_basis':'Synthetic isolated history checkpoint; geometry, water, loads and installed supports are unchanged.'})
    data['evidence'].append({'parameter':'mixed_layer_and_history_fixture','kind':'verification_definition',
        'source':'scripts/validate-section-method-matrix.py','source_sha256':None,'page':None,
        'note':'L1 effective, L2 explicit total phi_u=0/cu10/K0=1, fixed toe chosen as a physical boundary; never a weak spring. Three policies differ only at HISTORY.'})
    data['limitations']=[
        'Synthetic verification only; not a reconstruction of original A3-3 or FRWS settings.',
        'L1 effective-stress sand; L2 total-stress undrained phi_u=0, cu=10 kPa, K0=1.',
        'Fixed toe is explicitly imposed for all three cases; there is no weak-spring stabilization.',
        'The carry checkpoint is a no-change water_or_load event because soil_reset requires a non-carry policy.',
        'Finite rectangle remains q30 kPa, x4.3..16.3m, y-32.5..32.5m with explicit centered-position assumption.',
        'All other original synthetic fixture parameters are unchanged; no peak matching or parameter calibration.',
        'Engineering reinforcement groups are not selected in this numerical-method matrix.',
    ]
    return data


def summarize_stages(result):
    output=[]
    for stage in result.get('stages',[]):
        soils=stage.get('soilStates') or []
        output.append({'stageId':stage['id'],'status':stage['status'],
            'failureReason':stage.get('failureReason'),
            'maxAbsDisplacementMm':stage.get('maxAbsDisplacementMm'),
            'maxAbsMomentKnM':stage.get('maxAbsMomentKnM'),'maxAbsShearKn':stage.get('maxAbsShearKn'),
            'soilStateCount':len(soils),
            'nonzeroPlasticOffsets':sum(abs(s['plasticDisplacementM'])>1e-12 for s in soils),
            'maxAbsPlasticOffsetM':max((abs(s['plasticDisplacementM']) for s in soils),default=0.),
            'supports':[{'id':s['id'],'status':s['status'],'active':s['active'],
                         'referenceDisplacementMm':s.get('referenceDisplacementMm'),
                         'reactionCharacteristicKn':s.get('reactionCharacteristicKn')}
                        for s in stage.get('supports',[])]})
    return output


def maxima(comparison):
    maximum={}
    for stage in comparison.get('stages',[]):
        for name,metric in stage.get('metrics',{}).items():
            if 'maxAbsoluteDifference' not in metric: continue
            row=maximum.setdefault(name,{'maxAbsoluteDifference':0.,'maxNormalizedError':0.,'comparedValueCount':0})
            row['maxAbsoluteDifference']=max(row['maxAbsoluteDifference'],metric['maxAbsoluteDifference'])
            row['maxNormalizedError']=max(row['maxNormalizedError'],metric['maxNormalizedError'])
            row['comparedValueCount']+=metric['count']
    return maximum


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opensees-path',type=Path,help='Existing optional OpenSeesPy installation, not downloaded by this script')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/releases/V3_87_18_evidence/method_matrix.json')
    parser.add_argument('--timeout-seconds',type=int,default=180)
    args=parser.parse_args()
    if args.opensees_path:
        os.environ['PYTHONPATH']=str(args.opensees_path.resolve())+os.pathsep+os.environ.get('PYTHONPATH','')
    from app.section.models import freeze_section
    from app.section.service import analyze_section
    from app.section.benchmarks import run_independent_process,compare_independent,same_snapshot_hand_checks
    evidence={'schemaVersion':'pitguard.synthetic-method-matrix/1','purpose':'verification_fixture',
              'originalProjectEquivalenceClaimed':False,'formalDesignReady':False,
              'scriptSha256':sha256(Path(__file__).read_bytes()).hexdigest(),
              'pythonVersion':sys.version,'openseesPath':str(args.opensees_path) if args.opensees_path else None,
              'scope':'All stages: nodal u/rotation, element end M/V, soil pressures/plastic history, support reactions and installation references; same-snapshot hand checks.',
              'parameterFitting':False,'meshCampaignRun':False,'cases':[]}
    for policy in POLICIES:
        row={'historyPolicy':policy,'status':'failed','definition':fixture(policy)}
        evidence['cases'].append(row)
        print('[method matrix]',policy,flush=True)
        try:
            snapshot=freeze_section(row['definition'])
            result=analyze_section(snapshot)
            row.update(snapshotHash=snapshot.snapshot_hash,analysisHash=snapshot.analysis_hash,
                       resultHash=result['resultHash'],discreteInputHash=result['discreteInputHash'],
                       primaryStatus=result['status'],primaryStages=summarize_stages(result))
            independent=run_independent_process(snapshot,timeout_seconds=args.timeout_seconds)
            comparison=compare_independent(snapshot,result,independent)
            hand=same_snapshot_hand_checks(snapshot,result)
            row.update(independentStatus=independent.get('status'),
                independentStageStatuses=[{'stageId':s['id'],'status':s['status'],'failureReason':s.get('failureReason'),'stability':s.get('stability')} for s in independent.get('stages',[])],
                independentFailureReason=independent.get('failureReason'),independentDiagnostic=independent.get('diagnostic'),
                comparison=comparison,sameSnapshotHandChecks=hand,maximumErrors=maxima(comparison))
            row['status']='passed' if result['status']=='complete' and comparison['status']=='passed' and hand['status']=='passed' else 'failed'
            print('[method matrix]',row['status'],'primary',result['status'],'OpenSees',independent.get('status'),'comparison',comparison['status'],'hand',hand['status'],flush=True)
        except Exception as exc:
            row['exception']=f'{type(exc).__name__}: {exc}'
            print('[method matrix] failed',row['exception'],flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    evidence['status']='passed' if all(row['status']=='passed' for row in evidence['cases']) else 'failed'
    args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('[method matrix] evidence',args.output,flush=True)
    return 0 if evidence['status']=='passed' else 2


if __name__=='__main__':
    raise SystemExit(main())
