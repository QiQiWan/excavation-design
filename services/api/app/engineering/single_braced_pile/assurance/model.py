"""Immutable, strict and traceable evidence contracts.

A reviewer assertion and a SHA string do not prove that document bytes have
been independently checked. Verification level is explicitly conservative.
"""
from __future__ import annotations
from datetime import date
from math import isclose
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)


class Evidence(Strict):
    key: str=Field(min_length=3,max_length=130,pattern=r'^[a-zA-Z][a-zA-Z0-9_.-]+$')
    value: float | str | bool | None=None
    unit: str=Field(default='-',min_length=1,max_length=45)
    source_kind: Literal['investigation','field_monitoring','laboratory','pumping_test',
        'survey','construction_record','design_record','native_numeric_export',
        'independent_calculation','standard','other']
    source_sha256: str | None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    source_locator: str=Field(min_length=3,max_length=400)
    revision: str=Field(min_length=1,max_length=80)
    state: Literal['candidate','confirmed','conflict','missing']='candidate'
    reviewed_by: str | None=None
    reviewed_on: date | None=None
    @model_validator(mode='after')
    def validate_confirmation(self):
        if self.state=='confirmed' and (not self.source_sha256 or not self.reviewed_by or
            self.reviewed_on is None or self.value is None):
            raise ValueError('Confirmed evidence requires source digest, reviewer, review date and value')
        return self


class EvidenceBundle(Strict):
    project_id: str=Field(min_length=1,max_length=100)
    section_id: str=Field(min_length=1,max_length=100)
    reference_version: str=Field(min_length=1,max_length=60)
    records: tuple[Evidence,...]=()
    @model_validator(mode='after')
    def unique(self):
        keys=[x.key for x in self.records]
        if len(keys)!=len(set(keys)):
            raise ValueError('Evidence keys must be unique; conflicts require a separate review process')
        return self

    def index(self):
        return {x.key:x for x in self.records}


def audit_required(bundle: EvidenceBundle, required: dict[str,tuple[object,str]], *,
                   numerical_tolerance: float=1e-8)->dict:
    """Compare CONFIRMED source values to the actual frozen calculation input.

    Source digests are metadata assertions at this level, not proof of file bytes.
    This function does not and cannot certify the project source itself.
    """
    index=bundle.index();items=[]
    for key,(expected,unit) in required.items():
        ev=index.get(key)
        if ev is None:
            status='missing_evidence';reason='No source record bound to this calculation field'
        elif ev.state!='confirmed':
            status='not_confirmed';reason=f'Evidence state: {ev.state}'
        elif ev.unit!=unit:
            status='unit_mismatch';reason=f'Expected {unit}, received {ev.unit}'
        elif type(ev.value) is bool or type(expected) is bool:
            status='matched' if ev.value is expected else 'value_mismatch';reason='Boolean physical condition'
        elif isinstance(expected,(int,float)) and isinstance(ev.value,(int,float)):
            status=('matched' if isclose(float(ev.value),float(expected),rel_tol=numerical_tolerance,
                                         abs_tol=numerical_tolerance) else 'value_mismatch')
            reason='Numerical engineering input matched to a reviewed source record'
        else:
            status='matched' if ev.value==expected else 'value_mismatch'
            reason='Categorical engineering input comparison'
        items.append({'key':key,'status':status,'expected':expected,'unit':unit,
          'sourceSha256':ev.source_sha256 if ev else None,
          'sourceLocator':ev.source_locator if ev else None,'reason':reason})
    return {'schemaVersion':'pitguard.evidence-coverage/1',
            'status':'metadata_confirmed' if all(x['status']=='matched' for x in items) else 'incomplete',
            'requiredCount':len(items),'matchedCount':sum(x['status']=='matched' for x in items),
            'items':items,'fileBytesIndependentlyVerified':False,
            'limitations':['SHA256 strings and reviewer assertions are recorded but original file bytes are not verified by this API']}
