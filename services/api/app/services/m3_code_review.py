"""Independent code-method pressure cross-check for M3 physical studies.

JGJ 120-2012 section 3.4 motivates the active-pressure screen. A single
Rankine effective-stress branch is deliberately NOT the complete JGJ method:
water, geometry, unloading, pressure adjustments and site parameters need a
qualified engineering calculation. No numerical match qualifies construction.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from math import pi, sin, sqrt
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile
import json

from pydantic import Field, model_validator
from app.engineering.single_braced_pile.assurance.model import Strict
from .m3_physical_evidence import M3PhysicalStudy
from .m3_physical_solver import _interp

HEX = r'^[0-9a-f]{64}$'
MAX_ZIP = 6_000_000


class SoilLayer(Strict):
    top_depth_m: float = Field(ge=0, le=100)
    bottom_depth_m: float = Field(gt=0, le=200)
    gamma_dry_kn_m3: float = Field(gt=0, le=40)
    gamma_sat_kn_m3: float = Field(gt=0, le=45)
    cohesion_effective_kpa: float = Field(ge=0, le=1000)
    friction_angle_deg: float = Field(ge=0, lt=60)
    source_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=12,max_length=350)


class NormativeFaceCase(Strict):
    stage_id: str = Field(min_length=2,max_length=80)
    face_index: int = Field(ge=0, le=127)
    surcharge_kpa: float = Field(ge=0,le=2000)
    outside_water_depth_m: float = Field(ge=0, le=200)
    inside_water_depth_m: float = Field(ge=0, le=200)
    layers: tuple[SoilLayer,...] = Field(min_length=1,max_length=35)

    @model_validator(mode='after')
    def continuous_strata(self):
        previous=0.0
        for layer in self.layers:
            if abs(layer.top_depth_m-previous)>1e-6 or layer.bottom_depth_m<=layer.top_depth_m:
                raise ValueError('Soil intervals must be positive and continuous from crown')
            previous=layer.bottom_depth_m
        return self


class SupportSpringCase(Strict):
    stage_id: str = Field(min_length=2,max_length=80)
    member_id: str = Field(min_length=1,max_length=200)
    horizontal_support_spacing_m: float = Field(gt=.2,le=50)
    lambda_fixed_point: float = Field(ge=.5,le=1)
    relaxation_alpha: float = Field(ge=.8,le=1)
    source_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=12,max_length=350)


class NormativeDocument(Strict):
    schema_version: str = Field(alias='schemaVersion')
    project_id: str = Field(min_length=1,max_length=100)
    source_archive_sha256: str = Field(pattern=HEX)
    candidate_result_sha256: str = Field(pattern=HEX)
    revision: str = Field(min_length=1,max_length=80)
    gamma_water_kn_m3: float = Field(default=9.81,gt=9,lt=11)
    allowable_absolute_difference_kpa: float = Field(default=10.0,gt=0,le=500)
    face_cases: tuple[NormativeFaceCase,...] = Field(min_length=1,max_length=128)
    pile_diameter_m: float | None = Field(default=None,gt=.2,le=3)
    pile_geometry_source_sha256: str | None = Field(default=None,pattern=HEX)
    support_cases: tuple[SupportSpringCase,...] = Field(default=(),max_length=200)
    @model_validator(mode='after')
    def version(self):
        if self.schema_version!='pitguard.m3-normative-pressure/3.89.3':
            raise ValueError('Unsupported M3 normative pressure document version')
        ids=[(r.stage_id,r.face_index) for r in self.face_cases]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate stage and face normative case')
        if (self.pile_diameter_m is None)!=(self.pile_geometry_source_sha256 is None):
            raise ValueError('Pile diameter and independently attached geometry source must be paired')
        skeys=[(r.stage_id,r.member_id) for r in self.support_cases]
        if len(skeys)!=len(set(skeys)):
            raise ValueError('Duplicate stage/member support stiffness reference')
        return self


def _no_duplicate_json(raw:bytes):
    def pairs(values):
        out={}
        for k,v in values:
            if k in out:raise ValueError('Duplicate JSON key: '+k)
            out[k]=v
        return out
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
        parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite JSON value: '+v)))


def read_review_archive(raw:bytes,entry:str='review.json')->tuple[dict,dict]:
    """Bounded ZIP; bytes verified against declared sources/<sha>.bin paths."""
    if len(raw)>MAX_ZIP:raise ValueError('Review evidence ZIP exceeds byte budget')
    try:
        with ZipFile(BytesIO(raw)) as z:
            names=set();data={};size=0
            if len(z.infolist())>125:raise ValueError('Too many review evidence files')
            for info in z.infolist():
                name=info.filename;parts=PurePosixPath(name).parts
                if (name in names or info.is_dir() or name.startswith('/') or '\\' in name or
                    name!=str(PurePosixPath(name)) or any(s in ('.','..','') for s in parts) or
                    (info.external_attr>>16)&0o170000==0o120000):
                    raise ValueError('Unsafe/duplicate review archive path')
                if not (name==entry or (len(parts)==2 and parts[0]=='sources' and
                     len(parts[1])==68 and parts[1].endswith('.bin'))):
                    raise ValueError('Unexpected review evidence file '+name)
                size+=info.file_size
                if size>15_000_000 or info.file_size>5_000_000 or (info.compress_size and info.file_size/info.compress_size>500):
                    raise ValueError('Review archive decompressed size/ratio limit exceeded')
                names.add(name);data[name]=z.read(info)
    except BadZipFile as exc:raise ValueError('Invalid review evidence ZIP') from exc
    if entry not in data:raise ValueError('Missing '+entry)
    doc=_no_duplicate_json(data[entry]);files={}
    for name,body in data.items():
        if name!=entry:
            digest=name.removeprefix('sources/').removesuffix('.bin')
            if sha256(body).hexdigest()!=digest:raise ValueError('Review source bytes SHA-256 mismatch')
            files[digest]=body
    return doc,{'archiveSha256':sha256(raw).hexdigest(),'sourceDigests':files,
                 'sourceBytesVerified':True,'sourceAuthorshipAuthenticated':False}


def rankine_screen(face:NormativeFaceCase,depth:float,water_gamma:float,*,
                   stratum_index:int|None=None)->dict:
    """Independent effective vertical stress + Rankine active screen only.

    Effective soil pressure excludes passive resistance (Winkler contact in
    the main model), and net pore pressure outside minus inside is included.
    """
    if depth<0 or depth>face.layers[-1].bottom_depth_m+1e-7:
        raise ValueError('Depth outside reviewed soil strata')
    effective=0.0
    for layer in face.layers:
        end=min(depth,layer.bottom_depth_m)
        start=layer.top_depth_m
        if end<=start:continue
        water=face.outside_water_depth_m
        above=max(0.0,min(end,water)-start)
        below=(end-start)-above
        effective+=above*layer.gamma_dry_kn_m3+below*(layer.gamma_sat_kn_m3-water_gamma)
    layer=(face.layers[stratum_index] if stratum_index is not None else
           next((s for s in face.layers if depth<=s.bottom_depth_m+1e-8),face.layers[-1]))
    sine=sin(layer.friction_angle_deg*pi/180)
    ka=(1-sine)/(1+sine)
    soil=max(0.0,ka*(effective+face.surcharge_kpa)-2*layer.cohesion_effective_kpa*sqrt(ka))
    uo=water_gamma*max(0.0,depth-face.outside_water_depth_m)
    ui=water_gamma*max(0.0,depth-face.inside_water_depth_m)
    return {'depthM':round(depth,6),'effectiveVerticalKpa':round(effective,7),
            'ka':round(ka,8),'activeEffectiveKpa':round(soil,7),
            'netWaterKpa':round(uo-ui,7),'netReferenceKpa':round(soil+uo-ui,7)}


def compare_normative_pressure(study:M3PhysicalStudy,result:dict,archive:bytes,
                               expected_result_sha256:str,expected_archive_sha256:str)->dict:
    document,src=read_review_archive(archive)
    contract=NormativeDocument.model_validate(document)
    if (contract.project_id!=study.frame.topology.project_id or
        contract.source_archive_sha256!=expected_archive_sha256 or
        contract.candidate_result_sha256!=expected_result_sha256):
        raise ValueError('Review project or frozen snapshot identity mismatch')
    stages={s.stage_id:s for s in study.boundary.stages};rows=[]
    for face in contract.face_cases:
        stage=stages.get(face.stage_id)
        if stage is None:raise ValueError('Review stage does not exist in frozen model')
        actual=next((p for p in stage.profiles if p.face_index==face.face_index),None)
        if actual is None:raise ValueError('Review wall face missing in frozen model')
        depth=study.frame.crown_z_m-study.frame.toe_z_m
        if abs(face.layers[-1].bottom_depth_m-depth)>1e-6:
            raise ValueError('Soil profile must cover full wall height')
        for layer in face.layers:
            if layer.source_sha256 not in src['sourceDigests']:
                raise ValueError('Unverified soil parameter source bytes: '+layer.source_sha256)
        nodes={0.,depth,face.outside_water_depth_m,face.inside_water_depth_m}
        nodes.update([s.top_depth_m for s in face.layers]);nodes.update(s.bottom_depth_m for s in face.layers)
        nodes.update(study.frame.crown_z_m-p.z_m for p in actual.samples)
        nodes=sorted(d for d in nodes if 0<=d<=depth)
        comparisons=[];area=0.;normarea=0.;normmoment=0.
        for z in nodes:
            r=rankine_screen(face,z,contract.gamma_water_kn_m3)
            observed=_interp(actual,study.frame.crown_z_m-z)
            comparisons.append({**r,'modelPressureKpa':round(observed,7),
                                'differenceKpa':round(observed-r['netReferenceKpa'],7)})
        # Integrate each stratum from its own limiting interface value. Where
        # phi/c changes across an interface, reference active pressure jumps.
        # A continuous trapezoid across that interface would be incorrect.
        integration_points=set(nodes)
        for layer_index,layer in enumerate(face.layers):
            # Effective earth pressure changes from a negative trial value to
            # zero at a tension-crack threshold. Integrate the kink explicitly.
            internal=sorted(x for x in nodes if layer.top_depth_m<=x<=layer.bottom_depth_m)
            sine=sin(layer.friction_angle_deg*pi/180)
            ka=(1-sine)/(1+sine)
            c=layer.cohesion_effective_kpa
            def raw_active(d):
                r=rankine_screen(face,d,contract.gamma_water_kn_m3,stratum_index=layer_index)
                return ka*(r['effectiveVerticalKpa']+face.surcharge_kpa)-2*c*sqrt(ka)
            for da,db in zip(internal,internal[1:]):
                ya,yb=raw_active(da),raw_active(db)
                if ya*yb<0:
                    root=da+(db-da)*(-ya)/(yb-ya)
                    if da+1e-7<root<db-1e-7:integration_points.add(root)
        points=sorted(integration_points)
        for da,db in zip(points,points[1:]):
            mid=(da+db)/2
            index=next((i for i,l in enumerate(face.layers) if l.top_depth_m-1e-8<=mid<=l.bottom_depth_m+1e-8),None)
            if index is None:raise ValueError('Segment outside soil model')
            p0=rankine_screen(face,da,contract.gamma_water_kn_m3,stratum_index=index)
            p1=rankine_screen(face,db,contract.gamma_water_kn_m3,stratum_index=index)
            actual0=_interp(actual,study.frame.crown_z_m-da)
            actual1=_interp(actual,study.frame.crown_z_m-db)
            dl=db-da
            area+=dl*(actual0+actual1)/2
            r0,r1=p0['netReferenceKpa'],p1['netReferenceKpa']
            normarea+=dl*(r0+r1)/2
            normmoment+=dl*(r0*(2*da+db)+r1*(da+2*db))/6
        maximum=max(abs(i['differenceKpa']) for i in comparisons)
        rows.append({'stageId':face.stage_id,'faceIndex':face.face_index,
            'sourceSha256':sorted({i.source_sha256 for i in face.layers}),
            'modelResultantKnPerM':round(area,5),'rankineReferenceResultantKnPerM':round(normarea,5),
            'rankineReferenceFirstMomentKnMPerM':round(normmoment,5),
            'maxAbsoluteDifferenceKpa':round(maximum,5),
            'status':'numeric_close_only' if maximum<=contract.allowable_absolute_difference_kpa else 'numeric_difference',
            'samples':comparisons})
    if contract.pile_geometry_source_sha256 is not None and contract.pile_geometry_source_sha256 not in src['sourceDigests']:
        raise ValueError('Pile diameter source bytes missing or unverified')
    diameter=contract.pile_diameter_m
    b0=(min(study.frame.wall_pile_spacing_m,
           .9*(1.5*diameter+.5) if diameter<=1 else .9*(diameter+1))
           if diameter is not None else None)
    members={m['id']:m for m in result['structuralGraph']['members']}
    support_rows=[]
    for case in contract.support_cases:
        if case.source_sha256 not in src['sourceDigests']:
            raise ValueError('Support reference source bytes missing or unverified')
        m=members.get(case.member_id)
        if m is None or m['group']!='strut':
            raise ValueError('Support stiffness case references non-strut member')
        stage=next((s for s in result['stages'] if s['stageId']==case.stage_id),None)
        if stage is None:
            raise ValueError('Support stiffness reference stage missing')
        mrow=next((x for x in stage['memberResults'] if x['memberId']==case.member_id),None)
        if mrow is None:
            raise ValueError('Support member is not active in stated stage')
        section=study.frame.strut_section
        k=case.relaxation_alpha*section.E_kn_m2*section.area_m2*study.frame.wall_pile_spacing_m / (
                case.lambda_fixed_point*mrow['lengthM']*case.horizontal_support_spacing_m)
        support_rows.append({'stageId':case.stage_id,'memberId':case.member_id,
            'jgJ120ReferenceKnPerM':round(k,6),
            'rawMemberEAOverLKnPerM':round(section.E_kn_m2*section.area_m2/mrow['lengthM'],6),
            'supportSpacingM':case.horizontal_support_spacing_m,'methodReference':'JGJ 120-2012 4.1.10 support spring reference',
            'sourceSha256':case.source_sha256,'status':'reference_only_not_calibrated'})
    keys={(x['stageId'],x['faceIndex']) for x in rows}
    expected={(s.stage_id,p.face_index) for s in study.boundary.stages for p in s.profiles}
    missing=sorted(expected-keys)
    issues=['RANKINE_SCREEN_NOT_FULL_JGJ120_EARTH_PRESSURE_METHOD',
            'PASSIVE_EARTH_PRESSURE_ALREADY_REPRESENTED_BY_WINKLER_CONTACT',
            'SUPPORT_REACTION_REQUIRES_FULL_WALL_SUPPORT_STAGE_CALCULATION',
            'SUPPORT_PRELOAD_AND_INSTALLATION_HISTORY_UNQUALIFIED',
            'WATER_AND_SOIL_INPUT_AUTHENTICITY_NOT_ATTESTED',
            'GLOBAL_STABILITY_UPLIFT_AND_EMBEDMENT_NOT_INDEPENDENTLY_CLOSED']
    if missing:issues.append('INCOMPLETE_STAGE_FACE_PRESSURE_REVIEW')
    if any(r['status']=='numeric_difference' for r in rows):issues.append('PRESSURE_REFERENCE_DISAGREEMENT')
    return {'schemaVersion':'pitguard.m3-code-crosscheck/3.89.3',
       'candidateResultSha256':expected_result_sha256,'projectId':contract.project_id,
       'sourceArchiveSha256':expected_archive_sha256,'reviewArchiveSha256':src['archiveSha256'],
       'reviewRevision':contract.revision,'method':'layered_effective_stress_rankine_active_reference',
       'methodReference':'JGJ 120-2012 section 3.4 screening; site-specific method review required',
       'profiles':rows,'supportSpringReferences':support_rows,
       'singlePileSoilReactionWidthReferenceM':round(b0,6) if b0 is not None else None,
       'missingStageFaceCases':[{'stageId':a,'faceIndex':b} for a,b in missing],
       'unresolvedEngineeringChecks':issues,'sourceBytesVerified':True,
       'sourceAuthorQualificationReviewed':False,'modelInputRequalified':False,
       'status':'reference_numeric_review_only','engineeringQualified':False,
       'formalConstructionIssue':False}
