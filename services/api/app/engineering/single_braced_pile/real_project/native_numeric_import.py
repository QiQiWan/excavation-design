"""Strict native five-stage numerical evidence intake for Luoyu 3-3.

A ZIP passed by the client is a reproducible byte receipt, *not* proof that
FRWS or any design authority issued it. Numerical comparison is kept separate
from the field/method qualification and construction-release decisions.
"""
from __future__ import annotations
from io import BytesIO, StringIO
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from hashlib import sha256
from math import isfinite
import csv
import json
import re

STAGES = ('S1','S2','S3','S4','S5')
DATA_FILES = ('stages.json','curve.csv','pressure.csv','brace.csv')
EXPECTED_FILES = frozenset(('metadata.json',) + DATA_FILES)
MAX_ARCHIVE_BYTES = 16_000_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 48_000_000
MAX_CSV_ROWS_PER_FILE = 40000
CURVE_COLUMNS = ('stageId','depthSiteM','side','displacementMm','momentKnM','shearKn')
PRESSURE_COLUMNS = ('stageId','depthSiteM','side','pressureKind','pressureKpa')
BRACE_COLUMNS = ('stageId','id','reactionKnPerPile','referenceMm')


def _fail(message):
    raise ValueError('native_numeric: ' + message)


def _json(data:bytes,name:str):
    if len(data)>2_000_000:_fail(name+' exceeds JSON limit')
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:_fail(name+': duplicate JSON key: '+str(k))
            obj[k]=v
        return obj
    try:return json.loads(data.decode('utf-8-sig'),object_pairs_hook=unique,
                           parse_constant=lambda x:_fail(name+': nonfinite JSON value'))
    except (UnicodeError,json.JSONDecodeError) as exc:_fail(name+': invalid UTF-8 JSON: '+str(exc))


def _number(s, field, *,allow_negative=True):
    if not isinstance(s,str) or not s.strip():_fail('Missing '+field)
    try:v=float(s)
    except (ValueError,OverflowError):_fail('Invalid '+field)
    if not isfinite(v) or abs(v)>1_000_000 or (not allow_negative and v<0):
        _fail('Invalid numeric range for '+field)
    return v


def _read_csv(data:bytes, columns:tuple[str,...],name:str):
    if len(data)>25_000_000:_fail(name+' exceeds size limit')
    try:
        stream=StringIO(data.decode('utf-8-sig'),newline='')
        reader=csv.DictReader(stream,strict=True)
        if reader.fieldnames != list(columns):_fail(name+': required header/order: '+','.join(columns))
        result=[]
        for row in reader:
            if len(result)>=MAX_CSV_ROWS_PER_FILE:_fail(name+': too many rows')
            if None in row or any(x is None for x in row.values()):_fail(name+': malformed row')
            if any(len(str(v))>250 for v in row.values()):_fail(name+': excessive field length')
            result.append(row)
        return result
    except (UnicodeError,csv.Error) as exc:_fail(name+': CSV parse error: '+str(exc))


def _read_zip(blob:bytes):
    if len(blob)>MAX_ARCHIVE_BYTES:_fail('ZIP exceeds byte budget')
    if len(blob)<25:_fail('ZIP is empty')
    try:
        with ZipFile(BytesIO(blob)) as z:
            info=z.infolist();names=[x.filename for x in info]
            if len(names)!=len(EXPECTED_FILES) or set(names)!=EXPECTED_FILES:
                _fail('ZIP must contain exactly metadata.json, stages.json, curve.csv, pressure.csv, brace.csv')
            if len(set(names))!=len(names):_fail('Duplicate ZIP entry')
            total=0;result={}
            for entry in info:
                if (entry.flag_bits & 1 or entry.is_dir() or entry.file_size>25_000_000 or
                     entry.filename.startswith('/') or '/' in entry.filename or '\\' in entry.filename):
                    _fail('Unsafe or oversized ZIP entry')
                # Check symlinks (Unix mode bits) and cumulative inflation budget.
                if ((entry.external_attr>>16)&0o170000)==0o120000:_fail('ZIP symlinks are forbidden')
                if entry.compress_size and entry.file_size>max(1,entry.compress_size)*2000:
                    _fail('Suspicious ZIP compression ratio')
                with z.open(entry) as f:
                    chunks=[];size=0
                    while True:
                        part=f.read(1024*1024)
                        if not part:break
                        size+=len(part);total+=len(part)
                        if size>25_000_000 or total>MAX_TOTAL_UNCOMPRESSED_BYTES:_fail('ZIP extraction budget exceeded')
                        chunks.append(part)
                if size!=entry.file_size:_fail('ZIP declared file size disagrees with bytes')
                result[entry.filename]=b''.join(chunks)
            return result
    except (BadZipFile,OSError,RuntimeError,EOFError) as exc:_fail('Bad ZIP: '+str(exc))


def import_native_numeric(blob:bytes)->dict:
    files=_read_zip(blob)
    meta=_json(files['metadata.json'],'metadata.json')
    if not isinstance(meta,dict) or meta.get('schemaVersion')!='pitguard.luoyu-native-numeric/1':
        _fail('Unsupported metadata.schemaVersion')
    if meta.get('sectionId')!='3-3' or not isinstance(meta.get('projectId'),str) or not meta['projectId'].strip():
        _fail('Project ID and Luoyu 3-3 section are required')
    required_units={'depthDatum':'original_ground_down_m','forceBasis':'single_pile',
                    'displacementUnit':'mm','momentUnit':'kN.m','shearUnit':'kN',
                    'pressureUnit':'kPa','reactionUnit':'kN/pile','referenceUnit':'mm'}
    for field,value in required_units.items():
        if meta.get(field)!=value:_fail('Metadata unit/datum mismatch: '+field)
    checks=meta.get('fileDigests')
    if not isinstance(checks,dict) or set(checks)!=set(DATA_FILES):
        _fail('metadata.fileDigests must cover every data file exactly')
    for name in DATA_FILES:
        digest=checks[name]
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
            _fail('Invalid SHA256 declaration for '+name)
        if sha256(files[name]).hexdigest()!=digest:_fail('Data byte hash mismatch: '+name)
    stages=_json(files['stages.json'],'stages.json')
    if (not isinstance(stages,list) or len(stages)!=5 or
        [s.get('id') for s in stages if isinstance(s,dict)]!=list(STAGES)):
        _fail('stages.json must contain S1-S5 exactly in order')
    indexed={}
    for s in stages:
        if set(s)!={'id','activeSupportIds'} or not isinstance(s['activeSupportIds'],list):
            _fail('Invalid stage object or missing activeSupportIds')
        supports=s['activeSupportIds']
        if len(supports)!=len(set(supports)) or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in supports):
            _fail('Duplicate or invalid stage support IDs')
        indexed[s['id']]={'id':s['id'],'activeSupportIds':supports,'status':'success',
                         'curve':[],'pressureStations':[],'strutReactions':[]}
    curve=_read_csv(files['curve.csv'],CURVE_COLUMNS,'curve.csv')
    pressure=_read_csv(files['pressure.csv'],PRESSURE_COLUMNS,'pressure.csv')
    braces=_read_csv(files['brace.csv'],BRACE_COLUMNS,'brace.csv')
    if not curve or not pressure:_fail('All numeric channels require physical station rows')
    seen_curve=set();seen_pressure=set();seen_brace=set()
    for row in curve:
        sid=row['stageId'];side=row['side']
        if sid not in indexed or side not in ('above','below'):_fail('Invalid stage or shear-node side in curve.csv')
        depth=_number(row['depthSiteM'],'curve depth',allow_negative=False)
        key=(sid,round(depth,8),side)
        if key in seen_curve:_fail('Duplicate curve station '+str(key))
        seen_curve.add(key)
        indexed[sid]['curve'].append({'depthSiteM':depth,'side':side,
                **{k:_number(row[k],k) for k in CURVE_COLUMNS[3:]}})
    for row in pressure:
        sid=row['stageId'];side=row['side'];kind=row['pressureKind']
        if sid not in indexed or side not in ('inside','outside') or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,31}',kind):
            _fail('Invalid stage, side or pressure kind in pressure.csv')
        depth=_number(row['depthSiteM'],'pressure depth',allow_negative=False)
        key=(sid,round(depth,8),side,kind)
        if key in seen_pressure:_fail('Duplicate pressure station '+str(key))
        seen_pressure.add(key)
        indexed[sid]['pressureStations'].append({'depthSiteM':depth,'side':side,
                                  'pressureKind':kind,'pressureKpa':_number(row['pressureKpa'],'pressureKpa')})
    for row in braces:
        sid=row['stageId'];brace=row['id']
        if sid not in indexed or not brace or len(brace)>100:_fail('Invalid stage or support ID in brace.csv')
        key=(sid,brace)
        if key in seen_brace:_fail('Duplicate brace reaction '+str(key))
        seen_brace.add(key)
        indexed[sid]['strutReactions'].append({'id':brace,
            'reactionKnPerPile':_number(row['reactionKnPerPile'],'reactionKnPerPile'),
            'referenceMm':_number(row['referenceMm'],'referenceMm')})
    for sid in STAGES:
        s=indexed[sid]
        if not s['curve']:_fail('Missing u/M/V stations in '+sid)
        if {p['side'] for p in s['pressureStations']}!={'inside','outside'}:
            _fail('Both pressure sides required in '+sid)
        if {x['id'] for x in s['strutReactions']}!=set(s['activeSupportIds']):
            _fail('Installed support and brace reaction identities disagree in '+sid)
        s['curve'].sort(key=lambda x:(x['depthSiteM'],x['side']))
        s['pressureStations'].sort(key=lambda x:(x['depthSiteM'],x['side'],x['pressureKind']))
        s['strutReactions'].sort(key=lambda x:x['id'])
    reference={'sourceKind':'native_numeric_export','fileSha256':sha256(blob).hexdigest(),
               'depthDatum':meta['depthDatum'],'forceBasis':meta['forceBasis'],
               'pressureUnit':meta['pressureUnit'],
               'methodAndBoundaryConfirmed':bool(meta.get('methodAndBoundaryConfirmed') is True),
               'stages':[indexed[x] for x in STAGES]}
    return {'schemaVersion':'pitguard.native-numeric-intake/1',
            'projectId':meta['projectId'],'sectionId':meta['sectionId'],
            'assessmentStatus':'numeric_arrays_validated_source_method_review_required',
            'archiveSha256':sha256(blob).hexdigest(),
            'actualFileDigests':{k:sha256(v).hexdigest() for k,v in files.items()},
            'stationCounts':{sid:{'curve':len(indexed[sid]['curve']),
                 'pressure':len(indexed[sid]['pressureStations']),
                 'brace':len(indexed[sid]['strutReactions'])} for sid in STAGES},
            'nativeReference':reference,
            'methodAndBoundaryConfirmedDeclaration':meta.get('methodAndBoundaryConfirmed') is True,
            'originalSourceBytesIndependentlyAuthenticated':False,
            'formalSameConditionVerification':False,'formalDesignReady':False,
            'note':'The uploaded bytes match their internal SHA256 declarations. No external original-file signature, engineer method qualification or native software provenance was verified.'}


def compare_native_with_candidate(blob:bytes,candidate:dict)->dict:
    from .complete_curve_protocol import compare_five_stage_full_channels
    from .curve_adapter import normalize_research_candidate
    native=import_native_numeric(blob)
    if not isinstance(candidate,dict):_fail('Candidate must be an object')
    if candidate.get('modelStatus')=='conditional_research_only':
        candidate=normalize_research_candidate(candidate)
    comparison=compare_five_stage_full_channels(candidate,native['nativeReference'])
    return {'schemaVersion':'pitguard.native-numeric-comparison/1',
            'archiveSha256':native['archiveSha256'],
            'assessmentStatus':comparison['comparisonStatus'],
            'stationCounts':native['stationCounts'],'comparison':comparison,
            'sourceBytesIndependentlyAuthenticated':False,
            'methodAndBoundaryIndependentlyQualified':False,
            'formalSameConditionVerification':False,
            'formalDesignReady':False}


def make_blank_numeric_template()->bytes:
    stages=[{'id':sid,'activeSupportIds':[]} for sid in STAGES]
    data={'stages.json':json.dumps(stages,ensure_ascii=False,indent=2).encode('utf-8')}
    for name,headers in (('curve.csv',CURVE_COLUMNS),('pressure.csv',PRESSURE_COLUMNS),('brace.csv',BRACE_COLUMNS)):
        f=StringIO(newline='');writer=csv.writer(f);writer.writerow(headers)
        data[name]=f.getvalue().encode('utf-8-sig')
    metadata={'schemaVersion':'pitguard.luoyu-native-numeric/1','projectId':'填写项目ID',
              'sectionId':'3-3','depthDatum':'original_ground_down_m','forceBasis':'single_pile',
              'displacementUnit':'mm','momentUnit':'kN.m','shearUnit':'kN',
              'pressureUnit':'kPa','reactionUnit':'kN/pile','referenceUnit':'mm',
              'methodAndBoundaryConfirmed':False,
              'fileDigests':{name:sha256(data[name]).hexdigest() for name in DATA_FILES}}
    data['metadata.json']=json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')
    out=BytesIO()
    with ZipFile(out,'w',compression=ZIP_DEFLATED,compresslevel=8) as z:
        for name in sorted(data):z.writestr(name,data[name])
    return out.getvalue()
