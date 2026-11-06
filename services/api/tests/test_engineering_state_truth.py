from __future__ import annotations
import pytest
from app.rules.jgj120_2012.stability_rules import check_wall_deformation
from app.schemas.domain import CalculationResult, Point2D, Polyline2D, PressureProfile, Project, StageCalculationResult, WallInternalForceResult
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.intelligent_design_closure import _assessment


def _fixture():
    excavation = make_excavation_model('truth', Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=20, y=0), Point2D(x=20, y=10), Point2D(x=0, y=10)], closed=True), 0, -10)
    project = Project(name='truth', excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    return project, project.retaining_system.diaphragm_walls[0]


def _assess(project, check):
    return _assessment(project, CalculationResult(projectId=project.id, caseId='truth', checks=[check], checkSummary={'pass': 0, 'warning': 1, 'fail': 0}))


def test_real_displacement_warning_cannot_close_a_quantitative_failure():
    project, wall = _fixture()
    check = check_wall_deformation(wall.id, 10, 100, '一般').model_dump(mode='json', by_alias=True)
    assert check['status'] == 'warning'
    assessment = _assess(project, check)
    assert assessment['quantitativeOpenCount'] == 1
    row = assessment['openRecords'][0]
    assert row['safetyFactor'] == pytest.approx(0.4)
    assert row['utilization'] == pytest.approx(2.5)
    assert row['originalStatus'] == 'warning'
    assert row['status'] == 'fail'
    assert assessment['reviewCount'] == 1
    assert assessment['structuralClosed'] is False
    assert assessment['calculationClosed'] is False


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), float('-inf')])
def test_unknown_displacement_is_not_a_closed_constraint(value):
    project, wall = _fixture()
    check = {'ruleId': 'JGJ120-2012-DEFORMATION-SUBSET', 'objectId': wall.id, 'objectType': 'DiaphragmWallPanel', 'status': 'pass', 'calculatedValue': value, 'limitValue': 40, 'unit': 'mm'}
    assessment = _assess(project, check)
    assert assessment['structuralClosed'] is False
    assert assessment['calculationClosed'] is False
    row = next(r for r in assessment['distribution']['records'] if r['ruleId'] == 'WALL_DISPLACEMENT')
    assert row['safetyFactor'] is None
    assert row['status'] != 'pass'


def test_informational_construction_effect_numbers_are_not_capacity_ratios():
    project, wall = _fixture()
    check = {'ruleId': 'JGJ120-SUPPORT-CONSTRUCTION-EFFECTS-SUBSET', 'objectId': wall.id, 'status': 'warning', 'calculatedValue': 1000, 'limitValue': 300, 'unit': 'kN'}
    assessment = _assess(project, check)
    assert assessment['quantitativeOpenCount'] == 0
    assert assessment['reviewCount'] == 1
    assert assessment['structuralClosed'] is True


def test_zero_displacement_is_a_known_satisfied_maximum_constraint():
    project, wall = _fixture()
    assessment = _assess(project, check_wall_deformation(wall.id, 10, 0, '一般').model_dump(mode='json', by_alias=True))
    assert assessment['structuralClosed'] is True
    assert assessment['calculationClosed'] is True


def test_unknown_stage_quantity_cannot_be_hidden_by_another_stage_warning():
    project, wall = _fixture()
    known = check_wall_deformation(wall.id, 10, 10, '一般').model_dump(mode='json', by_alias=True)
    known.update(status='warning', stageLabel='known')
    missing = dict(known, calculatedValue=None, status='pass', stageLabel='missing')
    result = CalculationResult(projectId=project.id, caseId='truth', checks=[known, missing], checkSummary={'warning': 1, 'fail': 0})
    assessment = _assessment(project, result)
    assert assessment['structuralClosed'] is False
    assert assessment['calculationClosed'] is False


@pytest.mark.parametrize('scope', ['top_level', 'trace'])
def test_explicit_not_applicable_quantity_is_review_information(scope):
    project, wall = _fixture()
    check = {'ruleId': 'JGJ120-2012-DEFORMATION-SUBSET', 'objectId': wall.id, 'objectType': 'DiaphragmWallPanel', 'status': 'manual_review', 'calculatedValue': None, 'limitValue': 40, 'unit': 'mm'}
    if scope == 'top_level':
        check['applicability'] = 'not_required'
    else:
        check['trace'] = {'applicability': 'not_applicable'}
    assessment = _assess(project, check)
    assert assessment['structuralClosed'] is True
    assert assessment['calculationClosed'] is True
    assert assessment['reviewCount'] == 1


def test_quantitative_failure_is_not_suppressed_by_quality_in_rule_name():
    project, wall = _fixture()
    check = check_wall_deformation(wall.id, 10, 100, '一般').model_dump(mode='json', by_alias=True)
    check['ruleId'] = 'QUALITY-WALL-DEFORMATION'
    assessment = _assess(project, check)
    assert assessment['quantitativeOpenCount'] == 1


def test_unknown_quantity_requires_evidence_instead_of_an_automatic_sizing_guess():
    from app.services.intelligent_design_closure import _remaining_options
    project, wall = _fixture()
    check = check_wall_deformation(wall.id, 10, None, '一般').model_dump(mode='json', by_alias=True)
    assessment = _assess(project, check)
    options = _remaining_options(project, assessment)
    assert any(option.get('actionId') == 'restore_quantitative_evidence' for option in options)
    item = next(option for option in options if option.get('actionId') == 'restore_quantitative_evidence')
    assert item['automaticAllowed'] is False
    assert item['currentSafetyFactor'] is None


@pytest.mark.parametrize('proposal_status', ['pass', 'warning'])
def test_missing_supplied_wall_bars_are_not_verified_by_a_passing_proposal(proposal_status):
    from app.calculation.final_checks import refresh_final_stage_checks
    project, wall = _fixture()
    wall.reinforcement = []
    force = WallInternalForceResult(
        segmentId=wall.segment_id, stageId='S1',
        maxMoment=80, maxShear=20, maxDisplacement=10,
        maxMomentDesign=100, maxShearDesign=30,
    )
    stage = StageCalculationResult(
        segmentId=wall.segment_id, stageId='S1', solveId='solved',
        pressureProfile=PressureProfile(points=[]), wallInternalForce=force,
        loadEvidence={'load_id': 'load-1'},
    )
    refresh_final_stage_checks(project, None, [stage], {'S1': {'excavationDepthM': 10, 'assumptions': []}})
    missing = next(c for c in stage.checks if c.get('evidenceScope') == 'supplied_reinforcement')
    assert missing['status'] == 'manual_review'
    assert missing['limitValue'] is None
    proposal = dict(missing, status=proposal_status, limitValue=300, evidenceScope='proposal_only')
    result = CalculationResult(projectId=project.id, caseId='truth', checks=[missing, proposal], checkSummary={'pass': 1, 'fail': 0, 'manualReview': 1})
    assessment = _assessment(project, result)
    assert assessment['quantitativeUnknownCount'] == 1
    assert assessment['structuralClosed'] is False
    assert assessment['calculationClosed'] is False


def test_unsupported_soil_resistance_model_is_a_structural_block():
    project, wall = _fixture()
    assessment = _assess(project, {'ruleId': 'PITGUARD-SOIL-RESISTANCE-MODEL', 'objectId': project.id, 'objectType': 'SoilModel', 'status': 'fail', 'calculatedValue': None, 'limitValue': None, 'message': 'No validated soil constitutive response.'})
    assert assessment['structuralClosed'] is False
    assert assessment['calculationClosed'] is False


def test_docx_conclusion_never_promotes_manual_review_to_design_readiness():
    from app.reports.docx_report import _governing_conclusion
    project, wall = _fixture()
    missing = {'ruleId': 'GB50010-FLEXURE-SUBSET', 'objectId': wall.id, 'status': 'manual_review', 'evidenceScope': 'supplied_reinforcement', 'message': 'Missing supplied bars.'}
    project.calculation_results = [CalculationResult(projectId=project.id, caseId='truth', checks=[missing], checkSummary={'pass': 0, 'fail': 0, 'warning': 0, 'manualReview': 1})]
    conclusion = _governing_conclusion(project, [missing])
    assert '人工复核' in conclusion
    assert '可进入配筋' not in conclusion


@pytest.mark.parametrize("fail_count", [0, 1])
def test_docx_conclusion_marks_verify_proposals_unadopted(fail_count):
    from app.reports.docx_report import _governing_conclusion
    project, wall = _fixture()
    proposal = {'ruleId': 'GB50010-FLEXURE-SUBSET', 'objectId': wall.id, 'status': 'pass', 'evidenceScope': 'proposal_only'}
    project.calculation_results = [CalculationResult(projectId=project.id, caseId='truth', calculationMode='verify', derivedDesign={'status': 'proposal_only', 'adopted': False}, checks=[proposal], checkSummary={'pass': 1, 'fail': fail_count, 'warning': 0})]
    conclusion = _governing_conclusion(project, [proposal])
    assert '未采用' in conclusion
    assert '可进入配筋' not in conclusion
