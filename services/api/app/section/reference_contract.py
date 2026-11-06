"""Evidence-preserving historical reference contracts, independent of solver defaults.

A hash detects changes; it does not authenticate a vendor file or engineer signature.
Confirmation records are supplied evidence records, never generated approvals.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re

from .models import content_hash, canonical_json

SCHEMA = 'pitguard.reference-contract/1'
A3_CASE = 'PROJECT_A_SECTION_4_5_3_3_LONG_PILE'
SHA256 = re.compile(r'^[0-9a-f]{64}$')
SOURCE_KINDS = {'report_transcription', 'original_solver_export', 'parameter_export',
                'digitized_curve', 'synthetic_protocol_fixture', 'cad_drawing', 'application_archive'}
CRITICAL_IDS = (
    'materials.pile_concrete_E', 'materials.pile_EI_reduction',
    'soil.reaction_method_name', 'soil.reaction_formula', 'soil.effective_soil_reaction_width',
    'soil.passive_limit_and_tension_cutoff', 'soil.excavation_unloading_and_stress_history',
    'critical.m_depth_origin', 'critical.m_layer_transition', 'critical.m_units',
    'boundary_and_solver_missing.pile_head_rotation',
    'boundary_and_solver_missing.pile_toe_translation_and_rotation',
    'boundary_and_solver_missing.output_force_basis', 'boundary_and_solver_missing.support_force_recovery',
    'datum.wall_top_offset_m', 'datums.candidate_solver_axis', 'water.outside_water_during_stages',
    'water.water_unit_weight', 'water.pressure_combination', 'critical.water_combination_definition',
    'loads.adjacent_surcharge.lateral_pressure_distribution', 'critical.slope_pressure_method',
    'supports.crown_beam_equivalent_support.application_location_and_combination',
    'supports.S1.preload', 'critical.support_installation_reference', 'critical.stage_lifecycle',
    'historical_code_edition', 'critical.characteristic_combination',
    'reinforcement.moment_factor', 'reinforcement.shear_factor', 'critical.rebar_design_basis',
)


def _shape(value: dict, keys: set[str], label: str):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f'{label}: unexpected or missing fields')


def _text(value, label: str, limit: int = 4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label}: nonempty bounded text required')


def _validate(contract: dict, sealed: bool):
    keys = {'schemaVersion', 'caseId', 'sourceRegistry', 'parameters', 'criticalConditionIds', 'stageIds'}
    _shape(contract, keys | ({'contractHash'} if sealed else set()), 'reference contract')
    if contract['schemaVersion'] != SCHEMA:
        raise ValueError('Unsupported reference contract schema')
    _text(contract['caseId'], 'caseId', 100)
    # Bound nested input size and reject NaN/Infinity before hashing.
    if len(canonical_json(contract)) > 4_000_000:
        raise ValueError('Reference contract exceeds size limit')
    if sealed:
        digest = content_hash({k: v for k, v in contract.items() if k != 'contractHash'})
        if contract['contractHash'] != digest:
            raise ValueError('Reference contract hash mismatch')
    registry = contract['sourceRegistry']
    if not isinstance(registry, list) or not 1 <= len(registry) <= 100:
        raise ValueError('sourceRegistry must contain 1–100 sources')
    sources = {}
    for source in registry:
        if not isinstance(source, dict) or not {'id', 'kind', 'file', 'sha256'} <= source.keys():
            raise ValueError('Invalid source registry entry')
        _text(source['id'], 'source id', 100)
        _text(source['file'], 'source filename', 2000)
        if source['kind'] not in SOURCE_KINDS or not SHA256.fullmatch(str(source['sha256'])):
            raise ValueError('Invalid source kind/hash')
        if source['id'] in sources:
            raise ValueError('Duplicate source id')
        sources[source['id']] = source
    parameters = contract['parameters']
    if not isinstance(parameters, list) or not 1 <= len(parameters) <= 2000:
        raise ValueError('parameters must contain 1–2000 fields')
    seen = set()
    parameter_keys = {'id', 'value', 'unit', 'status', 'originalStatus', 'originalValue', 'identity',
                      'sourceRefs', 'derivation', 'requiresConfirmation', 'confirmation', 'inputPath', 'note'}
    for p in parameters:
        _shape(p, parameter_keys, 'parameter')
        _text(p['id'], 'parameter id', 250)
        if p['id'] in seen:
            raise ValueError('Duplicate parameter id')
        seen.add(p['id'])
        if p['status'] not in {'original_fact', 'derived_interpretation', 'unconfirmed'}:
            raise ValueError('Unknown evidence status')
        if p['originalStatus'] not in {'original_fact', 'derived_interpretation', 'missing', 'unconfirmed'}:
            raise ValueError('Invalid original status')
        if p['status'] == 'unconfirmed' and p['value'] is not None:
            raise ValueError('Unconfirmed parameter must remain null')
        if p['status'] != 'unconfirmed' and p['value'] is None:
            raise ValueError('Known parameter requires value')
        if not isinstance(p['requiresConfirmation'], bool):
            raise ValueError('requiresConfirmation must be boolean')
        if p['unit'] is not None and not isinstance(p['unit'], str):
            raise ValueError('Invalid parameter unit')
        _text(p['identity'], 'parameter identity')
        if p['inputPath'] is not None:
            _text(p['inputPath'], 'solver input path', 250)
        if p['status'] == 'derived_interpretation' and not p['derivation']:
            raise ValueError('Derived field needs explicit derivation')
        if not isinstance(p['sourceRefs'], list) or not p['sourceRefs']:
            raise ValueError('Each parameter needs source evidence or a missing-source locator')
        for ref in p['sourceRefs']:
            if not isinstance(ref, dict) or not {'sourceId', 'sha256', 'locator', 'pages'} <= ref.keys():
                raise ValueError('Invalid parameter source reference')
            source = sources.get(ref['sourceId'])
            if source is None or ref['sha256'] != source['sha256']:
                raise ValueError('Parameter source hash mismatch')
            _text(ref['locator'], 'source locator')
            if not isinstance(ref['pages'], list) or any(type(n) is not int or n < 1 for n in ref['pages']):
                raise ValueError('Invalid physical source pages')
        confirmation = p['confirmation']
        if confirmation is not None:
            _shape(confirmation, {'providedBy', 'providedAt', 'sourceId', 'sourceSha256', 'locator', 'note'},
                   'confirmation')
            for key in ('providedBy', 'providedAt', 'locator', 'note'):
                _text(confirmation[key], 'confirmation ' + key)
            source = sources.get(confirmation['sourceId'])
            if source is None or source['sha256'] != confirmation['sourceSha256']:
                raise ValueError('Confirmation source hash mismatch')
            if source['kind'] not in {'original_solver_export', 'parameter_export', 'report_transcription'}:
                raise ValueError('Synthetic/digitized/CAD source cannot confirm original conditions')
        changed = p['value'] != p['originalValue'] or (
            p['status'] == 'original_fact' and p['originalStatus'] != 'original_fact')
        if changed and confirmation is None:
            raise ValueError('Changed historical value/status requires supplied source confirmation')
    critical = contract['criticalConditionIds']
    if not isinstance(critical, list) or not critical or len(set(critical)) != len(critical):
        raise ValueError('Invalid critical condition list')
    if not set(critical) <= seen:
        raise ValueError('Unknown critical condition id')
    if contract['caseId'] == A3_CASE and not set(CRITICAL_IDS) <= set(critical):
        raise ValueError('A3 critical conditions cannot be removed')
    stages = contract['stageIds']
    if not isinstance(stages, list) or not stages or len(set(stages)) != len(stages):
        raise ValueError('Invalid reference stage ids')
    if contract['caseId'] == A3_CASE and stages != ['S1', 'S2', 'S3', 'S4', 'S5']:
        raise ValueError('Original A3 requires five distinct stages')


def freeze_reference_contract(value: dict) -> dict:
    """Return a detached, content-addressed contract; never promotes evidence states."""
    data = deepcopy(value)
    if 'contractHash' in data:
        validate_reference_contract(data)
        return data
    _validate(data, sealed=False)
    data['contractHash'] = content_hash(data)
    return data


def validate_reference_contract(contract: dict) -> None:
    _validate(contract, sealed=True)


def assess_reference_contract(contract: dict) -> dict:
    validate_reference_contract(contract)
    fields = {p['id']: p for p in contract['parameters']}
    missing = []
    for key in contract['criticalConditionIds']:
        p = fields[key]
        reason = None
        if p['status'] == 'unconfirmed':
            reason = 'original_condition_not_provided'
        elif p['requiresConfirmation'] and p['confirmation'] is None:
            reason = 'interpretation_requires_source_confirmation'
        if reason:
            missing.append({'id': key, 'reason': reason, 'identity': p['identity']})
    return {'status': 'incomplete' if missing else 'ready', 'contractHash': contract['contractHash'],
            'missingConditions': missing, 'counts': dict(Counter(p['status'] for p in fields.values())),
            'formalDesignReady': False,
            'note': 'Evidence completeness is not solver equivalence or designer approval.'}


def build_a3_reference_contract(audit: dict) -> dict:
    """Flatten the audited original facts without consuming missing values as defaults."""
    if audit.get('case_id') != A3_CASE:
        raise ValueError('Not the audited A3-3 original case')
    sources = []
    kinds = {'published_design_report_pdf': 'report_transcription',
             'cad_drawing_not_solver_native_model': 'cad_drawing',
             'PitGuard_source_application_package_not_identified_as_FRWS_model': 'application_archive'}
    for source in audit['source_registry']:
        sources.append({'id': source['id'], 'file': source['file'], 'sha256': source['sha256'],
                        'kind': kinds[source['type']]})
    by_hash = {s['sha256']: s['id'] for s in sources}
    parameters = []

    def walk(value, path=''):
        if isinstance(value, dict):
            if {'value', 'status', 'source'} <= value.keys():
                source = value['source']
                state = 'unconfirmed' if value['status'] == 'missing' else value['status']
                parameters.append({
                    'id': path, 'value': deepcopy(value['value']), 'unit': value.get('unit'), 'status': state,
                    'originalStatus': value['status'], 'originalValue': deepcopy(value['value']),
                    'identity': value['identity'],
                    'sourceRefs': [{'sourceId': by_hash[source['sha256']], 'sha256': source['sha256'],
                                    'locator': source['locator'], 'pages': source['physical_pdf_pages_1_based']}],
                    'derivation': value.get('derivation') or (value.get('note') if state == 'derived_interpretation' else None),
                    'requiresConfirmation': value.get('requires_confirmation_for_original_software_equivalence',
                                                       state == 'unconfirmed'),
                    'confirmation': None, 'inputPath': None, 'note': value.get('note', ''),
                })
                return
            for key, child in value.items():
                walk(child, path + '.' + key if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f'{path}.{index}')
    walk(audit)
    fields = {p['id']: p for p in parameters}
    source_refs = deepcopy(fields['geometry.pile_top_depth_candidate']['sourceRefs'])
    source_refs[0]['pages'] = [32, 33]
    source_refs[0]['locator'] = 'p32 section dimension chain and p33 stage table/response axes'

    def add(key, value, unit, identity, derivation=None, confirm=False, refs=None):
        state = 'unconfirmed' if value is None else 'derived_interpretation'
        parameters.append({'id': key, 'value': value, 'unit': unit, 'status': state,
                           'originalStatus': state, 'originalValue': deepcopy(value), 'identity': identity,
                           'sourceRefs': deepcopy(refs or source_refs), 'derivation': derivation,
                           'requiresConfirmation': confirm or value is None, 'confirmation': None,
                           'inputPath': None, 'note': ''})
    offset = fields['geometry.pile_top_depth_candidate']['value']
    local = lambda v: round(v - offset, 10)
    add('datum.wall_top_offset_m', offset, 'm', 'Ground depth of wall top; preserve overburden',
        'p32 prints ground-to-top 1.3; p33 curves start at depth1.3; z_wall=z_ground-1.3')
    add('datum.local_excavation_depths_m', [local(s['effective_excavation_depth']['value']) for s in audit['stages']],
        'm', 'Derived excavation depths below wall top', 'Each original stage excavation depth minus wall-top ground depth')
    add('datum.local_support_depths_m', {key: local(audit['supports'][key]['depth']['value']) for key in ('S1','T1')},
        'm', 'Derived S1/T1 depths below wall top', 'Original support depth minus1.3; S1=.325 also printed in p32 chain')
    add('datum.local_outside_water_m', local(audit['water']['initial_outside_groundwater_depth']['value']),
        'm', 'Water above wall top has negative local depth; do not clip', '0.5-1.3=-0.8')
    add('datum.local_inside_water_depths_m', [local(s['inside_phreatic_depth']['value']) for s in audit['stages']],
        'm', 'Derived stage water depths below wall top', 'Original stage inside water depths minus1.3')
    interfaces = [audit['soil']['layers'][0]['top_depth']['value']]
    interfaces += [layer['bottom_depth']['value'] for layer in audit['soil']['layers']]
    add('datum.local_layer_interfaces_m', [local(v) for v in interfaces], 'm',
        'Original complete stratigraphy in wall-local coordinates including overburden',
        'Cumulative original layer thickness interfaces minus1.3; no layers discarded')
    historical = deepcopy(fields['identity.design_standard'])
    historical['id'] = 'historical_code_edition'
    parameters.append(historical)
    add('current_review_basis', None, None, 'Current review edition and scope; independent from historical input')
    for key, identity in {
        'm_depth_origin': 'Original m-law depth origin', 'm_layer_transition': 'Original m-law layer transition',
        'water_combination_definition': 'Original layerwise total/effective water pressure composition',
        'slope_pressure_method': 'Original slope/bench pressure distribution method',
        'support_installation_reference': 'Original installation displacement and force history',
        'stage_lifecycle': 'Exact original event/state inheritance including separate install/remove',
        'characteristic_combination': 'Original characteristic force/load combination semantics',
        'rebar_design_basis': 'Original concrete/steel strengths and reinforcement calculation options',
    }.items():
        add('critical.' + key, None, None, identity)
    add('critical.m_units', '1 MPa/m² = 1000 kN/m⁴', None, 'Original m-column dimensional conversion',
        'MPa=1000 kN/m²; divide by m²')
    data = {'schemaVersion': SCHEMA, 'caseId': A3_CASE, 'sourceRegistry': sources, 'parameters': parameters,
            'criticalConditionIds': list(CRITICAL_IDS), 'stageIds': ['S1','S2','S3','S4','S5']}
    return freeze_reference_contract(data)
