"""A6 bounded one-strut candidate policies, validation, and numerical ranking.

Only variables actually consumed by the frozen Section solver are considered.
Changes to stage water, geotechnical properties, acceptance limits or standard
factors are explicitly disallowed. Every change must be re-solved.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
from math import ceil, isclose, pi, sin, isfinite
from app.engineering.single_braced_pile.contracts import Section

ALLOWED_FIELDS={
    'strut.stiffness_kn_m', 'strut.depth_m', 'pile.inertia_factor', 'pile.length_m',
    'cage.bar_count', 'cage.bar_diameter_mm', 'cage.stirrup_spacing_mm',
    'pile.diameter_m'
}
REBAR_FIELDS={'cage.bar_count','cage.bar_diameter_mm','cage.stirrup_spacing_mm'}
SUPPORT_FIELDS={'strut.depth_m','strut.stiffness_kn_m'}
SECTION_FIELDS={'pile.diameter_m'}


def _is_changed(a,b):
    return type(a) is not type(b) or a != b


def changed_fields(original:dict, final:dict)->dict[str,dict]:
    """Return only whitelisted physical changes, excluding expected provenance downgrades."""
    changed={}
    for field in sorted(ALLOWED_FIELDS):
        a,b=field.split('.')
        old=original[a][b];new=final[a][b]
        if _is_changed(old,new):changed[field]={'before':old,'after':new}
    return changed


def _allowed_value(field, old, new):
    if isinstance(new,bool) or not isinstance(new,(int,float)) or not isfinite(new):
        raise ValueError('候选参数必须为有限数值')
    if field=='cage.bar_count' and (isinstance(new,float) and not new.is_integer()):
        raise ValueError('钢筋根数必须为整数')
    if field=='cage.bar_count' and not isinstance(new,int):
        raise ValueError('钢筋根数必须为整数')
    if field=='pile.inertia_factor' and new>old:
        raise ValueError('禁止通过提高未经核验的惯性矩折减系数自动证明结构刚度')
    if field=='pile.length_m' and new<old:
        raise ValueError('当前候选搜索不允许自动缩短嵌固深度')
    if field=='pile.diameter_m' and (new<=old or new>old+.15+1e-9):
        raise ValueError('自动试算桩径只允许单步增大且上限为 150 mm')


def _components(action:dict)->list[dict]:
    if not isinstance(action,dict):raise ValueError('Candidate action must be an object')
    method=action.get('method')
    if method in {'bounded_joint_trial','bounded_multi_trial'}:
        pieces=action.get('actions')
        expected=2 if method=='bounded_joint_trial' else None
        if (not isinstance(pieces,list) or len(pieces)<2 or len(pieces)>3 or
             (expected is not None and len(pieces)!=expected) or
             (method=='bounded_multi_trial' and len(pieces)!=3) or
             any(not isinstance(x,dict) or x.get('field') not in ALLOWED_FIELDS or
                 x.get('method')!='bounded_parameter_trial' for x in pieces)):
            raise ValueError('Only 2 or 3 typed, individually sourced physical changes allowed')
        fields=[x['field'] for x in pieces]
        if len(fields)!=len(set(fields)):raise ValueError('Repeated coupled engineering variable')
        if action.get('field')!='coupled:'+'+'.join(sorted(fields)):
            raise ValueError('Coupled action identity mismatch')
        fingerprint=sha256(json.dumps([(x['field'],x['before'],x['after']) for x in pieces],
                            separators=(',',':'),sort_keys=True).encode()).hexdigest()[:20]
        if action.get('after')!=fingerprint:
            raise ValueError('Coupled action fingerprint mismatch')
        if len(pieces)==3 and not set(fields)<=(REBAR_FIELDS|SUPPORT_FIELDS|SECTION_FIELDS):
            raise ValueError('Three-way trial only supports verified reinforcement/support fields')
        return pieces
    if action.get('field') not in ALLOWED_FIELDS or method not in (None,'bounded_parameter_trial'):
        raise ValueError('禁止Agent修改未授权的工程字段')
    return [action]


def apply_action(snapshot:dict,action:dict)->dict:
    """Apply a composite atomically, validate once, and keep evidence unapproved.

    An individual intermediate shape may be invalid even when the complete
    coupled patch is feasible. Validate original before values against the
    frozen parent, set every new value on a copy, then validate the final
    physical model and any supplied fabrication contract.
    """
    pieces=_components(action)
    for item in pieces:
        field=item['field'];parent,attr=field.split('.')
        if not isinstance(snapshot.get(parent),dict) or snapshot[parent].get(attr)!=item.get('before'):
            raise ValueError('候选参数与冻结原值不匹配')
        _allowed_value(field,item['before'],item.get('after'))
    changed=deepcopy(snapshot)
    fields={a['field'] for a in pieces}
    for item in pieces:
        field=item['field'];parent,attr=field.split('.')
        changed[parent][attr]=item['after']
    if 'strut.depth_m' in fields:
        installations=[st for st in changed['stages'] if st['event']=='install_strut']
        if len(installations)!=1 or not (0.20<=changed['strut']['depth_m']<changed['pile']['length_m']
            and changed['strut']['depth_m']<=installations[0]['excavation_depth_m']-.05):
            raise ValueError('支撑位置不满足安装开挖深度或最小 0.05m 余量')
    if 'pile.length_m' in fields and changed['pile']['length_m']>changed['layers'][-1]['bottom_m']:
        raise ValueError('Pile tip beyond available geological layers')
    if fields & (REBAR_FIELDS|SECTION_FIELDS):
        cage=changed['cage'];r=changed['pile']['diameter_m']*500-cage['bar_axis_cover_mm']
        if r<=0 or 2*r*sin(pi/cage['bar_count'])-cage['bar_diameter_mm']<=0:
            raise ValueError('Candidate circular bar ring has no positive clear spacing')
        if cage['bar_axis_cover_mm']-cage['bar_diameter_mm']/2-cage['stirrup_diameter_mm']<=0:
            raise ValueError('Candidate hoop cover is invalid')
    paths=set()
    for entry in changed.get('sources',[]):
        field=entry.get('path')
        if field in fields:
            paths.add(field)
            if entry.get('state')=='confirmed':entry['state']='candidate';entry['approved_by']=None
    for field in sorted(fields-paths):
        changed.setdefault('sources',[]).append({'path':field,'origin':'calculation',
          'reference':'Agent trial, independent site/fabrication evidence required',
          'state':'candidate','approved_by':None})
    model=Section.model_validate(changed)
    # An optional, explicitly supplied production plan imposes hard geometric
    # feasibility. Unknown production information stays unknown and blocking.
    if (changed.get('engineering_design') or {}).get('cage_fabrication') and fields & (REBAR_FIELDS|SECTION_FIELDS):
        from app.engineering.single_braced_pile.normative.cage_fabrication import cage_fabrication_review
        layout=cage_fabrication_review(model)
        if layout['status']=='fail':
            raise ValueError('Candidate fails declared cage fabrication constraints: '+
                             ','.join(layout.get('failedChecks',[])[:4]))
    return changed

def _action(snapshot,field,new,reason,kind)->dict|None:
    parent,key=field.split('.')
    old=snapshot[parent][key]
    if not _is_changed(old,new):return None
    from .variable_dependencies import dependency_report
    dependency=dependency_report([{'field':field}])
    return {'field':field,'before':old,'after':new,
            'dependencyReview':dependency,
            'reason':reason,'family':kind,'permission':'L1_candidate_only',
            'method':'bounded_parameter_trial','fullRecalculationRequired':True,
            'requiresSourceReconfirmation':True,'formalConstructionIssue':False}


def _rebar_pool(snap,result)->list:
    rb=result.get('reinforcement') or {}
    area_req=rb.get('minimumAreaMm2');area=rb.get('providedAreaMm2')
    if not isinstance(area_req,(int,float)) or not isinstance(area,(int,float)) or area>=area_req:
        return []
    cage=snap['cage'];a=cage['bar_diameter_mm'];n=cage['bar_count']
    target=max(n+2,ceil(area_req/(pi*a*a/4)))
    if target%2:target+=1
    pool=[]
    if target<=80:
        pool.append(_action(snap,'cage.bar_count',target,
                            '已配纵筋面积低于明确给定的最小配筋面积；试算增加根数','longitudinal_rebar'))
    for dia in (16,18,20,22,25,28,32):
        if dia<=a:continue
        if n*pi*dia*dia/4>=area_req:
            pool.append(_action(snap,'cage.bar_diameter_mm',dia,
                                '只针对最小纵筋面积及几何生成受限候选；轴弯剪/裂缝仍需另核','longitudinal_rebar'))
            break
    return pool



def _verified_capacity_rebar_pool(snap,result):
    """Only react to an *explicit calculated FAIL* in stage-matched N/M/V.

    Missing load combinations, absent member data or an unsupported method
    never become a fictitious shear/bending capacity or an auto-PASS.
    """
    matched=((result.get('normativeCoverage') or {}).get('requiredChecks') or {}).get(
        'concrete_circular_matched_nm_v') or {}
    if matched.get('status')!='fail':return []
    rows=((matched.get('result') or {}).get('cases') or [])
    failing_nm=any(row.get('status')=='fail' and isinstance(row.get('nmUtilization'),(int,float))
                   and row['nmUtilization']>1 for row in rows)
    failing_shear=any(row.get('shearStatus')=='fail' and isinstance(row.get('shearUtilization'),(int,float))
                   and row['shearUtilization']>1 for row in rows)
    cage=snap['cage'];pool=[]
    if failing_nm:
        if cage['bar_count']+2<=80:
            pool.append(_action(snap,'cage.bar_count',cage['bar_count']+2,
               '已匹配轴力、阶段和截面的圆桩 N-M 验算出现超限；增加纵筋根数后需再次完整复核',
               'verified_nm_trial'))
        for dia in (16,18,20,22,25,28,32):
            if dia>cage['bar_diameter_mm']:
                pool.append(_action(snap,'cage.bar_diameter_mm',dia,
                    'N-M 验算超限时试算提高纵筋直径；构造、剪力及裂缝仍需独立闭合',
                    'verified_nm_trial'))
                break
    if failing_shear:
        old=cage['stirrup_spacing_mm']
        for reduction in (0.8,0.65):
            value=max(50.,round(old*reduction/5)*5.)
            if value<old:
                pool.append(_action(snap,'cage.stirrup_spacing_mm',value,
                  '同步组合的圆桩斜截面验算明确超限；试算减小箍筋间距，需复核构造和节点',
                  'verified_shear_trial'))
    return pool


def _joint_action(first,second):
    from .variable_dependencies import dependency_report
    fields=sorted([first['field'],second['field']])
    fingerprint=sha256(json.dumps([(a['field'],a['before'],a['after']) for a in (first,second)],
                       separators=(',',':'),sort_keys=True).encode()).hexdigest()[:20]
    return {'field':'coupled:'+'+'.join(fields),'after':fingerprint,
        'actions':[first,second],'family':'coupled_checked_variables',
        'method':'bounded_joint_trial',
        'reason':'Two independently sourced engineering design variables; joint recomputation of all stages required',
        'dependencyReview':dependency_report([first,second]),
        'permission':'L1_candidate_only','fullRecalculationRequired':True,
        'requiresSourceReconfirmation':True,'formalConstructionIssue':False}


def _multi_action(*actions):
    from .variable_dependencies import dependency_report
    if len(actions)!=3 or len({a['field'] for a in actions})!=3:
        raise ValueError('Three separate physical variables required')
    fields=sorted(a['field'] for a in actions)
    fingerprint=sha256(json.dumps([(a['field'],a['before'],a['after']) for a in actions],
                      separators=(',',':'),sort_keys=True).encode()).hexdigest()[:20]
    return {'field':'coupled:'+'+'.join(fields),'after':fingerprint,
        'actions':list(actions),'method':'bounded_multi_trial',
        'family':'M7_rebar_and_M6_support_coupled',
        'reason':'Three bounded, sourced design variables jointly recomputed; cage manufacturing and joint evidence remain gated',
        'dependencyReview':dependency_report(list(actions)),
        'permission':'L1_candidate_only','fullRecalculationRequired':True,
        'requiresSourceReconfirmation':True,'formalConstructionIssue':False}


def _joint_pool(pool,result):
    """Pair only physically related changes supported by the same one-strut solver."""
    available={}
    for a in pool:
        if a and a['field'] not in available:available[a['field']]=a
    checks=(result.get('checks') or {})
    normalized=((result.get('normativeCoverage') or {}).get('requiredChecks') or {})
    matched=((normalized.get('concrete_circular_matched_nm_v') or {}).get('result') or {}).get('cases') or []
    nm_fail=any(x.get('status')=='fail' for x in matched)
    shear_fail=any(x.get('shearStatus')=='fail' for x in matched)
    pairs=[]
    if checks.get('displacement',{}).get('satisfied') is False:
        pairs.append(('strut.depth_m','strut.stiffness_kn_m'))
    if nm_fail and shear_fail:
        pairs.append(('cage.bar_diameter_mm','cage.stirrup_spacing_mm'))
    if (result.get('reinforcement') or {}).get('minimumAreaMet') is False:
        pairs.append(('cage.bar_count','cage.bar_diameter_mm'))
    if checks.get('passiveResultant',{}).get('satisfied') is False:
        pairs.append(('pile.length_m','strut.stiffness_kn_m'))
    selected=[_joint_action(available[a],available[b]) for a,b in pairs if a in available and b in available]
    displacement_fail=checks.get('displacement',{}).get('satisfied') is False
    min_area_fail=(result.get('reinforcement') or {}).get('minimumAreaMet') is False
    triples=[]
    if displacement_fail and min_area_fail:
        triples.append(('cage.bar_count','cage.bar_diameter_mm','strut.stiffness_kn_m'))
    if displacement_fail and nm_fail and shear_fail:
        triples.append(('cage.bar_diameter_mm','cage.stirrup_spacing_mm','strut.stiffness_kn_m'))
    if min_area_fail and shear_fail:
        triples.append(('cage.bar_count','cage.bar_diameter_mm','cage.stirrup_spacing_mm'))
    if nm_fail and shear_fail:
        triples.append(('pile.diameter_m','cage.bar_diameter_mm','cage.stirrup_spacing_mm'))
    if displacement_fail and min_area_fail:
        triples.append(('pile.diameter_m','cage.bar_count','strut.stiffness_kn_m'))
    selected += [_multi_action(*(available[x] for x in names))
                 for names in triples if all(x in available for x in names)]
    return selected


def proposals(result:dict,max_candidates:int,*,visited:set|None=None,focus:str='auto')->list[dict]:
    """Use actual failure signals, not missing normative methods, to trigger edits."""
    if focus not in {'auto','support','reinforcement','embedment','diagnostics','coupled','section'}:
        raise ValueError('Unknown optimization focus')
    if focus=='diagnostics':return []
    snap=result['snapshot'];visited=visited or set()
    issues={x.get('code') for x in result.get('issues',[])}
    disp=(result.get('checks') or {}).get('displacement') or {}
    displacement_violation=disp.get('satisfied') is False
    pool=_rebar_pool(snap,result)+_verified_capacity_rebar_pool(snap,result)
    # The section diameter is an actual solver-consumed geometry. Trial only
    # for diagnosed design failures and within the non-overlapping pile spacing.
    # Changes require new plans, drilling clearance, cost and cage geometry checks.
    matched=((result.get('normativeCoverage') or {}).get('requiredChecks') or {}).get(
        'concrete_circular_matched_nm_v') or {}
    nmv_fail=matched.get('status')=='fail'
    if displacement_violation or nmv_fail:
        pile=snap['pile']
        for step in (.05,.10):
            dia=round(pile['diameter_m']+step,4)
            if dia<=min(pile['spacing_m'],2.5):
                pool.append(_action(snap,'pile.diameter_m',dia,
                   '控制阶段位移或 N-M-V 验算明确失败；增大圆桩直径后对所有阶段及钢筋笼重新核验',
                   'pile_section_geometry'))
    if 'PASSIVE_RESULTANT_LIMIT' in issues:
        depth=snap['layers'][-1]['bottom_m'];old=snap['pile']['length_m']
        for delta in (0.5,1.0,1.5):
            if old+delta<=min(depth,50.):
                pool.append(_action(snap,'pile.length_m',round(old+delta,3),
                    '桩趾被动土反力合力不满足，增加嵌固深度并对各阶段重算','embedment'))
    # A legacy single-round study still explores stiffness sensitivity, but
    # additional rounds are governed by actual unmet targets in the orchestrator.
    if result.get('analysisStatus')=='complete':
        for factor in (1.20,1.50,1.80):
            pool.append(_action(snap,'strut.stiffness_kn_m',round(snap['strut']['stiffness_kn_m']*factor,6),
                '支撑刚度有界敏感性；实际EA/L、对侧墙和节点承载力需另行确认','support_stiffness'))
        install=next((s for s in snap['stages'] if s['event']=='install_strut'),None)
        if install:
            for delta in (-0.15,0.15):
                new=round(snap['strut']['depth_m']+delta,3)
                if 0.20<=new<=install['excavation_depth_m']-0.05:
                    pool.append(_action(snap,'strut.depth_m',new,
                       '一道支撑标高局部调整试算；不得改变施工开挖顺序','support_elevation'))
    if 'PASSIVE_RESULTANT_LIMIT' in issues and not pool:
        pool.append(_action(snap,'strut.stiffness_kn_m',round(snap['strut']['stiffness_kn_m']*1.25,6),
            '仅作支撑约束敏感性，不能替代嵌固验算','support_stiffness'))
    # Composite actions are allowed only for explicitly observed failures;
    # their budget cost equals one complete frozen re-solve.
    coupled=_joint_pool(pool,result) if focus in {'auto','coupled'} else []
    if focus=='coupled':pool=coupled
    elif focus=='auto' and max_candidates>=4:pool=coupled+pool
    good=[];seen=set(tuple(x) for x in visited)
    for action in pool:
        if not action:continue
        physical=[a['field'] for a in action['actions']] if action.get('method') in ('bounded_joint_trial','bounded_multi_trial') else [action['field']]
        if focus=='support' and not all(f in SUPPORT_FIELDS for f in physical):continue
        if focus=='reinforcement' and not all(f in REBAR_FIELDS for f in physical):continue
        if focus=='embedment' and physical!=['pile.length_m']:continue
        if focus=='section' and not all(f in SECTION_FIELDS for f in physical):continue
        signature=(action['field'],action['after'])
        if signature in seen:continue
        try:apply_action(snap,action)
        except (ValueError,KeyError,TypeError):continue
        good.append(action);seen.add(signature)
        if len(good)>=max_candidates:break
    return good


def numerical_score(result:dict,baseline:dict)->dict:
    disp=(result.get('checks') or {}).get('displacement') or {}
    mm=disp.get('valueMm');limit=disp.get('limitMm')
    if not isinstance(mm,(int,float)) or not isinstance(limit,(int,float)) or limit<=0:
        return {'score':1e9,'status':'incomplete','numericViolation':None}
    violation=max(0.,(mm-limit)/limit)
    rb=result.get('reinforcement') or {}
    minimum=rb.get('minimumAreaMm2');provided=rb.get('providedAreaMm2')
    rebar_gap=(max(0.,(minimum-provided)/minimum) if
               isinstance(minimum,(int,float)) and minimum>0 and isinstance(provided,(int,float)) else 0.)
    norm=(result.get('normativeCoverage') or {}).get('requiredChecks') or {}
    hardfail=sum(1 for val in norm.values() if val.get('status')=='fail')
    source=baseline['snapshot'];changed=changed_fields(source,result['snapshot'])
    magnitude=0.
    for field,entry in changed.items():
        if field.startswith('cage.'):
            magnitude+=0.01
        else:
            before=entry['before']
            magnitude+=0.025*abs(entry['after']-before)/max(abs(before),1e-6)
    score=1000*hardfail+100*violation+100*rebar_gap+mm/limit+0.1*magnitude
    if result.get('analysisStatus')!='complete':score+=1e8
    return {'score':round(score,9),'status':'numerical_comparison_only',
            'numericViolation':round(violation,9),'minimumRebarGapRatio':round(rebar_gap,9),
            'explicitNormativeFailureCount':hardfail,'adjustmentMagnitudeProxy':round(magnitude,6),
            'formalConstructionIssue':False}
