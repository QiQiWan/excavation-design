"""Immutable Luoyu 3-3 benchmark intake with fail-closed evidence gates.

This module cannot authenticate a design institute's signature or approve a
method. Historic reference values remain unverified until backed by uploaded
source bytes and separate professional review. All original stage/connection
comparisons reuse the existing 3.89.1 engineering evidence implementation.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED
from datetime import datetime, timezone
from math import isfinite
import json
import csv
from io import StringIO
import os
import re
import tempfile

from .field_case_intake import FieldCase, blank_case_template, intake_field_case
from .original_report_pdf import read_design_report_pdf, REPORT_SCHEMA
from ..normative.reference_review import luoyu_three_three, audit_original_report_arithmetic

SCHEMA = 'pitguard.luoyu-fixed-acceptance/3.89.6'
RECORD_SCHEMA = 'pitguard.luoyu-acceptance-record/3.89.7'
ARCHIVE_PARTS = ('benchmark.json', 'field_case.zip')
MAX_UPLOAD_BYTES = 33_000_000
MAX_ENTRY_BYTES = 32_000_000
HASH = re.compile(r'^[a-f0-9]{64}$')
STAGES = ('S1', 'S2', 'S3', 'S4', 'S5')

# Names and units are fixed by the current historical excerpt, not the
# original design office. An inconsistent revision must be reviewed explicitly.
PARAMETERS = {
    'pileDiameterM': 'm',
    'pileSpacingM': 'm',
    'pileLengthM': 'm',
    'pileTopOriginalGroundDepthM': 'm',
    'siteFinalExcavationDepthM': 'm',
    'siteSupportDepthM': 'm',
    'siteReplacementLevelM': 'm',
    'outsideWaterOriginalGroundDepthM': 'm',
    'strutStiffnessMNPerM2': 'MN/m2',
    'finiteAdjacentSurcharge.pressureKpa': 'kPa',
    'finiteAdjacentSurcharge.nearDistanceM': 'm',
    'finiteAdjacentSurcharge.widthM': 'm',
    'finiteAdjacentSurcharge.lengthM': 'm',
    'terrain.slopeHeightM': 'm',
    'terrain.slopeWidthM': 'm',
    'terrain.benchWidthM': 'm',
    'soilLayersOriginalGround': 'structured_layer_table',
}

METHODS = {
    'geotechnical_soil_and_pressure': ('JGJ 120-2012 3.4', 'geotechnical_report'),
    'groundwater_and_seepage': ('JGJ 120-2012 4.2, 7, Appendix C', 'geotechnical_report'),
    'retaining_embedment_and_stability': ('JGJ 120-2012 4.2', 'independent_calculation'),
    'circular_pile_nm_shear': ('JGJ 120-2012 4.3 and Appendix B; applicable concrete standard', 'independent_calculation'),
    'circular_pile_service_crack': ('project-approved applicable circular-section crack method', 'method_qualification'),
    'support_and_wale_transfer': ('JGJ 120-2012 4.9', 'connection_detail'),
    'toe_crown_opposite_wall': ('project-specific toe/crown/opposite-wall boundary', 'independent_calculation'),
    'terrain_and_finite_surcharge': ('project-specific sloped ground and finite surcharge', 'independent_calculation'),
    'installation_replacement_removal': ('JGJ 120-2012 8.1; project construction sequence', 'independent_calculation'),
    'connection_anchorage_detail': ('applicable concrete/steel and connection requirements', 'connection_detail'),
}


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _strict_json(raw: bytes, name: str) -> dict:
    if len(raw) > 2_000_000:
        raise ValueError(name + ' exceeds 2 MB')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(name + ': duplicate JSON key ' + key)
            obj[key] = value
        return obj
    obj = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                     parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite value: ' + v)))
    if not isinstance(obj, dict):
        raise ValueError(name + ' must be an object')
    return obj


def _read_archive(raw: bytes) -> dict[str, bytes]:
    if not isinstance(raw, bytes) or not 20 < len(raw) <= MAX_UPLOAD_BYTES:
        raise ValueError('Fixed acceptance ZIP exceeds transport budget or is empty')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [x.filename for x in entries]
            if len(entries) != len(ARCHIVE_PARTS) or set(names) != set(ARCHIVE_PARTS) or len(names) != len(set(names)):
                raise ValueError('ZIP must contain benchmark.json and field_case.zip exactly once')
            contents = {}
            for item in entries:
                if item.is_dir() or item.flag_bits & 1 or item.file_size > MAX_ENTRY_BYTES:
                    raise ValueError('Unsafe, encrypted, or oversized ZIP member')
                if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('ZIP member symlink is forbidden')
                if item.compress_size and item.file_size / item.compress_size > 1000:
                    raise ValueError('Suspicious ZIP compression ratio')
                data = archive.read(item, pwd=None)
                if len(data) != item.file_size:
                    raise ValueError('ZIP member length mismatch')
                contents[item.filename] = data
            return contents
    except (BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise ValueError('Invalid acceptance ZIP') from exc


def _reference_value(facts: dict, key: str):
    obj = facts
    for part in key.split('.'):
        obj = obj[part]
    return obj


def _same_historical(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return isfinite(float(a)) and isfinite(float(b)) and abs(float(a) - float(b)) <= max(1e-9, abs(float(b)) * 1e-8)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            _same_historical(x, y) if isinstance(x, (int, float, list)) and isinstance(y, (int, float, list))
            else _canonical(x) == _canonical(y) for x, y in zip(a, b))
    return False


def requirement_catalog() -> dict:
    ref = luoyu_three_three()
    facts = ref['facts']
    return {
        'schemaVersion': SCHEMA,
        'projectId': ref['project'], 'sectionId': ref['section'],
        'historicalReference': {
            'evidenceStatus': 'text_excerpt_only_unverified_original_bytes',
            'sourceFileNameAsRecorded': ref['source']['file'],
            'sourcePagesAsRecorded': ref['source']['physicalPages'],
            'historicalReferenceHash': sha256(_canonical(ref)).hexdigest(),
            'originalNativeStageArraysPresent': False,
            'pdfPhysicalPageAuditSupported': True,
        },
        'requiredDocumentRoles': [
            'original_design_report', 'original_construction_drawing',
            'geotechnical_report', 'original_numeric_export',
            'connection_detail', 'independent_calculation',
            'method_qualification', 'inspection_report',
            'water_level_observation',
        ],
        'parameters': [{'name': name, 'unit': unit, 'historicalValue': _reference_value(facts, name),
                        'status': 'historic_unverified'} for name, unit in PARAMETERS.items()],
        'methods': [{'id': mid, 'reference': desc, 'keyEvidenceRole': role,
                     'requiresSeparateMethodQualification': True}
                    for mid, (desc, role) in METHODS.items()],
        'stages': list(STAGES),
        'numericChannels': ['displacementMm','momentKnM','shearKn','pressureKpa','reactionKnPerPile','referenceMm'],
        'formalConstructionIssue': False,
    }


def blank_template() -> bytes:
    spec = requirement_catalog()
    overlay = {
        'schemaVersion': SCHEMA, 'projectId': spec['projectId'], 'sectionId': '3-3',
        'revision': 'REPLACE_WITH_DESIGN_DOCUMENT_REVISION',
        'parameters': [], 'methods': [],
        'notes': 'Fill using actual design-office source files; blanks are intentional.',
    }
    case_data = blank_case_template()
    case_data['revision'] = overlay['revision']
    case = _canonical(case_data)
    stream = BytesIO()
    nested = BytesIO()
    with ZipFile(nested, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('case.json', case)
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('benchmark.json', _canonical(overlay))
        archive.writestr('field_case.zip', nested.getvalue())
    return stream.getvalue()




def _audit_water_observation_files(case: FieldCase, nested_zip: bytes) -> dict:
    """Parse actual CSV document bytes and pair stage-matched inside/outside wells.

    CSV waterDepthM is positive downward from original ground, while the
    hydrology comparator uses a signed upward head relative to that datum.
    Time stamps require an explicit UTC offset. This is field-observation
    completeness screening, never a design adverse water-head decision.
    """
    from ..assurance.field_hydrology import (
        WaterLevelObservation, ObservationReviewInput, reconcile_observations)
    observations = []
    declared = [d for d in case.documents if d.kind == 'water_level_observation']
    if not declared:
        return {'status': 'missing_original_water_level_observations',
                'stageCoverage': {stage: False for stage in STAGES},
                'pairedCount': 0, 'sourceFileSha256': [],
                'designAdverseHeadEstablished': False}
    columns = ['stageId','side','wellId','observedAt','waterDepthM','quality']
    with ZipFile(BytesIO(nested_zip)) as source:
        for record in declared:
            raw = source.read(record.path)
            if len(raw) > 2_000_000:
                raise ValueError('Water observation CSV exceeds 2 MB')
            try:
                decoded = raw.decode('utf-8-sig')
            except UnicodeError as exc:
                raise ValueError('Water observation CSV must be UTF-8') from exc
            reader = csv.DictReader(StringIO(decoded, newline=''), strict=True)
            if reader.fieldnames != columns:
                raise ValueError('Water observation CSV requires exact header: ' + ','.join(columns))
            try:
                rows = list(reader)
            except csv.Error as exc:
                raise ValueError('Malformed water observation CSV') from exc
            for line_no, row in enumerate(rows, 2):
                if line_no > 2002:
                    raise ValueError('Water observation count exceeds bound')
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('Invalid water observation CSV row')
                if row['stageId'] not in STAGES or row['side'] not in ('inside','outside'):
                    raise ValueError('Water observation stage/side mismatch')
                try:
                    depth = float(row['waterDepthM'])
                except ValueError as exc:
                    raise ValueError('Invalid waterDepthM') from exc
                if not isfinite(depth) or not -100 <= depth <= 500:
                    raise ValueError('Water level depth beyond validated field range')
                obs = WaterLevelObservation(
                    project_id=case.project_id,section_id=case.section_id,
                    stage_id=row['stageId'],well_id=row['wellId'],side=row['side'],
                    observed_at=row['observedAt'],head_m=-depth,
                    quality=row['quality'],source_sha256=record.sha256,source_row=line_no)
                observations.append(obs)
    if len(observations) < 2:
        raise ValueError('At least one inside/outside water observation pair required')
    audit = reconcile_observations(ObservationReviewInput(
        project_id=case.project_id, section_id=case.section_id,
        observations=tuple(observations), max_pair_gap_minutes=30.0))
    coverage = {stage: any(r['stageId'] == stage and r['pairedCount'] > 0
                    for r in audit['stages']) for stage in STAGES}
    return {'status': 'paired_all_five_stages_requires_site_review' if all(coverage.values())
              else 'incomplete_five_stage_water_pairing',
            'stageCoverage': coverage, 'pairedCount': audit['pairedCount'],
            'maxObservedDeltaHeadM': audit['maxObservedPairedDeltaHeadM'],
            'sourceFileSha256': [d.sha256 for d in declared],
            'stagePairs': audit['stages'],
            'waterDepthDatum': 'original_ground_down_m',
            'maxPairGapMinutes': 30,
            'designAdverseHeadEstablished': False,
            'instrumentsAndBoundaryAuthenticated': False}


def _source_matrix(case: FieldCase):
    roles = {}
    for doc in case.documents:
        roles.setdefault(doc.sha256, set()).add(doc.kind)
    return roles


def _audit_original_report_pdf(case: FieldCase, nested_blob: bytes) -> dict:
    """Read real PDF member bytes after field-case hash validation has succeeded."""
    selected = [d for d in case.documents if d.kind == 'original_design_report'
                and d.path.lower().endswith('.pdf')]
    if not selected:
        return {'status': 'not_supplied_as_pdf', 'sourcePdfSha256': None,
                'sourceFileBytesParsed': False, 'printedStageExtremaAreNotNativeSamples': True}
    if len(selected) > 1:
        return {'status': 'multiple_original_pdf_sources_require_revision_selection',
                'sourceFileBytesParsed': False, 'sourcePdfSha256': None,
                'printedStageExtremaAreNotNativeSamples': True}
    doc = selected[0]
    try:
        with ZipFile(BytesIO(nested_blob)) as source:
            data = source.read(doc.path)
        # Field-case intake already checks every document SHA; verify again at
        # this boundary to prevent a later refactor weakening this invariant.
        if sha256(data).hexdigest() != doc.sha256:
            raise ValueError('original report PDF SHA-256 mismatches field-case manifest')
        result = read_design_report_pdf(data)
    except ValueError as exc:
        return {'status': 'pdf_parse_or_section_audit_requires_manual_resolution',
                'sourceFileBytesParsed': False, 'sourcePdfSha256': doc.sha256,
                'reason': str(exc)[:240], 'printedStageExtremaAreNotNativeSamples': True}
    return {'status': 'source_bytes_and_printed_report_excerpts_matched',
            'sourceFileBytesParsed': True, 'sourcePdfSha256': doc.sha256,
            'report': result, 'printedStageExtremaAreNotNativeSamples': True}


def evaluate(raw: bytes, *, frozen_result: dict | None = None) -> dict:
    contents = _read_archive(raw)
    overlay = _strict_json(contents['benchmark.json'], 'benchmark.json')
    if overlay.get('schemaVersion') != SCHEMA:
        raise ValueError('Unsupported fixed acceptance schema')
    allowed = {'schemaVersion','projectId','sectionId','revision','parameters','methods','notes'}
    if not set(overlay).issubset(allowed) or not {'schemaVersion','projectId','sectionId','revision','parameters','methods'}.issubset(overlay):
        raise ValueError('Fixed acceptance manifest contains missing or unsupported fields')
    ref = luoyu_three_three()
    if overlay.get('projectId') != ref['project'] or overlay.get('sectionId') != '3-3':
        raise ValueError('Fixed benchmark project and 3-3 section identity mismatch')
    rev = overlay.get('revision')
    if not isinstance(rev, str) or not 2 <= len(rev) <= 140:
        raise ValueError('Revision is required')
    field = intake_field_case(contents['field_case.zip'], frozen_result=frozen_result)
    with ZipFile(BytesIO(contents['field_case.zip'])) as nested:
        case = FieldCase.model_validate(_strict_json(nested.read('case.json'), 'field case.json'))
    if case.project_id != ref['project'] or case.section_id != '3-3' or case.revision != rev:
        raise ValueError('Nested field case project, section, and revision must match overlay')
    doc_roles = _source_matrix(case)
    pdf_review = _audit_original_report_pdf(case, contents['field_case.zip'])
    water_review = _audit_water_observation_files(case, contents['field_case.zip'])
    if frozen_result is not None and case.result_hash is None:
        raise ValueError('Uploaded benchmark has no frozen-result identity')

    params = overlay.get('parameters')
    if not isinstance(params, list) or len(params) > len(PARAMETERS):
        raise ValueError('Parameter records must be a bounded list')
    indexed = {}
    for record in params:
        if not isinstance(record, dict) or set(record) != {'name','unit','value','sourceSha256','sourceLocator'}:
            raise ValueError('Parameter record requires name, unit, value, sourceSha256 and sourceLocator')
        name = record['name']
        if name not in PARAMETERS or name in indexed or record['unit'] != PARAMETERS[name]:
            raise ValueError('Unknown, repeated or incorrectly unit-tagged parameter')
        digest = record['sourceSha256']
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError('Source SHA256 must be a lower-case hexadecimal digest')
        locator = record['sourceLocator']
        if not isinstance(locator, str) or not 4 <= len(locator) <= 400:
            raise ValueError('Parameter source locator must identify a specific page or row')
        indexed[name] = record
    parameter_review = []
    for name, unit in PARAMETERS.items():
        historic = _reference_value(ref['facts'], name)
        item = indexed.get(name)
        roles = doc_roles.get(item['sourceSha256'], set()) if item else set()
        if item is None:
            state = 'not_supplied'
        elif not roles.intersection({'geotechnical_report', 'original_design_report'}):
            state = 'source_bytes_or_role_missing'
        elif not _same_historical(item['value'], historic):
            state = 'historical_revision_difference_requires_resolution'
        else:
            state = 'declared_matches_historical_excerpt_source_bytes_present'
        machine_match = False
        extracted = None
        physical_page = None
        if (item is not None and pdf_review['status'] == 'source_bytes_and_printed_report_excerpts_matched'
                and item['sourceSha256'] == pdf_review['sourcePdfSha256']):
            source_report = pdf_review['report']['standardCase']
            extracted = source_report['parameters'].get(name)
            if name == 'soilLayersOriginalGround':
                # The layout/table text was checked against the recorded report,
                # but the full geological model still needs geotechnical originals.
                machine_match = False
                physical_page = 32
            elif extracted is not None:
                machine_match = _same_historical(item['value'], extracted)
                physical_page = 33 if name in ('siteReplacementLevelM',) else 32
                if not machine_match:
                    state = 'pdf_printed_value_disagrees_with_declared_parameter'
        parameter_review.append({'name': name, 'unit': unit, 'historicalValue': historic,
            'suppliedValue': item['value'] if item else None,
            'sourceSha256': item['sourceSha256'] if item else None,
            'sourceLocator': item['sourceLocator'] if item else None,
            'pdfPrintedValue': extracted, 'pdfPhysicalPage': physical_page,
            'sourceValueMachineExtractedAndMatched': machine_match,
            'sourceValueIndependentlyParsedAndConfirmed': False, 'status': state})

    methods = overlay.get('methods')
    if not isinstance(methods, list) or len(methods) > len(METHODS):
        raise ValueError('Methods must be a bounded list')
    method_by_id = {}
    for item in methods:
        if not isinstance(item, dict) or set(item) != {'id','methodRevision','applicabilityNote','qualificationSha256','calculationSha256','negativeCaseSha256'}:
            raise ValueError('Method entry has missing or unexpected fields')
        mid = item['id']
        if mid not in METHODS or mid in method_by_id:
            raise ValueError('Unknown or duplicate method identity')
        for field_name in ('qualificationSha256','calculationSha256','negativeCaseSha256'):
            digest = item[field_name]
            if not isinstance(digest, str) or not HASH.fullmatch(digest):
                raise ValueError('Invalid method source hash: ' + field_name)
        if len({item['qualificationSha256'], item['calculationSha256'], item['negativeCaseSha256']}) != 3:
            raise ValueError('Method qualification, independent calculation and negative case must be distinct files')
        if not isinstance(item['methodRevision'], str) or not 2 <= len(item['methodRevision']) <= 140:
            raise ValueError('Method revision is required')
        if not isinstance(item['applicabilityNote'], str) or not 12 <= len(item['applicabilityNote']) <= 800:
            raise ValueError('Method applicability note is required')
        method_by_id[mid] = item
    method_review = []
    for mid, (label, key_role) in METHODS.items():
        item = method_by_id.get(mid)
        missing = []
        if item is None:
            missing.append('method_declaration')
        else:
            for field_name, allowed in (
                ('qualificationSha256', {'method_qualification'}),
                ('calculationSha256', {'independent_calculation'}),
                ('negativeCaseSha256', {'inspection_report'}),
            ):
                if not doc_roles.get(item[field_name], set()).intersection(allowed):
                    missing.append(field_name + '_bytes_and_role')
            if key_role not in {doc.kind for doc in case.documents}:
                missing.append('required_domain_source_role_' + key_role)
        method_review.append({'id': mid, 'methodReference': label,
            'status': 'not_submitted' if item is None else 'incomplete_evidence' if missing else 'submitted_requires_independent_method_approval',
            'missing': missing, 'methodQualified': False,
            'declaredRevision': item['methodRevision'] if item else None})

    deep = field.get('deepEngineeringReview') or {}
    stage = deep.get('stageComparison') or {}
    original_numeric = field.get('originalNumericChannels') or []
    stages = stage.get('stages') or []
    all_stages = {s.get('stageId') for s in stages} == set(STAGES) and len(stages) == 5
    stage_status = stage.get('status', 'not_submitted')
    field_fail = field.get('status') == 'fail'
    numeric_failed = stage_status == 'fail' or any(x.get('status') == 'fail' for x in stages)
    stage_review = {'stageIdsPresent': all_stages, 'stageStatus': stage_status,
        'matchedValueCount': stage.get('matchedValueCount', 0),
        'originalNumericFileCount': len(original_numeric),
        'independentComparisonAvailable': bool(stages),
        'perStage': stages,
        'sameConditionQualification': False}
    if pdf_review.get('report'):
        extracted_stages = {x['stageId']: x for x in pdf_review['report']['standardCase']['stages']}
        stage_checks = []
        for source_stage in ref['stages']:
            stage_id = source_stage['id']
            printed = extracted_stages.get(stage_id)
            aligned = bool(printed) and all(
                _same_historical(source_stage[prop], printed['ranges'][channel])
                for prop, channel in (('displacementRangeMm','displacementMm'),
                                      ('momentRangeKnM','momentKnM'),('shearRangeKn','shearKn')))
            stage_checks.append({'stageId': stage_id, 'reportPhysicalPage': printed['physicalPage'] if printed else None,
                                 'historicalExtremaMatchReportText': aligned,
                                 'nativeDepthSamplesPresent': False})
        pdf_review['historicalExcerptStageComparison'] = stage_checks
    arithmetic = audit_original_report_arithmetic(ref)
    arithmetic_good = all(x['consistent'] for x in arithmetic['checkedRelationships'])
    role_states = [{'role': r, 'status': 'uploaded_bytes_verified' if r in {d.kind for d in case.documents} else 'missing'}
                   for r in requirement_catalog()['requiredDocumentRoles']]
    missing = []
    missing += ['role:' + x['role'] for x in role_states if x['status'] == 'missing']
    missing += ['parameter:' + x['name'] for x in parameter_review if x['status'] != 'declared_matches_historical_excerpt_source_bytes_present']
    missing += ['method:' + x['id'] for x in method_review if x['status'] != 'submitted_requires_independent_method_approval']
    if water_review['status'] != 'paired_all_five_stages_requires_site_review':
        missing.append('verified_S1_S5_inside_outside_water_observation_pairs')
    if not all_stages or stage_status != 'numeric_match_only':
        missing.append('independent_S1_S5_all_channel_numeric_comparison')
    if not field.get('nodeReviews') or not deep.get('detailedConnections'):
        missing.append('qualified_connection_details_and_node_actions')
    if deep.get('engineerRecordComparison') is None:
        missing.append('original_engineer_review_record_comparison')
    if frozen_result is None:
        missing.append('frozen_project_calculation_result_binding')
    if pdf_review['status'] != 'source_bytes_and_printed_report_excerpts_matched':
        missing.append('original_design_report_pdf_source_page_audit')
    elif not all(x['historicalExtremaMatchReportText'] for x in pdf_review['historicalExcerptStageComparison']):
        missing.append('original_design_report_pdf_printed_extrema_revision_conflict')
    if any(x['status']=='pdf_printed_value_disagrees_with_declared_parameter' for x in parameter_review):
        missing.append('declared_parameter_disagrees_with_original_pdf_text')
    if not arithmetic_good:
        missing.append('historical_reference_arithmetic_check')
    missing.append('independent_professional_method_qualification_and_source_authorship')
    if numeric_failed or field_fail or 'declared_parameter_disagrees_with_original_pdf_text' in missing:
        assessment = 'numerical_or_engineering_discrepancy'
    elif missing:
        assessment = 'blocked_missing_or_unreviewed_evidence'
    else:
        assessment = 'awaiting_professional_authenticity_and_method_approval'
    digest = sha256(raw).hexdigest()
    return {'schemaVersion': RECORD_SCHEMA, 'benchmarkId': digest,
        'archiveSha256': digest, 'referenceHash': sha256(_canonical(ref)).hexdigest(),
        'projectId': ref['project'], 'sectionId': '3-3', 'revision': rev,
        'assessmentStatus': assessment, 'referenceSourceVerified': False,
        'originalSourceAuthorshipAuthenticated': False,
        'professionalMethodApprovalVerified': False,
        'referenceArithmetic': arithmetic,
        'originalReportPdfAudit': pdf_review,
        'documentRoles': role_states, 'parameterComparison': parameter_review,
        'methodEvidence': method_review, 'waterObservations': water_review,
        'stageComparison': stage_review,
        'nodeReviewSummary': {'count': len(field.get('nodeReviews', [])),
            'statuses': [x['status'] for x in field.get('nodeReviews', [])],
            'detailedCount': len(deep.get('detailedConnections') or []),
            'independentEngineersReviewAvailable': deep.get('engineerRecordComparison') is not None},
        'missingOrUnreviewed': sorted(set(missing)),
        'coverage': {
            'originalDesignPdfSectionsParsed': bool(pdf_review['sourceFileBytesParsed']),
            'printedFiveStageExtremaVerifiedAgainstHistorical': sum(
                x['historicalExtremaMatchReportText'] for x in pdf_review.get('historicalExcerptStageComparison', [])),
            'sourcePdfParameterFieldsMachineMatched': sum(x['sourceValueMachineExtractedAndMatched'] for x in parameter_review),
            'documentRolesVerified': sum(x['status']=='uploaded_bytes_verified' for x in role_states),
            'documentRolesRequired': len(role_states),
            'parameterDeclarationsMatchedHistorical': sum(x['status']=='declared_matches_historical_excerpt_source_bytes_present' for x in parameter_review),
            'parametersRequired': len(parameter_review),
            'methodEvidenceCompleteForReview': sum(x['status']=='submitted_requires_independent_method_approval' for x in method_review),
            'methodsRequired': len(method_review),
            'observedWaterStagesPaired': sum(bool(v) for v in water_review['stageCoverage'].values()),
            'waterStagesRequired': len(STAGES),
            'independentStageNumericComparisonValues': stage.get('matchedValueCount', 0),
        },
        'fieldReceiptStatus': field['status'],
        'frozenCalculationBound': frozen_result is not None,
        'dataIntegrityVerified': field['bytesIntegrityVerified'],
        'independentProgramComparisonIsNotSourceAuthentication': True,
        'engineeringQualified': False, 'formalConstructionIssue': False}




def declared_frozen_result_hash(raw: bytes) -> str | None:
    """Extract the validated nested identity before repository binding at API."""
    contents = _read_archive(raw)
    with ZipFile(BytesIO(contents['field_case.zip'])) as nested:
        case = FieldCase.model_validate(_strict_json(nested.read('case.json'), 'field case.json'))
    return case.result_hash


def _archive_path(root: Path, identity: str) -> Path:
    if not HASH.fullmatch(identity):
        raise ValueError('Invalid acceptance archive identifier')
    return root / 'archives' / (identity + '.zip')


def _record_path(root: Path, identity: str) -> Path:
    if not HASH.fullmatch(identity):
        raise ValueError('Invalid acceptance record identifier')
    return root / 'records' / (identity + '.json')


def _write_once(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('Symlinks are forbidden in acceptance storage')
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError('Immutable acceptance content identity collision')
        return
    fd, temp_path = tempfile.mkstemp(prefix='.acceptance-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError('Immutable acceptance record modified concurrently')
    finally:
        Path(temp_path).unlink(missing_ok=True)


def save_benchmark(raw: bytes, root: Path, reviewer: str, *, frozen_result: dict | None = None) -> dict:
    if not isinstance(reviewer, str) or len(reviewer.strip()) < 2:
        raise ValueError('A signed-in engineering checker identity is required')
    report = evaluate(raw, frozen_result=frozen_result)
    root = Path(root)
    identity = report['benchmarkId']
    _write_once(_archive_path(root, identity), raw)
    path = _record_path(root, identity)
    if path.exists():
        return read_benchmark(identity, root)
    stored = {**report, 'submittedByChecker': reviewer.strip(),
              'receivedAt': datetime.now(timezone.utc).isoformat(),
              'checkerHasNotSignedEngineeringApproval': True}
    stored['recordHash'] = sha256(_canonical(stored)).hexdigest()
    _write_once(path, _canonical(stored))
    return stored


def read_benchmark(identity: str, root: Path) -> dict:
    root = Path(root)
    record = _strict_json(_record_path(root, identity).read_bytes(), 'stored acceptance record')
    digest = record.pop('recordHash', None)
    if sha256(_canonical(record)).hexdigest() != digest or record.get('benchmarkId') != identity:
        raise ValueError('Stored acceptance record integrity failure')
    if sha256(_archive_path(root, identity).read_bytes()).hexdigest() != identity:
        raise ValueError('Stored source evidence archive integrity failure')
    record['recordHash'] = digest
    return record
