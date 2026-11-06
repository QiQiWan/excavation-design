"""P1 field observation reconciliation and scoped Thiem pumping-test interpretation.

The numerical observations in this module are *source declarations*. No borehole,
instrument calibration or original file is authenticated by this API.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import log, pi
from typing import Literal
from pydantic import Field, model_validator
from .model import Strict


class WaterLevelObservation(Strict):
    project_id: str = Field(min_length=1, max_length=100)
    section_id: str = Field(min_length=1, max_length=100)
    stage_id: str = Field(min_length=1, max_length=80)
    well_id: str = Field(min_length=1, max_length=120)
    side: Literal['inside', 'outside']
    observed_at: datetime
    head_m: float = Field(ge=-1000, le=10000)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_row: int = Field(ge=1)
    quality: Literal['valid', 'excluded'] = 'valid'

    @model_validator(mode='after')
    def aware_time(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError('Observation time must contain a timezone offset')
        return self


class ObservationReviewInput(Strict):
    project_id: str = Field(min_length=1, max_length=100)
    section_id: str = Field(min_length=1, max_length=100)
    observations: tuple[WaterLevelObservation, ...] = Field(min_length=2, max_length=2000)
    inside_well_id: str | None = None
    outside_well_id: str | None = None
    max_pair_gap_minutes: float = Field(default=30.0, gt=0, le=1440)

    @model_validator(mode='after')
    def valid_identity(self):
        seen = set()
        per_stage_side = {}
        for record in self.observations:
            if (record.project_id, record.section_id) != (self.project_id, self.section_id):
                raise ValueError('Field observation belongs to a different project/section')
            key = (record.stage_id, record.side, record.well_id, record.observed_at.astimezone(timezone.utc))
            if key in seen:
                raise ValueError('Duplicate observation for a stage, well and timestamp')
            seen.add(key)
            cell=(record.stage_id,record.side)
            per_stage_side[cell]=per_stage_side.get(cell,0)+1
            if per_stage_side[cell]>400:
                raise ValueError('At most 400 observations per stage and side are supported in synchronous matching')
        return self


def _pair_optimal(inside: list[WaterLevelObservation], outside: list[WaterLevelObservation], max_seconds: float):
    """Maximize non-reused pairs, then minimize total timestamp separation.

    Monotone dynamic programming avoids independently pairing each outside
    reading to the same convenient inside reading. Ties are deterministic.
    """
    a = sorted(inside, key=lambda x: (x.observed_at, x.source_row))
    b = sorted(outside, key=lambda x: (x.observed_at, x.source_row))
    m, n = len(a), len(b)
    # At most 1000 observations per side in the admission contract.
    # Storing path decisions instead of full pair lists bounds memory.
    count = [[0] * (n + 1) for _ in range(m + 1)]
    cost = [[0.0] * (n + 1) for _ in range(m + 1)]
    choice = [[''] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            options = [(count[i + 1][j], cost[i + 1][j], 'skip_inside'),
                       (count[i][j + 1], cost[i][j + 1], 'skip_outside')]
            delta = abs((a[i].observed_at - b[j].observed_at).total_seconds())
            if delta <= max_seconds:
                options.append((1 + count[i + 1][j + 1], delta + cost[i + 1][j + 1], 'pair'))
            best = min(options, key=lambda x: (-x[0], x[1], {'pair':0,'skip_inside':1,'skip_outside':2}[x[2]]))
            count[i][j], cost[i][j], choice[i][j] = best
    matched = []
    i = j = 0
    while i < m and j < n:
        pick = choice[i][j]
        if pick == 'pair':
            matched.append((a[i], b[j]))
            i += 1; j += 1
        elif pick == 'skip_inside':
            i += 1
        else:
            j += 1
    return matched


def reconcile_observations(request: ObservationReviewInput) -> dict:
    active = [r for r in request.observations if r.quality == 'valid']
    stages = sorted({r.stage_id for r in request.observations})
    rows = []
    unpaired_total = 0
    for stage in stages:
        records = [r for r in active if r.stage_id == stage]
        inside_ids = {r.well_id for r in records if r.side == 'inside'}
        outside_ids = {r.well_id for r in records if r.side == 'outside'}
        ambiguous = ((len(inside_ids) > 1 and not request.inside_well_id)
                     or (len(outside_ids) > 1 and not request.outside_well_id))
        if ambiguous:
            rows.append({'stageId': stage, 'status': 'well_selection_required',
                         'availableInsideWells': sorted(inside_ids),
                         'availableOutsideWells': sorted(outside_ids), 'pairedCount': 0,
                         'maxObservedPairedDeltaHeadM': None})
            continue
        chosen_inside = request.inside_well_id or next(iter(inside_ids), None)
        chosen_outside = request.outside_well_id or next(iter(outside_ids), None)
        ins = [r for r in records if r.side == 'inside' and r.well_id == chosen_inside]
        out = [r for r in records if r.side == 'outside' and r.well_id == chosen_outside]
        pairs = _pair_optimal(ins, out, request.max_pair_gap_minutes * 60)
        unpaired_total += len(ins) + len(out) - 2 * len(pairs)
        detail = [{'insideObservedAt':a.observed_at.isoformat(),
                   'outsideObservedAt':b.observed_at.isoformat(),
                   'timeDifferenceSeconds':abs((a.observed_at-b.observed_at).total_seconds()),
                   'insideHeadM':a.head_m,'outsideHeadM':b.head_m,
                   'outsideMinusInsideHeadM':b.head_m-a.head_m,
                   'insideSourceSha256':a.source_sha256,'outsideSourceSha256':b.source_sha256,
                   'insideSourceRow':a.source_row,'outsideSourceRow':b.source_row}
                  for a,b in pairs]
        rows.append({'stageId':stage,'status':'paired_observations_only' if pairs else 'no_time_compatible_pair',
                     'insideWellId':chosen_inside,'outsideWellId':chosen_outside,
                     'pairedCount':len(detail),'unpairedCount':len(ins)+len(out)-2*len(pairs),
                     'maxObservedPairedDeltaHeadM':max((p['outsideMinusInsideHeadM'] for p in detail),default=None),
                     'maxObservedAbsoluteDeltaHeadM':max((abs(p['outsideMinusInsideHeadM']) for p in detail),default=None),
                     'pairs':detail})
    maxima = [x['maxObservedPairedDeltaHeadM'] for x in rows if x.get('maxObservedPairedDeltaHeadM') is not None]
    return {'schemaVersion':'pitguard.field-hydrology-observations/1',
            'projectId':request.project_id,'sectionId':request.section_id,
            'assessmentStatus':'well_selection_required' if any(x['status']=='well_selection_required' for x in rows)
                else 'observations_paired_site_review_required' if maxima else 'insufficient_synchronous_observations',
            'stages':rows,'pairedCount':sum(r['pairedCount'] for r in rows),
            'unpairedSelectedCount':unpaired_total,
            'excludedObservationCount':len(request.observations)-len(active),
            'maxObservedPairedDeltaHeadM':max(maxima,default=None),
            'designAdverseHeadEstablished':False,'originalSourceBytesIndependentlyAuthenticated':False,
            'formalDesignReady':False,
            'limitations':['Reported heads are client-declared observations; instrument, well mapping and source bytes require independent verification',
                           'Paired maximum within the supplied dates is not a design worst-case head or an uplift/piping safety factor']}


class PumpingTestInterpretationInput(Strict):
    project_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    test_id: str = Field(min_length=1)
    discharge_m3_s: float = Field(gt=0, le=20)
    near_radius_m: float = Field(gt=0)
    far_radius_m: float = Field(gt=0)
    near_drawdown_m: float = Field(ge=0)
    far_drawdown_m: float = Field(ge=0)
    confined_aquifer_thickness_m: float = Field(gt=0, le=500)
    confined: bool
    steady_state: bool
    homogeneous_isotropic: bool
    fully_penetrating_well: bool
    well_loss_negligible: bool
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def physical_geometry(self):
        if self.far_radius_m <= self.near_radius_m:
            raise ValueError('Far observation radius must exceed near radius')
        return self


def interpret_thiem(request: PumpingTestInterpretationInput) -> dict:
    required = ('confined','steady_state','homogeneous_isotropic',
                'fully_penetrating_well','well_loss_negligible')
    failures = [x for x in required if not getattr(request,x)]
    if request.near_drawdown_m <= request.far_drawdown_m:
        failures.append('near_drawdown_exceeds_far_drawdown')
    result = {'schemaVersion':'pitguard.pumping-test-thiem/1',
              'projectId':request.project_id,'sectionId':request.section_id,
              'testId':request.test_id,'methodId':'confined_steady_thiem_two_observation_wells',
              'methodAssumptionsSatisfied':not failures,'outsideScopeReasons':failures,
              'sourceSha256Declared':request.source_sha256,
              'originalSourceBytesIndependentlyAuthenticated':False,
              'designAdverseHeadEstablished':False,'verticalHydraulicConductivityIdentified':False,
              'formalDesignReady':False}
    if failures:
        return {**result,'assessmentStatus':'outside_Thiem_method_scope',
                'transmissivityM2PerS':None,'hydraulicConductivityMPerS':None}
    T = (request.discharge_m3_s*log(request.far_radius_m/request.near_radius_m)
         / (2*pi*(request.near_drawdown_m-request.far_drawdown_m)))
    return {**result,'assessmentStatus':'candidate_parameter_requires_field_review',
            'transmissivityM2PerS':T,
            'hydraulicConductivityMPerS':T/request.confined_aquifer_thickness_m,
            'note':'Quasi-steady confined-aquifer radial interpretation; does not infer storage, aquifer boundaries, vertical K or design water head'}
