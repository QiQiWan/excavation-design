"""Same-source research DXF/CSV/JSON bundle for one frozen M3 study candidate.

The artifact is visibly REFERENCE ONLY and cannot issue construction drawings.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO, StringIO
from zipfile import ZipFile, ZIP_DEFLATED
import csv,json


def _json(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')


def _csv(headers,rows):
    stream=StringIO();w=csv.writer(stream);w.writerow(headers);w.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def _dxf(graph):
    lines=['0','SECTION','2','HEADER','9','$ACADVER','1','AC1009','0','ENDSEC',
           '0','SECTION','2','ENTITIES']
    nodes={int(n['id']):n for n in graph['nodes']}
    for member in graph['members']:
        a=nodes[member['startNode']];b=nodes[member['endNode']]
        layer={'wall':'WALL','wale':'WALE','crown':'CROWN','strut':'BRACE','column':'COLUMN'}.get(member['group'],'REFERENCE')
        lines+=['0','LINE','8',layer,'10',str(a['x']),'20',str(a['y']),'30',str(a['z']),
                '11',str(b['x']),'21',str(b['y']),'31',str(b['z'])]
    lines+=['0','TEXT','8','NOT_FOR_CONSTRUCTION','10','0','20','0','30','0',
            '40','0.35','1','REFERENCE MODEL - NOT FOR CONSTRUCTION',
            '0','ENDSEC','0','EOF']
    return ('\n'.join(lines)+'\n').encode('ascii')


def make_candidate_work_bundle(result:dict,source_audit:dict)->bytes:
    if result.get('status')!='nonlinear_contact_reference_solved_only':
        raise ValueError('Only a solved immutable M3 reference candidate may be exported')
    if result.get('engineeringQualified') is not False or result.get('formalConstructionIssue') is not False:
        raise ValueError('Formal issue flag must remain false')
    graph=result['structuralGraph']
    node_ids={n['id'] for n in graph['nodes']}
    member_ids={m['id'] for m in graph['members']}
    if any(m['startNode'] not in node_ids or m['endNode'] not in node_ids for m in graph['members']):
        raise ValueError('Spatial graph references a missing node')
    if len(member_ids)!=len(graph['members']):
        raise ValueError('Spatial member identities must be unique')
    for stage in result['stages']:
        if any(m['memberId'] not in member_ids for m in stage['memberResults']):
            raise ValueError('Stage force references missing structural member')
    candidate_bytes=_json(result)
    candidate_hash=sha256(candidate_bytes).hexdigest()
    files={'model_result.json':candidate_bytes,'reference_geometry.dxf':_dxf(graph),
        'spatial_nodes.csv':_csv(('nodeId','xM','yM','zM','tags'),[
          (n['id'],n['x'],n['y'],n['z'],'|'.join(n['tags'])) for n in graph['nodes']]),
        'spatial_members.csv':_csv(('memberId','startNode','endNode','group','kind','activation'),[
          (m['id'],m['startNode'],m['endNode'],m['group'],m['kind'],m['activation']) for m in graph['members']]),
        'stage_member_results.csv':_csv(('stageId','memberId','group','axialKn','shearYKn','shearZKn',
                                        'momentYKnM','momentZKnM','torsionKnM'),[
          (s['stageId'],r['memberId'],r['group'],r['axialKn'],r['shearYKn'],r['shearZKn'],
           r['momentYKnM'],r['momentZKnM'],r['torsionKnM'])
          for s in result['stages'] for r in s['memberResults']]),
        'm7_node_action_ledger.csv':_csv(('stageId','memberId','nodeId','xM','yM','zM',
                          'FxKn','FyKn','FzKn','MxKnM','MyKnM','MzKnM','qualification','signBasis'),[
          (r['stageId'],r['memberId'],r['nodeId'],*r['pointM'],*r['actionOnNodeForceKn'],*r['actionOnNodeMomentKnM'],
           'unqualified_model_action','global_xyz_action_on_node_from_negated_element_end_force')
          for s in result['stages'] for r in s['braceNodeActions']]),
        'engineering_gates.json':_json({'candidateId':result['candidateId'],
           'candidateResultSha256':candidate_hash,'pressureEvidenceArchiveSha256':source_audit['archiveSha256'],
           'verifiedPressureBytesOnly':True,'independentSoilModelCalibration':False,
           'nodeCapacityQualified':False,'globalStabilityQualified':False,
           'automaticAdoptionAllowed':False,'engineeringQualified':False,
           'formalConstructionIssue':False,'unresolvedChecks':result['unresolvedEngineeringChecks']})}
    manifest={'schemaVersion':'pitguard.m3-research-artifact/3.89.2',
        'candidateId':result['candidateId'],'candidateResultSha256':candidate_hash,
        'sourceArchiveSha256':source_audit['archiveSha256'],
        'status':'work_reference_only',
        'formalConstructionIssue':False,
        'files':{name:{'sha256':sha256(content).hexdigest(),'bytes':len(content)} for name,content in sorted(files.items())}}
    files['manifest.json']=_json(manifest)
    buf=BytesIO()
    with ZipFile(buf,'w',compression=ZIP_DEFLATED) as archive:
        for name,blob in sorted(files.items()):archive.writestr(name,blob)
    return buf.getvalue()
