"""P2 independent *reported* crack calculations and limited Euler screening.

A numerical agreement between two third-party reports is not normative
validation of the chosen circular-section crack methodology.
"""
from __future__ import annotations
from math import isclose
from ..normative.second_order_screen import euler_ideal_column_critical_kn
from pydantic import Field, model_validator
from .model import Strict


class CrackReport(Strict):
    reviewer_id: str = Field(min_length=2, max_length=120)
    method_id: str = Field(min_length=5, max_length=180)
    method_scope: str = Field(min_length=12, max_length=800)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    crack_width_mm: float = Field(ge=0, le=20)


class CrackCrosscheckInput(Strict):
    result_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    snapshot_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    combination_name: str = Field(min_length=2, max_length=160)
    stage_id: str = Field(min_length=1, max_length=100)
    depth_m: float = Field(ge=0)
    side: str = Field(pattern=r'^(above|below)$')
    crack_limit_mm: float = Field(gt=0, le=2)
    absolute_tolerance_mm: float = Field(ge=0, le=0.5)
    relative_tolerance: float = Field(ge=0, le=1)
    first: CrackReport
    second: CrackReport

    @model_validator(mode='after')
    def two_independent_reports(self):
        if self.first.reviewer_id == self.second.reviewer_id:
            raise ValueError('Independent crack reports require distinct reviewers')
        if self.first.source_sha256 == self.second.source_sha256:
            raise ValueError('Independent crack reports require distinct source files')
        return self


def crosscheck_crack_reports(request:CrackCrosscheckInput, result:dict) -> dict:
    if (request.result_hash != result.get('resultHash') or
            request.snapshot_hash != result.get('snapshotHash')):
        raise ValueError('Crack report does not match the immutable calculation result')
    checks = (result.get('normativeCoverage') or {}).get('requiredChecks') or {}
    crack = (checks.get('concrete_circular_crack_control') or {}).get('result') or {}
    cases = crack.get('cases') or []
    matches = [r for r in cases if r.get('stageId') == request.stage_id and
               r.get('combinationName') == request.combination_name and
               r.get('side') == request.side and
               isinstance(r.get('depthM'),(int,float)) and
               isclose(r['depthM'],request.depth_m,rel_tol=0,abs_tol=1e-7)]
    if not matches:
        raise ValueError('Report combination, stage, depth and side must match one computed SLS station')
    frozen_limit = ((result.get('snapshot') or {}).get('crack_control') or {}).get('width_limit_mm')
    if not isinstance(frozen_limit,(int,float)) or not isclose(
            frozen_limit,request.crack_limit_mm,rel_tol=0,abs_tol=1e-9):
        raise ValueError('Report crack limit differs from frozen project SLS setting')
    a,b = request.first.crack_width_mm,request.second.crack_width_mm
    absolute = abs(a-b)
    relative = absolute / max(a,b,1e-6)
    overlimit = a > request.crack_limit_mm or b > request.crack_limit_mm
    # Both tolerances must be satisfied; when either is zero the comparison is exact.
    agreed = (absolute <= request.absolute_tolerance_mm + 1e-12 and
              relative <= request.relative_tolerance + 1e-12)
    state = ('exceeds_declared_limit' if overlimit else
             'numerical_disagreement' if not agreed else
             'numerically_agreed_method_unqualified')
    return {'schemaVersion':'pitguard.circular-crack-independent-crosscheck/1',
            'resultHash':request.result_hash,'snapshotHash':request.snapshot_hash,
            'SLSStation':{'combinationName':request.combination_name,'stageId':request.stage_id,
                          'depthM':request.depth_m,'side':request.side},
            'assessmentStatus':state,'calculationFileHashes':[request.first.source_sha256,request.second.source_sha256],
            'reviewerIds':[request.first.reviewer_id,request.second.reviewer_id],
            'methods':[request.first.method_id,request.second.method_id],
            'crackWidthMm':[a,b], 'absoluteDifferenceMm':absolute,'relativeDifference':relative,
            'declaredAbsoluteToleranceMm':request.absolute_tolerance_mm,
            'declaredRelativeTolerance':request.relative_tolerance,
            'declaredCrackLimitMm':request.crack_limit_mm,'allReportedWidthsWithinLimit':not overlimit,
            'sourceBytesIndependentlyAuthenticated':False,
            'circularCrackMethodNormativelyQualified':False,
            'formalDesignReady':False,
            'requiredNextActions':[' independently qualify circular-section crack-width method and SLS action combination',
                                   'verify original report bytes and calculations with independent engineer']}


class EulerSecondOrderScreenInput(Strict):
    project_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    axial_compression_kn: float = Field(ge=0)
    flexural_rigidity_kn_m2: float = Field(gt=0)
    effective_length_m: float = Field(gt=0, le=200)
    effective_length_basis: str = Field(min_length=12, max_length=600)
    governing_stage_id: str = Field(min_length=1)


def screen_euler_second_order(request: EulerSecondOrderScreenInput) -> dict:
    critical = euler_ideal_column_critical_kn(
        flexural_rigidity_kn_m2=request.flexural_rigidity_kn_m2,
        effective_length_m=request.effective_length_m)
    utilization = request.axial_compression_kn/critical
    return {'schemaVersion':'pitguard.pile-euler-reference-screen/1',
            'projectId':request.project_id,'sectionId':request.section_id,
            'governingStageId':request.governing_stage_id,
            'criticalEulerCompressionKn':critical,'compressionOverEulerCritical':utilization,
            'assessmentStatus':'at_or_above_euler_instability' if utilization >= 1 else
              'theoretical_euler_below_critical_scope_limited',
            'soilSpringAndConstructionStagesAccounted':False,
            'boundaryAssumptionVerified':False,
            'normativeSecondOrderDesignComplete':False,'formalDesignReady':False,
            'note':'Euler column reference with declared effective length; actual soil-pile interaction and second-order analysis require a qualified structural model'}
