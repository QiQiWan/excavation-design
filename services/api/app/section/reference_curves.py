"""Strict neutral numerical exchange; this is not an FRWS native-file reader.

Depth resampling is comparison only. Rounded report extrema are never promoted to
nodal arrays. One-sided samples and Gauss/nodal-lumping projections retain their
physical identities and their coverage limitations.
"""
from __future__ import annotations

from bisect import bisect_left
from copy import deepcopy
from math import isfinite, sqrt

from .models import content_hash, canonical_json
from .reference_contract import (SHA256, _shape, _text, assess_reference_contract,
                                 validate_reference_contract)

SCHEMA = 'pitguard.reference-curves/1'
QUANTITIES = {'u', 'M', 'V', 'N', 'pressure_outside', 'pressure_inside'}
REQUIRED_QUANTITIES = ('u', 'M', 'V', 'pressure_outside', 'pressure_inside')
SIGNS = {'u': 'toward_excavation', 'M': 'solver_positive_curvature',
         'V': 'solver_positive_section_shear', 'pressure': 'compressive_positive',
         'reaction': 'opposes_positive_displacement'}
CSV_HEADERS = ('stage_id,source_stage_id,quantity,unit,basis,depth_m,side,value,'
               'pressure_stress,pressure_composition,pressure_role,projection')
UNITS = {'u': {'mm', 'm'}, 'M': {'kN*m', 'kN*m/m'}, 'V': {'kN', 'kN/m'},
         'N': {'kN', 'kN/m'}, 'pressure_outside': {'kPa'}, 'pressure_inside': {'kPa'}}


def _number(value, label):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f'{label}: finite number required')
    return value


def _strings(values, label, maximum=100):
    if not isinstance(values, list) or not values or len(values) > maximum:
        raise ValueError(f'{label}: nonempty bounded list required')
    for value in values:
        _text(value, label, 200)
    if len(values) != len(set(values)):
        raise ValueError(f'{label}: duplicate identity')


def _validate(bundle, sealed):
    keys = {'schemaVersion', 'caseId', 'contractHash', 'source', 'datum', 'forceBasis', 'signs',
            'effectBasis', 'requiredStageIds', 'stages', 'reinforcement', 'tolerances'}
    _shape(bundle, keys | ({'bundleHash'} if sealed else set()), 'curve bundle')
    if bundle['schemaVersion'] != SCHEMA:
        raise ValueError('Unsupported curve schema')
    if len(canonical_json(bundle)) > 20_000_000:
        raise ValueError('Curve bundle exceeds size limit')
    if sealed and bundle['bundleHash'] != content_hash({k:v for k,v in bundle.items() if k != 'bundleHash'}):
        raise ValueError('Curve bundle hash mismatch')
    _text(bundle['caseId'], 'caseId', 100)
    if bundle['contractHash'] is not None and not SHA256.fullmatch(str(bundle['contractHash'])):
        raise ValueError('Invalid contract hash')
    source = bundle['source']
    _shape(source, {'kind','sha256','software','version'}, 'curve source')
    if source['kind'] not in {'native_numeric_export','numeric_table_transcription','digitized_curve',
                              'synthetic_protocol_fixture','pitguard_result'}:
        raise ValueError('Unknown curve extraction mode')
    if not SHA256.fullmatch(str(source['sha256'])):
        raise ValueError('Invalid source hash')
    for key in ('software', 'version'):
        if source[key] is not None:
            _text(source[key], key, 200)
    datum = bundle['datum']
    _shape(datum, {'origin','positive','offsetToWallTopM','evidence'}, 'datum')
    if datum['origin'] not in {'wall_top','model_ground','project_elevation'} or datum['positive'] not in {'down','up'}:
        raise ValueError('Unsupported depth convention')
    _number(datum['offsetToWallTopM'], 'offsetToWallTopM')
    _text(datum['evidence'], 'datum evidence')
    if datum['origin']=='wall_top' and (datum['positive']!='down' or datum['offsetToWallTopM']!=0):
        raise ValueError('Wall-top canonical depth must be down with zero offset')
    force = bundle['forceBasis']
    _shape(force, {'kind','pileSpacingM','conversion'}, 'force basis')
    if force['kind'] not in {'single_pile','per_metre','unconfirmed'}:
        raise ValueError('Unsupported force basis')
    if force['pileSpacingM'] is not None and _number(force['pileSpacingM'],'pileSpacingM') <= 0:
        raise ValueError('Pile spacing must be positive')
    expected = {'single_pile':'identity','per_metre':'line_to_pile_multiply_spacing','unconfirmed':'unconfirmed'}
    if force['conversion'] != expected[force['kind']]:
        raise ValueError('Explicit force conversion does not match basis')
    if force['kind']=='per_metre' and force['pileSpacingM'] is None:
        raise ValueError('Line-to-pile conversion requires explicit spacing')
    _shape(bundle['signs'], set(SIGNS), 'sign conventions')
    for sign in bundle['signs'].values():
        _text(sign, 'sign convention', 200)
    if bundle['effectBasis'] not in {'characteristic','design','unconfirmed'}:
        raise ValueError('Unknown effect basis')
    _strings(bundle['requiredStageIds'], 'required stages', 50)
    tolerances = bundle['tolerances']
    _shape(tolerances, {'absolute','relative','depthM'}, 'tolerances')
    _shape(tolerances['absolute'], QUANTITIES | {'support','rebar'}, 'absolute tolerances')
    for key, value in tolerances['absolute'].items():
        if _number(value, key) < 0:
            raise ValueError('Tolerances cannot be negative')
    if not 0 <= _number(tolerances['relative'], 'relative tolerance') <= 1:
        raise ValueError('Relative tolerance must be in [0,1]')
    if not 0 < _number(tolerances['depthM'], 'depth tolerance') <= .001:
        raise ValueError('Depth tolerance must be in (0,.001] metres')
    stages = bundle['stages']
    if not isinstance(stages, list) or len(stages)>50:
        raise ValueError('Invalid stage array')
    stage_ids=set()
    sample_count=0
    for stage in stages:
        _shape(stage, {'id','sourceStageId','event','domainM','boundariesM','curves','supports','design'}, 'stage')
        if stage['id'] in stage_ids or stage['id'] not in bundle['requiredStageIds']:
            raise ValueError('Duplicate or unknown stage')
        stage_ids.add(stage['id'])
        _text(stage['sourceStageId'],'source stage identity',200)
        if stage['event'] not in {'initial','excavate','install','remove','water_or_load','unconfirmed'}:
            raise ValueError('Unknown stage event')
        domain=stage['domainM']
        if not isinstance(domain,list) or len(domain)!=2 or _number(domain[0],'domain')>=_number(domain[1],'domain'):
            raise ValueError('Invalid stage domain')
        boundaries=stage['boundariesM']
        if not isinstance(boundaries,list) or len(boundaries)>1000:
            raise ValueError('Invalid boundaries')
        for z in boundaries:
            if not domain[0] < _number(z,'boundary') < domain[1]:
                raise ValueError('Boundary outside stage domain')
        if boundaries!=sorted(set(boundaries)):
            raise ValueError('Boundary identities must be unique and ordered')
        channels=set()
        if not isinstance(stage['curves'],list) or len(stage['curves'])>6:
            raise ValueError('Invalid curves')
        for curve in stage['curves']:
            _shape(curve, {'quantity','unit','basis','pressureIdentity','projection','samples'}, 'curve')
            quantity=curve['quantity']
            if quantity not in QUANTITIES or quantity in channels:
                raise ValueError('Unknown/duplicate curve quantity')
            channels.add(quantity)
            if curve['unit'] not in UNITS[quantity]:
                raise ValueError('Unknown physical unit')
            if quantity=='u':
                if curve['basis']!='geometric' or curve['pressureIdentity'] is not None:
                    raise ValueError('Displacement has geometric basis')
            elif quantity.startswith('pressure_'):
                if curve['basis']!='stress':
                    raise ValueError('Pressure has stress basis')
                identity=curve['pressureIdentity']
                _shape(identity, {'stress','composition','role'}, 'pressure identity')
                if identity['stress'] not in {'effective','total','mixed','unconfirmed'} or identity['composition'] not in {
                    'soil_only','water_only','soil_plus_water','layerwise','unconfirmed'} or identity['role'] not in {
                    'mobilized','imposed','net','unconfirmed'}:
                    raise ValueError('Unknown pressure identity')
            else:
                if curve['pressureIdentity'] is not None or curve['basis']!=force['kind']:
                    raise ValueError('Force channel basis must match declared force conversion')
                if force['kind']=='per_metre' and curve['unit'] not in {'kN/m','kN*m/m'}:
                    raise ValueError('Per-metre force requires per-metre unit')
                if force['kind']=='single_pile' and curve['unit'] not in {'kN','kN*m'}:
                    raise ValueError('Single-pile force requires single-pile unit')
            if curve['projection'] not in {'element_end_limits','nodal','native_numeric',
                                           'gauss_weighted_nodal_lumping','digitized'}:
                raise ValueError('Unknown curve projection')
            samples=curve['samples']
            if not isinstance(samples,list) or len(samples)>10000:
                raise ValueError('Invalid samples')
            # Empty arrays are allowed only as explicit templates/missing coverage.
            sample_count += len(samples)
            points=set(); by_depth={}
            for point in samples:
                _shape(point, {'z','value','side'}, 'sample')
                z=_number(point['z'],'sample depth'); _number(point['value'],'sample value')
                if not domain[0]<=z<=domain[1]:
                    raise ValueError('Sample outside declared domain')
                if point['side'] not in {'above','below','continuous'}:
                    raise ValueError('Unknown sample side')
                key=(z,point['side'])
                if key in points:
                    raise ValueError('Duplicate sample (depth,side)')
                points.add(key); by_depth.setdefault(z,set()).add(point['side'])
            if any('continuous' in sides and len(sides)>1 for sides in by_depth.values()):
                raise ValueError('Continuous and one-sided sample identity conflicts')
        support_ids=set()
        if not isinstance(stage['supports'],list) or len(stage['supports'])>50:
            raise ValueError('Invalid support results')
        for support in stage['supports']:
            _shape(support, {'id','depthM','status','referenceDisplacementMm','displacementMm',
                             'reaction','memberAxialForceKn'}, 'support')
            _text(support['id'],'support id',100)
            if support['id'] in support_ids:
                raise ValueError('Duplicate support id')
            support_ids.add(support['id']); _number(support['depthM'],'support depth')
            if support['status'] not in {'active','not_installed','removed','unconfirmed'}:
                raise ValueError('Unknown support lifecycle state')
            for key in ('referenceDisplacementMm','displacementMm','memberAxialForceKn'):
                if support[key] is not None:
                    _number(support[key],key)
            if support['reaction'] is not None:
                _shape(support['reaction'], {'value','unit','basis'}, 'support reaction')
                _number(support['reaction']['value'],'reaction')
                if support['reaction']['basis']!=force['kind'] or support['reaction']['unit'] != (
                    'kN/m' if force['kind']=='per_metre' else 'kN'):
                    raise ValueError('Support reaction basis/unit mismatch')
            if support['status']=='not_installed' and any(support[k] is not None for k in (
                'referenceDisplacementMm','displacementMm','reaction','memberAxialForceKn')):
                raise ValueError('Not-installed support must retain unavailable response as null')
            if support['status']=='removed':
                if support['displacementMm'] is not None or support['memberAxialForceKn'] is not None:
                    raise ValueError('Removed support has no current member displacement/axial force')
                if support['reaction'] is not None and support['reaction']['value'] != 0:
                    raise ValueError('Removed support cannot retain a nonzero current reaction')
        if stage['design'] is not None:
            _shape(stage['design'], {'effectFactor','momentMaxDesignKnM','shearMaxDesignKn','combination'}, 'design effects')
            for key in ('effectFactor','momentMaxDesignKnM','shearMaxDesignKn'):
                if _number(stage['design'][key],key)<0:
                    raise ValueError('Design effect values must be nonnegative')
            _text(stage['design']['combination'],'combination')
    if sample_count>150000:
        raise ValueError('Curve sample total exceeds limit')
    if not isinstance(bundle['reinforcement'],list) or len(bundle['reinforcement'])>100:
        raise ValueError('Invalid reinforcement rows')
    for row in bundle['reinforcement']:
        _shape(row, {'id','fromDepthM','toDepthM','requiredAreaMm2','providedAreaMm2',
                     'governingStageIds','method'}, 'reinforcement')
        _text(row['id'],'reinforcement id',100); _text(row['method'],'reinforcement method')
        if _number(row['fromDepthM'],'rebar start')>=_number(row['toDepthM'],'rebar end'):
            raise ValueError('Invalid reinforcement domain')
        for key in ('requiredAreaMm2','providedAreaMm2'):
            if row[key] is not None and _number(row[key],key)<0:
                raise ValueError('Reinforcement area must be nonnegative')
        _strings(row['governingStageIds'],'governing stages',50)
        if not set(row['governingStageIds'])<=set(bundle['requiredStageIds']):
            raise ValueError('Unknown reinforcement governing stage')


def _checked_validate(value, sealed):
    try:
        _validate(value,sealed)
    except (TypeError,KeyError,IndexError,OverflowError) as exc:
        raise ValueError('Malformed curve bundle structure') from exc


def freeze_curve_bundle(value: dict) -> dict:
    data=deepcopy(value)
    if isinstance(data,dict) and 'bundleHash' in data:
        validate_curve_bundle(data)
        return data
    _checked_validate(data,False)
    data['bundleHash']=content_hash(data)
    return data


def validate_curve_bundle(bundle: dict) -> None:
    _checked_validate(bundle,True)


def _normalize_bundle(bundle: dict) -> dict:
    """Explicit declared conversions only; never choose a sign to improve a fit."""
    validate_curve_bundle(bundle)
    out=deepcopy(bundle)
    datum=bundle['datum']; multiplier=1 if datum['positive']=='down' else -1
    depth=lambda z: multiplier*z + datum['offsetToWallTopM']
    factor=bundle['forceBasis']['pileSpacingM'] if bundle['forceBasis']['kind']=='per_metre' else 1
    for stage in out['stages']:
        stage['domainM']=sorted(depth(z) for z in stage['domainM'])
        stage['boundariesM']=sorted(depth(z) for z in stage['boundariesM'])
        for curve in stage['curves']:
            scale=1000 if curve['quantity']=='u' and curve['unit']=='m' else (
                factor if curve['quantity'] in {'M','V','N'} else 1)
            for sample in curve['samples']:
                sample['z']=depth(sample['z']); sample['value']*=scale
                # above/below are physical wall sides, independent of numerical axis direction.
            if curve['quantity']=='u': curve['unit']='mm'
            if curve['quantity'] in {'M','V','N'} and bundle['forceBasis']['kind']!='unconfirmed':
                curve['basis']='single_pile'; curve['unit']='kN*m' if curve['quantity']=='M' else 'kN'
        for support in stage['supports']:
            support['depthM']=depth(support['depthM'])
            if support['reaction'] is not None and bundle['forceBasis']['kind']!='unconfirmed':
                support['reaction']['value']*=factor
                support['reaction'].update(unit='kN',basis='single_pile')
        # Design/rebar fields have explicit canonical units in their names; never multiply them twice.
    for row in out['reinforcement']:
        row['fromDepthM'],row['toDepthM']=sorted((depth(row['fromDepthM']),depth(row['toDepthM'])))
    return out


def _indexed(curve):
    points={}
    for p in curve['samples']:
        points.setdefault(p['z'],{})[p['side']]=p['value']
    return points,sorted(points)


def _sample(indexed, z, side, boundaries, tolerance):
    points,depths=indexed
    i=bisect_left(depths,z)
    near=next((d for d in depths[max(0,i-1):i+1] if abs(d-z)<=tolerance),None)
    if near is not None:
        values=points[near]
        return values.get(side,values.get('continuous'))
    if i==0 or i==len(depths):
        return None
    lo,hi=depths[i-1],depths[i]
    if any(lo < boundary < hi for boundary in boundaries):
        return None
    left=points[lo].get('below',points[lo].get('continuous'))
    right=points[hi].get('above',points[hi].get('continuous'))
    if left is None or right is None:
        return None
    return left+(right-left)*(z-lo)/(hi-lo)


def _compare_channel(candidate, reference, domain, boundaries, atol, rtol, ztol):
    a=_indexed(candidate); b=_indexed(reference)
    points=sorted(set((p['z'],p['side']) for c in (candidate,reference) for p in c['samples']))
    errors=[]; missing=[]
    for z,side in points:
        if not domain[0]-ztol<=z<=domain[1]+ztol:
            missing.append({'z':z,'side':side,'reason':'outside_common_domain'}); continue
        av=_sample(a,z,side,boundaries,ztol); bv=_sample(b,z,side,boundaries,ztol)
        if av is None or bv is None:
            missing.append({'z':z,'side':side,'reason':'missing_side_or_coverage'}); continue
        error=av-bv
        errors.append({'z':z,'side':side,'error':error,'candidate':av,'reference':bv,
                       'within':abs(error)<=atol+rtol*abs(bv)})
    missing_boundaries=[z for z in boundaries if any(
        not any(abs(d-z)<=ztol for d in index[1]) for index in (a,b))]
    if a[1] and b[1]:
        lo=max(domain[0],min(a[1]),min(b[1])); hi=min(domain[1],max(a[1]),max(b[1]))
        coverage=max(0.,hi-lo)/(domain[1]-domain[0])
    else: coverage=0.
    missing_length=(domain[1]-domain[0])*(1-coverage)
    incomplete=bool(missing or missing_boundaries or missing_length>ztol or not errors)
    peak=max(errors,key=lambda e:abs(e['error'])) if errors else None
    return {'quantity':candidate['quantity'], 'status':'incomplete' if incomplete else (
                'within_tolerance' if all(e['within'] for e in errors) else 'outside_tolerance'),
            'sampleCount':len(errors), 'coverageFraction':coverage,'missingCoverageLengthM':missing_length,
            'maxAbsoluteError':abs(peak['error']) if peak else None,
            'maxErrorAt':{'z':peak['z'],'side':peak['side']} if peak else None,
            'signedBias':sum(e['error'] for e in errors)/len(errors) if errors else None,
            'rmse':sqrt(sum(e['error']**2 for e in errors)/len(errors)) if errors else None,
            'missingSamples':missing,'missingBoundaryDepthsM':missing_boundaries,
            'candidateExtrema':_extrema(candidate), 'referenceExtrema':_extrema(reference)}


def _extrema(curve):
    if not curve['samples']: return None
    lo=min(p['value'] for p in curve['samples']); hi=max(p['value'] for p in curve['samples'])
    return {'minimum':lo,'maximum':hi,
            'minimumLocations':[{'z':p['z'],'side':p['side']} for p in curve['samples'] if p['value']==lo],
            'maximumLocations':[{'z':p['z'],'side':p['side']} for p in curve['samples'] if p['value']==hi]}


def _default_tolerances():
    return {'absolute':{'u':.05,'M':.1,'V':.1,'N':.1,'pressure_outside':.1,'pressure_inside':.1,
                        'support':.1,'rebar':1.}, 'relative':.01,'depthM':1e-8}


def section_curve_bundle(snapshot, result: dict) -> dict:
    from .service import validate_result
    validate_result(snapshot,result)
    data=snapshot.payload(); spacing=data['pile']['spacing_m']; stages=[]
    input_stages={s['id']:s for s in data['stages']}
    for output in result['stages']:
        stage=input_stages[output['id']]
        curves=[]
        for quantity,key,unit in [('u','displacementMm','mm'),('M','momentCharacteristicKnM','kN*m'),
                                  ('V','shearCharacteristicKn','kN')]:
            curves.append({'quantity':quantity,'unit':unit,'basis':'geometric' if quantity=='u' else 'single_pile',
                           'pressureIdentity':None,'projection':'element_end_limits',
                           'samples':[{'z':p['depthM'],'value':p[key],'side':'continuous' if p['side'] in {'within','interior'} else p['side']} for p in output['points']]})
        for side in ('outside','inside'):
            grouped={}
            for p in output.get('soilStates') or []:
                if p['side']!=side: continue
                group=grouped.setdefault(p['depthM'],{'pressure':0.,'weight':0.,'water':0.})
                water=p.get('externalWaterKpa')
                if water is None:
                    if p.get('pressureBasis','effective')=='total':
                        water=0.
                    else:
                        level=stage['water_outside_depth_m'] if side=='outside' else stage['water_inside_depth_m']
                        water=data['analysis']['water_unit_weight_kn_m3']*max(0,p['depthM']-level)
                group['pressure']+=p['pressureKn']; group['weight']+=p['weightM']
                group['water']+=water*p['weightM']
            curves.append({'quantity':'pressure_'+side,'unit':'kPa','basis':'stress',
                           'pressureIdentity':{'stress':'total','composition':'soil_plus_water','role':'mobilized'},
                           'projection':'gauss_weighted_nodal_lumping',
                           'samples':[{'z':z,'side':'continuous',
                                       'value':g['pressure']/(spacing*g['weight'])+g['water']/g['weight']}
                                      for z,g in sorted(grouped.items())]})
        supports=[]
        for s in output.get('supports') or []:
            active=s.get('active',False)
            supports.append({'id':s['id'],'depthM':s['depthM'],'status':'active' if active else ('unconfirmed' if s['status']=='not_calculated' else s['status']),
                             'referenceDisplacementMm':s['referenceDisplacementMm'],
                             'displacementMm':s['displacementMm'] if active else None,
                             'reaction':{'value':s['reactionCharacteristicKn'],'unit':'kN','basis':'single_pile'}
                                 if s['reactionCharacteristicKn'] is not None else None,
                             'memberAxialForceKn':None})
        length=data['pile']['length_m']
        boundaries=sorted({z for layer in data['soil_layers'] for z in (layer['top_m'],layer['bottom_m'])
                           if 0<z<length} | {s['depth_m'] for s in data['supports'] if 0<s['depth_m']<length}
                          | {z for z in (stage['excavation_depth_m'],stage['water_inside_depth_m'],
                                          stage['water_outside_depth_m']) if 0<z<length})
        factor=data['analysis']['design_effect_factor']
        design=None
        if output['status']=='success':
            design={'effectFactor':factor,'momentMaxDesignKnM':output['maxAbsMomentKnM']*factor,
                    'shearMaxDesignKn':output['maxAbsShearKn']*factor,
                    'combination':data['analysis']['design_effect_factor_basis']}
        stages.append({'id':output['id'],'sourceStageId':output['id'],'event':stage['event'],
                       'domainM':[0.,length],'boundariesM':boundaries,'curves':curves,'supports':supports,'design':design})
    rows=[]
    for row in result['reinforcement'].get('rows',[]):
        characteristic=row['momentDesignKnM']/data['analysis']['design_effect_factor']
        tied=[s['id'] for s in result['stages'] if s['status']=='success' and
              abs(s['maxAbsMomentKnM']-characteristic)<=1e-8]
        rows.append({'id':row['id'],'fromDepthM':row['fromDepthM'],'toDepthM':row['toDepthM'],
                     'requiredAreaMm2':row.get('requiredAreaMm2'),'providedAreaMm2':row.get('providedAreaMm2'),
                     'governingStageIds':tied or [row['governingStageId']], 'method':result['reinforcement']['method']})
    return freeze_curve_bundle({'schemaVersion':SCHEMA,'caseId':data.get('source_project_id') or data['section_id'],
        'contractHash':data.get('reference_contract_hash'),
        'source':{'kind':'synthetic_protocol_fixture' if data['purpose']=='verification_fixture' else 'pitguard_result',
                  'sha256':result['resultHash'],'software':'PitGuard','version':result['algorithmVersion']},
        'datum':{'origin':'wall_top','positive':'down','offsetToWallTopM':0.,
                 'evidence':'Frozen solver nodes and explicit input datum; pressure retains Gauss/nodal-lumping identity'},
        'forceBasis':{'kind':'single_pile','pileSpacingM':spacing,'conversion':'identity'},'signs':dict(SIGNS),
        'effectBasis':'characteristic','requiredStageIds':[s['id'] for s in stages], 'stages':stages,
        'reinforcement':rows,'tolerances':_default_tolerances()})


def _at_path(value,path):
    for part in path.split('.'):
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value


def _value_comparison(a,b,atol,rtol):
    if a is None or b is None:
        return {'status':'incomplete','candidate':a,'reference':b,'absoluteError':None}
    error=abs(a-b)
    return {'status':'within_tolerance' if error<=atol+rtol*abs(b) else 'outside_tolerance',
            'candidate':a,'reference':b,'absoluteError':error}


def _governing(bundle,quantity,tolerance):
    extrema=[]
    for stage in bundle['stages']:
        curve=next((c for c in stage['curves'] if c['quantity']==quantity),None)
        if curve and curve['samples']:
            extrema.append((stage['id'],min(p['value'] for p in curve['samples']),
                            max(p['value'] for p in curve['samples'])))
    if not extrema: return None
    low=min(x[1] for x in extrema); high=max(x[2] for x in extrema)
    return {'minimum':low,'maximum':high,
            'minimumStageIds':[s for s,lo,hi in extrema if abs(lo-low)<=tolerance],
            'maximumStageIds':[s for s,lo,hi in extrema if abs(hi-high)<=tolerance]}


def compare_reference_curves(snapshot, result: dict, contract: dict, reference: dict) -> dict:
    validate_reference_contract(contract); validate_curve_bundle(reference)
    candidate_raw=section_curve_bundle(snapshot,result)
    candidate=_normalize_bundle(candidate_raw); ref=_normalize_bundle(reference)
    data=snapshot.payload(); assessment=assess_reference_contract(contract); blockers=[]
    if not data.get('reference_contract_hash') or not (
        data.get('reference_contract_hash')==reference['contractHash']==contract['contractHash']):
        blockers.append('reference_contract_hash_mismatch')
    if reference['caseId']!=contract['caseId']:
        blockers.append('reference_case_mismatch')
    if reference['source']['kind'] in {'synthetic_protocol_fixture','digitized_curve','pitguard_result'}:
        blockers.append('synthetic_or_digitized_reference')
    if data['purpose']=='verification_fixture': blockers.append('candidate_is_synthetic')
    if reference['source']['software'] is None or reference['source']['version'] is None:
        blockers.append('original_software_identity_missing')
    if assessment['status']!='ready': blockers.append('original_conditions_incomplete')
    if reference['forceBasis']['kind']=='unconfirmed': blockers.append('force_basis_unconfirmed')
    if reference['forceBasis']['pileSpacingM'] is None or abs(
        reference['forceBasis']['pileSpacingM']-data['pile']['spacing_m'])>1e-9:
        blockers.append('pile_spacing_mismatch')
    if reference['effectBasis']!='characteristic': blockers.append('effect_basis_mismatch')
    for sign in SIGNS:
        if reference['signs'][sign]!=candidate['signs'][sign]: blockers.append('sign_convention_mismatch:'+sign)
    fields={p['id']:p for p in contract['parameters']}; condition_matches=[]
    for key in contract['criticalConditionIds']:
        parameter=fields[key]; path=parameter['inputPath']; reason=None
        if parameter['status']=='unconfirmed': reason='original_condition_not_provided'
        elif path is None: reason='solver_binding_not_provided'
        else:
            try:
                if _at_path(data,path)!=parameter['value']: reason='solver_condition_differs'
            except (KeyError,IndexError,ValueError,TypeError): reason='solver_binding_unavailable'
        condition_matches.append({'id':key,'inputPath':path,'status':'matched' if reason is None else reason})
    if any(p['status']!='matched' for p in condition_matches): blockers.append('material_conditions_not_matched')
    expected=contract['stageIds']
    if reference['requiredStageIds']!=expected: blockers.append('reference_stage_manifest_mismatch')
    by_candidate={s['id']:s for s in candidate['stages']}; by_ref={s['id']:s for s in ref['stages']}
    missing_stages=[s for s in expected if s not in by_candidate or s not in by_ref]
    if missing_stages: blockers.append('missing_stages')
    curves=[]; support_results=[]; design_results=[]; missing_channels=[]
    tolerances=reference['tolerances']; absolute=tolerances['absolute']; rtol=tolerances['relative']; ztol=tolerances['depthM']
    for stage_id in expected:
        if stage_id not in by_candidate or stage_id not in by_ref: continue
        a,b=by_candidate[stage_id],by_ref[stage_id]
        if a['event']!=b['event']: blockers.append('stage_event_mismatch:'+stage_id)
        if any(abs(x-y)>ztol for x,y in zip(a['domainM'],b['domainM'])):
            blockers.append('stage_domain_mismatch:'+stage_id)
        boundaries=sorted(set(a['boundariesM']+b['boundariesM']))
        ac={c['quantity']:c for c in a['curves']}; bc={c['quantity']:c for c in b['curves']}
        for quantity in REQUIRED_QUANTITIES:
            if quantity not in ac or quantity not in bc:
                missing_channels.append({'stageId':stage_id,'quantity':quantity}); continue
            ca,cb=ac[quantity],bc[quantity]
            if ca['basis']!=cb['basis'] or ca['unit']!=cb['unit'] or ca['pressureIdentity']!=cb['pressureIdentity']:
                curves.append({'stageId':stage_id,'quantity':quantity,'status':'identity_mismatch'}); continue
            if quantity.startswith('pressure_') and ca['projection']!=cb['projection']:
                curves.append({'stageId':stage_id,'quantity':quantity,'status':'projection_mismatch'}); continue
            sign='pressure' if quantity.startswith('pressure_') else quantity
            if reference['signs'][sign]!=candidate['signs'][sign]:
                curves.append({'stageId':stage_id,'quantity':quantity,'status':'sign_mismatch'}); continue
            metrics=_compare_channel(ca,cb,a['domainM'],boundaries,absolute[quantity],rtol,ztol)
            curves.append({'stageId':stage_id,**metrics})
        sa={s['id']:s for s in a['supports']}; sb={s['id']:s for s in b['supports']}
        for key in sorted(set(sa)|set(sb)):
            if key not in sa or key not in sb:
                support_results.append({'stageId':stage_id,'supportId':key,'status':'incomplete'}); continue
            x,y=sa[key],sb[key]
            if x['status']!=y['status'] or abs(x['depthM']-y['depthM'])>ztol:
                support_results.append({'stageId':stage_id,'supportId':key,'status':'identity_mismatch'}); continue
            if x['status']=='not_installed':
                support_results.append({'stageId':stage_id,'supportId':key,'status':'inactive_matched'}); continue
            if x['status']=='removed':
                metric=_value_comparison(x['referenceDisplacementMm'],y['referenceDisplacementMm'],absolute['u'],rtol)
                support_results.append({'stageId':stage_id,'supportId':key,'status':metric['status'],
                                        'metrics':{'referenceDisplacementMm':metric}}); continue
            if x['status']=='unconfirmed':
                support_results.append({'stageId':stage_id,'supportId':key,'status':'incomplete'}); continue
            metrics={field:_value_comparison(x[field],y[field],absolute['u'],rtol)
                     for field in ('referenceDisplacementMm','displacementMm')}
            metrics['reaction']=_value_comparison(x['reaction']['value'] if x['reaction'] else None,
                y['reaction']['value'] if y['reaction'] else None,absolute['support'],rtol)
            metrics['memberAxialForceKn']=_value_comparison(x['memberAxialForceKn'],y['memberAxialForceKn'],absolute['support'],rtol)
            if x['memberAxialForceKn'] is None and y['memberAxialForceKn'] is None:
                metrics['memberAxialForceKn']['status']='not_applicable'
            horizontal=[m for k,m in metrics.items() if k!='memberAxialForceKn']
            status='incomplete' if any(m['status']=='incomplete' for m in horizontal) else (
                'within_tolerance' if all(m['status']=='within_tolerance' for m in horizontal) else 'outside_tolerance')
            support_results.append({'stageId':stage_id,'supportId':key,'status':status,'metrics':metrics})
        if a['design'] is None or b['design'] is None:
            design_results.append({'stageId':stage_id,'status':'incomplete'}); continue
        metrics={key:_value_comparison(a['design'][key],b['design'][key],
                   1e-10 if key=='effectFactor' else absolute['M' if key.startswith('moment') else 'V'],rtol)
                 for key in ('effectFactor','momentMaxDesignKnM','shearMaxDesignKn')}
        status='within_tolerance' if all(m['status']=='within_tolerance' for m in metrics.values()) else 'outside_tolerance'
        if a['design']['combination']!=b['design']['combination']: status='identity_mismatch'
        design_results.append({'stageId':stage_id,'status':status,'metrics':metrics})
    envelopes=[]
    selected_a={**candidate,'stages':[by_candidate[s] for s in expected if s in by_candidate]}
    selected_b={**ref,'stages':[by_ref[s] for s in expected if s in by_ref]}
    for quantity in ('u','M','V'):
        a=_governing(selected_a,quantity,absolute[quantity]); b=_governing(selected_b,quantity,absolute[quantity])
        if a is None or b is None:
            envelopes.append({'quantity':quantity,'status':'incomplete'}); continue
        metrics={key:_value_comparison(a[key],b[key],absolute[quantity],rtol) for key in ('minimum','maximum')}
        ties=(a['minimumStageIds']==b['minimumStageIds'] and a['maximumStageIds']==b['maximumStageIds'])
        envelopes.append({'quantity':quantity,'candidate':a,'reference':b,'metrics':metrics,
                          'status':'within_tolerance' if ties and all(m['status']=='within_tolerance' for m in metrics.values()) else 'outside_tolerance'})
    rebar=[]; ra={r['id']:r for r in candidate['reinforcement']}; rb={r['id']:r for r in ref['reinforcement']}
    for key in sorted(set(ra)|set(rb)):
        if key not in ra or key not in rb:
            rebar.append({'id':key,'status':'incomplete'}); continue
        a,b=ra[key],rb[key]
        metrics={k:_value_comparison(a[k],b[k],absolute['rebar'],rtol) for k in ('requiredAreaMm2','providedAreaMm2')}
        status='within_tolerance' if all(m['status']=='within_tolerance' for m in metrics.values()) else 'outside_tolerance'
        if any(abs(a[k]-b[k])>ztol for k in ('fromDepthM','toDepthM')) or a['method']!=b['method']:
            status='identity_mismatch'
        if a['governingStageIds']!=b['governingStageIds']: status='governing_stage_mismatch'
        rebar.append({'id':key,'status':status,'metrics':metrics})
    outputs=curves+support_results+design_results+envelopes+rebar
    full=(not missing_stages and not missing_channels and len(curves)==len(expected)*len(REQUIRED_QUANTITIES)
          and bool(rebar) and all(o['status'] in {'within_tolerance','outside_tolerance','inactive_matched'} for o in outputs))
    numeric='incomplete' if not full else ('within_tolerance' if all(o['status'] in {'within_tolerance','inactive_matched'} for o in outputs) else 'outside_tolerance')
    same_conditions=not blockers
    overall=('conditions_mismatch' if any('mismatch' in b for b in blockers) else 'incomplete') if blockers else numeric
    return {'schemaVersion':'pitguard.reference-comparison/1','status':overall,'numericalStatus':numeric,
            'sameConditionsVerified':same_conditions,'formalDesignReady':False,
            'contractHash':contract['contractHash'],'referenceBundleHash':reference['bundleHash'],
            'candidateBundleHash':candidate_raw['bundleHash'],'blockers':list(dict.fromkeys(blockers)),
            'conditionAssessment':assessment,'conditionMatches':condition_matches,
            'coverage':{'expectedStageIds':expected,'missingStages':missing_stages,'missingChannels':missing_channels,
                        'fullCurveAcceptance':full and same_conditions,
                        'displacementMomentShearComplete':not missing_stages and not any(
                            m['quantity'] in {'u','M','V'} for m in missing_channels) and
                            len([c for c in curves if c['quantity'] in {'u','M','V'}])==len(expected)*3 and
                            all(c['status'] in {'within_tolerance','outside_tolerance'} for c in curves if c['quantity'] in {'u','M','V'}),
                        'pressureProjectionComplete':not missing_stages and not any(
                            m['quantity'].startswith('pressure_') for m in missing_channels) and
                            len([c for c in curves if c['quantity'].startswith('pressure_')])==len(expected)*2 and
                            all(c['status'] in {'within_tolerance','outside_tolerance'} for c in curves if c['quantity'].startswith('pressure_'))},
            'curves':curves,'supports':support_results,'designEffects':design_results,'envelopes':envelopes,
            'reinforcement':rebar,'tolerances':deepcopy(tolerances),
            'limitations':['Gauss/nodal-lumping pressure has no fabricated boundary values.',
                           'Line reactions are not physical support axial force.',
                           'Matching numeric data alone does not authenticate historical software conditions.']}


def a3_curve_template(contract: dict) -> dict:
    """Empty, deliberately unconfirmed original-result intake; no invented nodes."""
    from .reference_contract import A3_CASE
    validate_reference_contract(contract)
    if contract['caseId'] != A3_CASE:
        raise ValueError('A3 template requires its original contract')
    source=next(s for s in contract['sourceRegistry'] if s['id']=='A_PDF')
    stages=[]
    for index,event in enumerate(('excavate','install','excavate','install','remove'),1):
        curves=[]
        for quantity in REQUIRED_QUANTITIES:
            pressure=quantity.startswith('pressure_')
            curves.append({'quantity':quantity,
                           'unit':'kPa' if pressure else {'u':'mm','M':'kN*m','V':'kN'}[quantity],
                           'basis':'stress' if pressure else ('geometric' if quantity=='u' else 'unconfirmed'),
                           'pressureIdentity':{'stress':'unconfirmed','composition':'unconfirmed','role':'unconfirmed'} if pressure else None,
                           'projection':'native_numeric','samples':[]})
        stages.append({'id':f'S{index}','sourceStageId':str(index),'event':event,
                       'domainM':[1.3,7.9],'boundariesM':[1.625,1.8,4.6,7.,7.6],
                       'curves':curves,'supports':[],'design':None})
    return freeze_curve_bundle({'schemaVersion':SCHEMA,'caseId':contract['caseId'],
        'contractHash':contract['contractHash'],
        'source':{'kind':'numeric_table_transcription','sha256':source['sha256'],
                  'software':'同济启明星 FRWS','version':'9.0 (reported; exact build unconfirmed)'},
        'datum':{'origin':'model_ground','positive':'down','offsetToWallTopM':-1.3,
                 'evidence':'Derived from PDF p32 dimension chain and p33 axes; retain original depths'},
        'forceBasis':{'kind':'unconfirmed','pileSpacingM':.9,'conversion':'unconfirmed'},
        'signs':{key:'unconfirmed' for key in SIGNS},'effectBasis':'unconfirmed',
        'requiredStageIds':list(contract['stageIds']),'stages':stages,'reinforcement':[],
        'tolerances':_default_tolerances()})
