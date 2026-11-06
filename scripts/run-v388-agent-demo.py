#!/usr/bin/env python3
"""Offline, synthetic A5/A6 demo. Never promotes a candidate or issues a design."""
from __future__ import annotations
from pathlib import Path
import argparse,json,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services'/'api'))
from app.agents.contracts import StartRun
from app.agents.engine import AgentRuntime
from app.agents.store import AgentStore
from app.engineering.single_braced_pile.repository import Repository


def main():
    p=argparse.ArgumentParser(description='V3.88 one-strut Agent synthetic verification run')
    p.add_argument('--output',default=str(ROOT/'verification-output'/'agent-v388-demo'))
    p.add_argument('--candidates',type=int,default=3,choices=range(1,6))
    args=p.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    repo=Repository(root=out/'immutable-results')
    store=AgentStore(out/'agent-demo.sqlite3')
    input_file=ROOT/'services'/'api'/'app'/'engineering'/'single_braced_pile'/'assets'/'verification_example.json'
    data=json.loads(input_file.read_text(encoding='utf-8'))
    source=repo.compute(data)
    runtime=AgentRuntime(store=store,repo=repo)
    run=runtime.create(StartRun(result_hash=source['resultHash'],max_candidates=args.candidates),actor='synthetic-demo')
    finished=runtime.process_until_idle(run['id'])
    summary={'schemaVersion':'pitguard.agent-demo/1','sourceResultHash':source['resultHash'],
        'sourceSnapshotHash':source['snapshotHash'],'sourceDisplacementMm':source['checks']['displacement']['valueMm'],
        'runId':run['id'],'status':finished['status'],'candidates':store.candidates(run['id']),
        'events':store.events(run['id']),'realEngineeringReview':False,
        'formalConstructionIssue':False}
    target=out/'agent-demo-result.json';target.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print('Agent run:',finished['status'])
    for c in summary['candidates']:
        print('  ',c['patch']['field'],c['patch']['after'],c['status'],
              c['metrics'].get('candidateDisplacementMm'),'mm')
    print('No engineering issue permission. Output:',target)
    return 0 if finished['status']=='awaiting_review' else 1


if __name__=='__main__':raise SystemExit(main())
