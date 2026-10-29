"""Strict 5-stage, full-channel *research* comparison for Luoyu 3-3.

No numerical export is inferred from a plotted curve or a PDF extremum.
Every reference value is a source assertion until the actual file digest and
engineering boundary interpretation are independently verified. Even a
perfect numerical match is not a formal engineering acceptance.
"""
from __future__ import annotations
from math import sqrt,isfinite
import re

STAGES=('S1','S2','S3','S4','S5')
CURVE_FIELDS=('displacementMm','momentKnM','shearKn')
PRESSURE_FIELDS=('pressureKpa',)
BRACE_FIELDS=('reactionKnPerPile','referenceMm')


def _finite(row,fields):
    return all(isinstance(row.get(k),(int,float)) and not isinstance(row.get(k),bool)
               and isfinite(row[k]) for k in fields)


def _index(rows,keyfunc,fields,name,issues):
    if not isinstance(rows,list) or not rows:
        issues.append(name+':missing');return {}
    indexed={}
    for row in rows:
        if not isinstance(row,dict) or not _finite(row,fields):
            issues.append(name+':invalid_values');return {}
        key=keyfunc(row)
        if any(v is None for v in key) or key in indexed:
            issues.append(name+':missing_identity_or_duplicate');return {}
        indexed[key]=row
    return indexed


def _curve_key(row):
    return (round(row.get('depthSiteM',float('nan')),8),row.get('side'))


def _pressure_key(row):
    return (round(row.get('depthSiteM',float('nan')),8),row.get('side'),row.get('pressureKind'))


def _brace_key(row):
    return (row.get('id'),)


def compare_five_stage_full_channels(candidate:dict,original:dict)->dict:
    """Require signed nodal u/M/V, *both* soil sides and every brace reaction.

    Same-grid matching is intentional; extrapolation, inferred control peaks
    and smoothing across a concentrated shear jump are not valid defaults.
    """
    issues=[]
    if original.get('sourceKind')!='native_numeric_export':
        issues.append('original_native_numeric_export')
    if not re.fullmatch(r'[0-9a-f]{64}',str(original.get('fileSha256',''))):
        issues.append('native_file_sha256')
    if original.get('depthDatum')!='original_ground_down_m':
        issues.append('coordinate_datum')
    if original.get('forceBasis')!='single_pile':
        issues.append('force_width_basis')
    if not original.get('methodAndBoundaryConfirmed'):
        issues.append('verified_physical_method_and_boundary')
    if original.get('pressureUnit')!='kPa':
        issues.append('pressure_unit_kPa')
    original_stages=original.get('stages'); candidate_stages=candidate.get('stages')
    for label,stages in [('original',original_stages),('candidate',candidate_stages)]:
        if not isinstance(stages,list) or len(stages)!=5 or [v.get('id') for v in stages]!=list(STAGES):
            issues.append(label+':five_ordered_stages')
    if issues:
        return {'schemaVersion':'pitguard.luoyu-full-channels/1','comparisonStatus':'blocked',
                'rmse':None,'requiredEvidence':sorted(set(issues)),
                'originalFileBytesIndependentlyChecked':False,
                'formalSameConditionVerification':False}
    results=[]
    for st_c,st_r in zip(candidate_stages,original_stages):
        sid=st_r['id'];before=len(issues)
        if st_c.get('status')!='success':issues.append(sid+':candidate_unsolved')
        curves=[]
        # A stage before installation legitimately has zero active braces. Both
        # exports must explicitly declare that fact; an absent array alone
        # cannot distinguish no brace from missing measurement data.
        active_c=st_c.get('activeSupportIds')
        active_r=st_r.get('activeSupportIds')
        if active_c is not None or active_r is not None:
            if (not isinstance(active_c,list) or not isinstance(active_r,list) or
                active_c!=active_r or len(active_c)!=len(set(active_c)) or
                any(not isinstance(x,str) or not x for x in active_c)):
                issues.append(sid+':active_support_identity_or_sequence_mismatch')
        else:
            if not st_c.get('strutReactions') or not st_r.get('strutReactions'):
                issues.append(sid+':explicit_active_support_inventory_required')
        for label,cols,key,fields in [
            ('curve','curve',_curve_key,('depthSiteM',)+CURVE_FIELDS),
            ('pressure','pressureStations',_pressure_key,('depthSiteM',)+PRESSURE_FIELDS),
            ('brace','strutReactions',_brace_key,BRACE_FIELDS)]:
            if label=='brace' and active_c==[] and active_r==[]:
                if st_c.get(cols)!=[] or st_r.get(cols)!=[]:
                    issues.append(sid+':empty_support_stage_requires_empty_reactions')
                continue
            ca=_index(st_c.get(cols),key,fields,sid+':candidate_'+label,issues)
            reference_idx=_index(st_r.get(cols),key,fields,sid+':original_'+label,issues)
            if label=='brace' and active_c is not None and active_r is not None:
                if set(x[0] for x in ca)!=set(active_c) or set(x[0] for x in reference_idx)!=set(active_r):
                    issues.append(sid+':support_reaction_and_active_inventory_disagree')
            if ca and reference_idx:
                if set(ca)!=set(reference_idx):
                    issues.append(sid+':'+label+'_station_or_support_mismatch')
                if label=='pressure':
                    sides={k[1] for k in reference_idx}
                    if sides!={'inside','outside'}:
                        issues.append(sid+':both_pressure_sides_required')
                if label=='curve':
                    if not set(k[1] for k in reference_idx).issubset({'above','below'}):
                        issues.append(sid+':invalid_shear_side')
                if label=='brace':
                    if any(not k[0] or not isinstance(k[0],str) for k in reference_idx):
                        issues.append(sid+':brace_identity_missing')
                curves.append((label,ca,reference_idx,fields[1:] if label!='brace' else BRACE_FIELDS))
        if len(issues)>before:continue
        channel_rmse={};channel_max={};matched_count={}
        if active_c==[] and active_r==[]:
            matched_count['brace']=0
            # Undefined error for no active supports, not an artificial zero.
            channel_rmse['referenceMm']=None
            channel_rmse['reactionKnPerPile']=None
            channel_max['referenceMm']=None
            channel_max['reactionKnPerPile']=None
        for label,ca,reference_idx,fields in curves:
            matched_count[label]=len(reference_idx)
            for field in fields:
                diffs=[ca[k][field]-reference_idx[k][field] for k in reference_idx]
                channel_rmse[field]=sqrt(sum(d*d for d in diffs)/len(diffs))
                channel_max[field]=max(abs(d) for d in diffs)
        results.append({'stageId':sid,'matchedStations':matched_count,
                        'channelRMSE':channel_rmse,'channelMaxAbsDifference':channel_max})
    if issues:
        return {'schemaVersion':'pitguard.luoyu-full-channels/1','comparisonStatus':'blocked',
                'rmse':None,'requiredEvidence':sorted(set(issues)),
                'originalFileBytesIndependentlyChecked':False,
                'formalSameConditionVerification':False}
    return {'schemaVersion':'pitguard.luoyu-full-channels/1',
            'comparisonStatus':'all_channels_compared_source_peer_review_pending',
            'stages':results,'originalFileSha256':original['fileSha256'],
            'originalFileBytesIndependentlyChecked':False,
            'formalSameConditionVerification':False,
            'reason':'Signed stages, both-side pressures and support events numerically compared; source bytes and physical method still require independent certification'}
