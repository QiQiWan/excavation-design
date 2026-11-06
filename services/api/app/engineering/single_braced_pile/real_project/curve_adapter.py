"""Normalize the *computed* research data to the full-channel comparer.

This is a structural adapter. It cannot generate the missing FRWS original
point arrays and cannot be used as an original/native numerical export.
"""
from __future__ import annotations
from copy import deepcopy
from math import isfinite


def normalize_research_candidate(candidate:dict)->dict:
    if candidate.get('modelStatus')!='conditional_research_only':
        raise ValueError('Only the explicit conditional research model is supported')
    out=deepcopy(candidate)
    for stage in out.get('stages',[]):
        if stage.get('status')!='success':continue
        points=stage.get('soilPoints') if stage.get('soilPoints') is not None else stage.get('pressureStations')
        if not isinstance(points,list) or not points:
            raise ValueError('Computed stage must include actual soil pressure records')
        rows=[]
        for pt in points:
            if pt.get('side') not in ('inside','outside') or pt.get('pressureKind') is None:
                raise ValueError('Pressure side or kind is undefined')
            pressure=pt.get('pressureKpa');depth=pt.get('depthSiteM')
            if any(type(x) is bool or not isinstance(x,(int,float)) or not isfinite(x)
                   for x in (pressure,depth)):
                raise ValueError('Nonfinite computed pressure station')
            rows.append({'depthSiteM':depth,'side':pt['side'],
                         'pressureKind':pt['pressureKind'],'pressureKpa':pressure})
        # The nonlinear solver reports two shape-function contributions at
        # the same Gauss depth. These are force quadrature terms, not two
        # independent physical pressure measurement stations. Collapse only
        # identical depth/side/kind by weight-preserving averaging; this
        # maintains integral (pressure x effective width x weight) exactly.
        if stage.get('soilPoints') is not None:
            groups={}
            for src,row in zip(points,rows):
                weight=src.get('weightM')
                width=src.get('projectionWidthM')
                if (not isinstance(weight,(int,float)) or weight<=0 or
                    not isinstance(width,(int,float)) or width<=0 or
                    not isfinite(weight) or not isfinite(width)):
                    raise ValueError('Physical pressure quadrature weights or widths are missing')
                key=(round(row['depthSiteM'],8),row['side'],row['pressureKind'])
                record=groups.setdefault(key,{'sumPressureTimesWeight':0.,
                   'sumWeight':0.,'physicalProjectionWidthM':width,'sourceContributionCount':0})
                if abs(record['physicalProjectionWidthM']-width)>1e-9:
                    raise ValueError('Contradictory physical projection widths at one station')
                record['sumPressureTimesWeight']+=row['pressureKpa']*weight
                record['sumWeight']+=weight
                record['sourceContributionCount']+=1
            stage['pressureStations']=[{'depthSiteM':key[0],
                  'side':key[1],'pressureKind':key[2],
                  'pressureKpa':v['sumPressureTimesWeight']/v['sumWeight'],
                  'quadratureWeightM':v['sumWeight'],
                  'projectionWidthM':v['physicalProjectionWidthM'],
                  'sourceContributionCount':v['sourceContributionCount']}
                 for key,v in sorted(groups.items())]
            stage['pressureIntegrationPreserved']=True
        else:
            stage['pressureStations']=rows
        supports=stage.get('strutReactions')
        if not isinstance(supports,list):
            raise ValueError('Computed stage must explicitly provide support reactions')
        ids=[x.get('id') for x in supports]
        if len(ids)!=len(set(ids)) or any(not isinstance(s,str) or not s for s in ids):
            raise ValueError('Computed support identities missing or repeated')
        if 'activeSupportIds' in stage and stage['activeSupportIds']!=ids:
            raise ValueError('Computed support inventory contradicts the reaction records')
        stage['activeSupportIds']=ids
    out['curveAdapter']='conditional_computed_records_only/1'
    out['isOriginalNativeNumericExport']=False
    return out
