"""M3 physical benchmark evidence bundle: byte-verified, identity-bound input.

The contract authenticates bytes relative to an explicitly supplied archive. It
cannot authenticate a design firm, soil model, survey, or professional signature.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal
from zipfile import ZipFile, BadZipFile
import json

from pydantic import Field, model_validator

from app.engineering.single_braced_pile.assurance.model import Strict
from app.services.m3_spatial_frame import M3FrameInput

MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_INFLATED_BYTES = 16 * 1024 * 1024
HEX = r'^[a-f0-9]{64}$'


class ProfilePoint(Strict):
    z_m: float = Field(ge=-200, le=100)
    pressure_kpa: float = Field(ge=-5000, le=5000)


class WallPressureProfile(Strict):
    pressure_basis: Literal['external_net_traction_excluding_winkler_reaction']
    face_index: int = Field(ge=0, le=127)
    samples: tuple[ProfilePoint, ...] = Field(min_length=2, max_length=64)
    source_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=12,max_length=350)

    @model_validator(mode='after')
    def sorted_depths(self):
        zz = [x.z_m for x in self.samples]
        if any(a <= b for a, b in zip(zz, zz[1:])):
            raise ValueError('Pressure z coordinates must decrease strictly from crown to toe')
        return self


class WallSoilContact(Strict):
    face_index: int = Field(ge=0, le=127)
    side: Literal['outside', 'inside']
    z_top_m: float = Field(ge=-200, le=100)
    z_bottom_m: float = Field(ge=-200, le=100)
    stiffness_kn_m3: float = Field(gt=0, le=1e9)
    gap_mm: float = Field(default=0.0, ge=0, le=10000)
    source_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=12,max_length=350)

    @model_validator(mode='after')
    def bounds(self):
        if self.z_top_m <= self.z_bottom_m:
            raise ValueError('Soil contact requires z_top > z_bottom')
        return self


class PhysicalStage(Strict):
    stage_id: str = Field(min_length=2, max_length=80)
    profiles: tuple[WallPressureProfile, ...] = Field(min_length=4, max_length=128)
    contacts: tuple[WallSoilContact, ...] = Field(default=(), max_length=256)


class M3PhysicalBoundary(Strict):
    model_kind: Literal['unilateral_winkler_with_distributed_net_pressure_reference']
    max_vertical_mesh_m: float = Field(default=3.0, ge=0.5, le=8)
    stages: tuple[PhysicalStage, ...] = Field(min_length=1, max_length=12)


class M3PhysicalStudy(Strict):
    schema_version: Literal['pitguard.m3-physical-study/1'] = Field(alias='schemaVersion')
    frame: M3FrameInput
    boundary: M3PhysicalBoundary

    @model_validator(mode='after')
    def physical_valid(self):
        base = self.frame
        stage_by_id = {s.id: s for s in base.stages}
        original_faces = set(range(len(base.topology.excavation.vertices)))
        if len(self.boundary.stages) != len(stage_by_id):
            raise ValueError('Physical study stages must match frozen frame stages')
        if {s.stage_id for s in self.boundary.stages} != set(stage_by_id):
            raise ValueError('Physical and frozen stage identities mismatch')
        for stage in base.stages:
            if any(p.net_pressure_kpa != 0 for p in stage.face_tractions):
                raise ValueError('Physical-study frame legacy face tractions must be zero; use profile loads only')
            if stage.lateral_soil_spring_kn_per_m2:
                raise ValueError('Physical study disallows legacy two-sided wale springs')
        for stage in self.boundary.stages:
            profiles = stage.profiles
            if {p.face_index for p in profiles} != original_faces or len(profiles) != len(original_faces):
                raise ValueError('One depth pressure profile is mandatory for every original face')
            for profile in profiles:
                if abs(profile.samples[0].z_m - base.crown_z_m) > 1e-7 or abs(profile.samples[-1].z_m - base.toe_z_m) > 1e-7:
                    raise ValueError('Pressure profile must cover full crown-to-toe depth')
            seen = set()
            excavation_bottom_z = base.crown_z_m - stage_by_id[stage.stage_id].excavation_depth_m
            for contact in stage.contacts:
                key = (contact.face_index, contact.side)
                if key in seen:
                    raise ValueError('Duplicate soil contact for face and side')
                seen.add(key)
                if contact.face_index not in original_faces:
                    raise ValueError('Soil contact references unknown face')
                if contact.z_top_m > base.crown_z_m + 1e-7 or contact.z_bottom_m < base.toe_z_m - 1e-7:
                    raise ValueError('Soil contact is outside wall extent')
                if contact.side == 'inside' and contact.z_top_m > excavation_bottom_z + 1e-7:
                    raise ValueError('Inside soil spring may not extend above the excavated bottom')
        return self


def source_record(study: M3PhysicalStudy, stage: PhysicalStage, item: WallPressureProfile | WallSoilContact) -> dict:
    common = {'schemaVersion': 'pitguard.m3-physical-source/1',
              'projectId': study.frame.topology.project_id,
              'stageId': stage.stage_id,
              'faceIndex': item.face_index,
              'coordinateDatum': 'project_local_z_m',
              'evidenceLevel': study.frame.evidence_level}
    common['methodReference'] = item.method_reference
    if isinstance(item, WallPressureProfile):
        return {**common, 'kind': 'net_pressure_profile',
                'pressureBasis': item.pressure_basis, 'pressureUnit': 'kPa',
                'samples': [{'zM': p.z_m, 'pressureKpa': p.pressure_kpa} for p in item.samples]}
    return {**common, 'kind': 'unilateral_soil_contact', 'side': item.side,
            'zTopM': item.z_top_m, 'zBottomM': item.z_bottom_m,
            'stiffnessKnM3': item.stiffness_kn_m3, 'gapMm': item.gap_mm}


def _read_json(raw: bytes) -> object:
    def reject_pairs(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise ValueError('Duplicate JSON field: ' + key)
            record[key] = value
        return record
    def reject_constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=reject_pairs, parse_constant=reject_constant)


def read_physical_bundle(raw: bytes) -> tuple[M3PhysicalStudy, dict]:
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError('M3 physical-study ZIP exceeds raw byte budget')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            files = {}
            names = set()
            if len(archive.infolist()) > 200:
                raise ValueError('Too many M3 source files')
            total = 0
            for info in archive.infolist():
                name = info.filename
                parts = PurePosixPath(name).parts
                if (name in names or name != str(PurePosixPath(name)) or name.startswith('/') or '\\' in name or
                    any(x in ('', '.', '..') for x in parts) or info.is_dir() or
                    info.external_attr >> 16 & 0o170000 == 0o120000):
                    raise ValueError('Unsafe or duplicate M3 source ZIP path')
                if not (name == 'study.json' or
                        (len(parts) == 2 and parts[0] == 'sources' and
                         parts[1].endswith('.json') and len(parts[1]) == 69)):
                    raise ValueError('Unexpected file in M3 evidence bundle: ' + name)
                names.add(name)
                total += info.file_size
                if total > MAX_INFLATED_BYTES or info.file_size > MAX_BUNDLE_BYTES:
                    raise ValueError('M3 source ZIP exceeds inflated byte budget')
                if info.compress_size and info.file_size / info.compress_size > 200:
                    raise ValueError('Unreasonable M3 ZIP compression ratio')
                files[name] = archive.read(info)
    except BadZipFile as exc:
        raise ValueError('Corrupted M3 evidence ZIP') from exc
    if 'study.json' not in files:
        raise ValueError('M3 physical-study bundle must contain study.json')
    study = M3PhysicalStudy.model_validate(_read_json(files['study.json']))
    expected = {'study.json'}
    verified = []
    for stage in study.boundary.stages:
        for item in (*stage.profiles, *stage.contacts):
            path = 'sources/' + item.source_sha256 + '.json'
            expected.add(path)
            source = files.get(path)
            if source is None or sha256(source).hexdigest() != item.source_sha256:
                raise ValueError('M3 raw source bytes missing or SHA mismatch: ' + path)
            if _read_json(source) != source_record(study, stage, item):
                raise ValueError('M3 raw source content differs from project/stage/face physical input')
            verified.append({'stageId': stage.stage_id, 'faceIndex': item.face_index,
                             'sourceSha256': item.source_sha256, 'kind': 'pressure' if isinstance(item, WallPressureProfile) else 'soil_contact'})
    if set(files) != expected:
        raise ValueError('Unreferenced or duplicate-identity M3 evidence sources in archive')
    return study, {'schemaVersion': 'pitguard.m3-physical-evidence/1',
                   'archiveSha256': sha256(raw).hexdigest(),
                   'studySha256': sha256(files['study.json']).hexdigest(),
                   'sourceCount': len(expected)-1,
                   'verifiedSources': verified,
                   'status': 'byte_integrity_and_input_identity_verified_only',
                   'sourceAuthenticityReviewed': False,
                   'engineeringQualified': False, 'formalConstructionIssue': False}
