"""V3.89.5 same-identity review package (NOT signed-off construction issue).

DOCX calculation draft, annotated DXF and IFC4 curve reference all derive from
one immutable M3 spatial graph and the archived method review ledger.
"""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
from io import BytesIO,StringIO
from zipfile import ZipFile,ZIP_DEFLATED
from uuid import uuid5,UUID
import csv,json

from app.services.m3_reference_artifacts import make_candidate_work_bundle,_json,_csv

IFC_CHARS='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$'
NS=UUID('38a9933e-cc87-4bac-a713-e13f6a0c8e2d')


def _guid(value):
    i=uuid5(NS,value).int
    result=''
    for _ in range(22):
        result=IFC_CHARS[i%64]+result;i//=64
    return result


def _ifc_string(value):
    # IFC identifier text is ASCII because the member IDs are machine identifiers.
    return "'"+str(value).encode('ascii','replace').decode('ascii').replace("'","''")+"'"


def ifc4_axis_reference(graph,candidate_hash):
    """Geometric IFC4 axis-curve reference. No swept solid, design approval or quantities."""
    lines=['ISO-10303-21;','HEADER;',
        "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2]'),'2;1');",
        "FILE_NAME('PitGuard_axes_reference.ifc','2026-11-15T00:00:00',('PitGuard'),('PitGuard'),'PitGuard','PitGuard','');",
        "FILE_SCHEMA(('IFC4'));",'ENDSEC;','DATA;']
    counter=[0]
    def add(command):
        counter[0]+=1
        lines.append(f'#{counter[0]}={command};')
        return f'#{counter[0]}'
    origin=add('IFCCARTESIANPOINT((0.,0.,0.))')
    axes=add(f'IFCAXIS2PLACEMENT3D({origin},$,$)')
    ctx=add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,0.000001,{axes},$)")
    unit=add('IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)')
    units=add(f'IFCUNITASSIGNMENT(({unit}))')
    project=add(f"IFCPROJECT('{_guid(candidate_hash+'project')}',$,'PitGuard Review Reference',$,$,$,$,({ctx}),{units})")
    place=add(f'IFCLOCALPLACEMENT($,{axes})')
    site=add(f"IFCSITE('{_guid(candidate_hash+'site')}',$,'Reference Location',$,$,{place},$,$,.ELEMENT.,$,$,$,$,$)")
    add(f"IFCRELAGGREGATES('{_guid(candidate_hash+'aggregate')}',$,$,$,{project},({site}))")
    pts={n['id']:n for n in graph['nodes']}
    proxies=[]
    for m in graph['members']:
        a=pts[m['startNode']];b=pts[m['endNode']]
        p1=add(f"IFCCARTESIANPOINT(({a['x']:.8f},{a['y']:.8f},{a['z']:.8f}))")
        p2=add(f"IFCCARTESIANPOINT(({b['x']:.8f},{b['y']:.8f},{b['z']:.8f}))")
        curve=add(f'IFCPOLYLINE(({p1},{p2}))')
        representation=add(f"IFCSHAPEREPRESENTATION({ctx},'Axis','Curve3D',({curve}))")
        definition=add(f'IFCPRODUCTDEFINITIONSHAPE($,$,({representation}))')
        name=_ifc_string(m['id'])
        proxy=add(f"IFCBUILDINGELEMENTPROXY('{_guid(candidate_hash+str(m['id']))}',$,{name},'NOT FOR CONSTRUCTION - AXIS ONLY',{_ifc_string(m['group'])},{place},{definition},$,.NOTDEFINED.)")
        proxies.append(proxy)
    if proxies:
        add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE('{_guid(candidate_hash+'contain')}',$,$,$,({','.join(proxies)}),{site})")
    lines+=['ENDSEC;','END-ISO-10303-21;']
    return ('\n'.join(lines)+'\n').encode('ascii')


def annotated_review_dxf(graph,candidate_hash):
    import ezdxf
    doc=ezdxf.new('R2010')
    for layer,color in [('WALL',8),('WALE',3),('CROWN',5),('STRUT',2),('COLUMN',4),('REVIEW_NOT_FOR_CONSTRUCTION',1)]:
        doc.layers.new(layer,dxfattribs={'color':color})
    ms=doc.modelspace();nodes={n['id']:n for n in graph['nodes']}
    for m in graph['members']:
        a=nodes[m['startNode']];b=nodes[m['endNode']]
        ms.add_line((a['x'],a['y'],a['z']),(b['x'],b['y'],b['z']),dxfattribs={'layer':m['group'].upper() if m['group'] in ('wall','wale','crown','strut','column') else 'WALL'})
        if m['group']=='strut':
            mx=(a['x']+b['x'])/2;my=(a['y']+b['y'])/2
            ms.add_text(str(m['id'])[:32],dxfattribs={'height':.25,'insert':(mx,my,0.),'layer':'REVIEW_NOT_FOR_CONSTRUCTION'})
    ms.add_text('PITGUARD WORK REVIEW - DO NOT CONSTRUCT',dxfattribs={
        'height':.6,'insert':(0.,-3.,0.),'layer':'REVIEW_NOT_FOR_CONSTRUCTION'})
    ms.add_text('CAD/BIM AXIS MODEL ONLY - NO APPROVED NODE/REBAR DETAILS',dxfattribs={
        'height':.3,'insert':(0.,-4.,0.),'layer':'REVIEW_NOT_FOR_CONSTRUCTION'})
    stream=StringIO();doc.write(stream)
    return stream.getvalue().encode('utf-8')


def calculation_book_docx(result,normative_reviews,component_reviews,result_hash,source_hash):
    from docx import Document
    from docx.shared import Pt,Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    d=Document();sec=d.sections[0];sec.top_margin=Inches(.75);sec.bottom_margin=Inches(.7)
    style=d.styles['Normal'];style.font.name='Noto Sans CJK SC';style.font.size=Pt(9.5)
    d.add_heading('PitGuard 基坑支护计算与校审工作稿',0)
    p=d.add_paragraph('M3 / M5 / M7 · 规范方法对照与同源成果 · 禁止用于施工')
    p.style='Subtitle'
    d.add_paragraph('本稿使用同一冻结空间结果与规范对照记录。数值验算和资料字节校验尚未形成完整工程适用性确认；不具备正式施工图签发资格。')
    d.add_heading('1 计算身份与模型范围',level=1)
    for label,value in [('候选方案',result['candidateId']),('冻结结果 SHA-256',result_hash),
         ('原始输入 ZIP SHA-256',source_hash),('计算模型','分布净土压力 + 单向 Winkler 接触 + 理想桩端空间杆系')]:
        p=d.add_paragraph();p.add_run(label+'：').bold=True;p.add_run(str(value))
    d.add_heading('2 分阶段计算结果',level=1)
    table=d.add_table(rows=1,cols=6);table.style='Table Grid'
    for cell,val in zip(table.rows[0].cells,['阶段','最大位移 mm','最大支撑轴力 kN','接触点数','平衡残差 kN','数值状态']):cell.text=val
    for s in result['stages']:
        row=table.add_row().cells
        values=[s['stageId'],s['maximumWallHorizontalDisplacementMm'],s['maxBraceAxialKn'],
            s['soilContactActiveCount'],s['globalForceBalanceKn'],'参考计算']
        for cell,v in zip(row,values):cell.text=str(v)
    d.add_heading('3 独立规范方法对照',level=1)
    if not normative_reviews:
        d.add_paragraph('未收到规范土压力资料包。分层有效应力、Rankine 参考压力及原设计净土压力的独立对照待完成。')
    else:
        for index,item in enumerate(normative_reviews,1):
            r=item['report'];d.add_paragraph(f'复核 {index}：{r["reviewArchiveSha256"]} · {r["status"]}')
            t=d.add_table(rows=1,cols=4);t.style='Table Grid'
            for c,v in zip(t.rows[0].cells,['阶段/墙面','模型合力 kN/m','参考合力 kN/m','最大差 kPa']):c.text=v
            for a in r['profiles'][:16]:
                cells=t.add_row().cells
                for c,v in zip(cells,[f'{a["stageId"]} / {a["faceIndex"]}',a['modelResultantKnPerM'],
                              a['rankineReferenceResultantKnPerM'],a['maxAbsoluteDifferenceKpa']]):c.text=str(v)
            if len(r['profiles'])>16:d.add_paragraph('其余墙面/阶段详见随附 JSON 和 CSV。')
    d.add_heading('4 构件与节点分项检查',level=1)
    if not component_reviews:
        d.add_paragraph('未取得受控的构件承载力与节点详图计算依据；所有 M5/M7 节点承载力仍待校审。')
    else:
        for index,item in enumerate(component_reviews,1):
            r=item['report'];d.add_paragraph(f'校核 {index}：{r["status"]} · 构件 {len(r["memberChecks"])} 项 · 节点 {len(r["nodeChecks"])} 项 · 缺项 {len(r["missingChecks"])}。')
            d.add_paragraph('注意：输入的空间杆系作用未确认属于正式 ULS/SLS 组合；承载力利用率、欧拉基准和局部节点算式均仅供初筛。')
    d.add_heading('5 待闭合项目及校审结论',level=1)
    for v in ['土压力算法适用性、分层水位和参数真实性',
              '支撑轴力与偏心受压、围檩连续梁、立柱竖向作用',
              '围护桩全阶段轴弯剪、变形与稳定；施工历史与边界校准',
              '圆桩裂缝、钢筋锚固、连接节点完整承载力',
              '图算表模型一致性及设计、复核、审核和签发手续']:
        d.add_paragraph('待核验：'+v,style='Normal')
    p=d.add_paragraph('结论：工程合格状态 = 未确认；正式施工图签发 = 禁止。')
    p.runs[0].bold=True
    p=d.add_paragraph('设计：________    复核：________    审核：________    日期：________')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    d.add_heading('附件 A 主要构件控制内力摘录',level=1)
    d.add_paragraph('按同一冻结空间计算结果列出前 10 项轴力较大的构件，完整数据见 stage_member_results.csv。')
    triples=[(s['stageId'],r) for s in result['stages'] for r in s['memberResults']]
    triples.sort(key=lambda x:abs(x[1]['axialKn']),reverse=True)
    t=d.add_table(rows=1,cols=5);t.style='Table Grid'
    for c,v in zip(t.rows[0].cells,['阶段','构件','N kN','My kN·m','Vy kN']):c.text=v
    for sid,r in triples[:10]:
        for c,v in zip(t.add_row().cells,[sid,r['memberId'][:26],r['axialKn'],
                                r['momentYKnM'],r['shearYKn']]):c.text=str(v)
    d.add_heading('附件 B 独立审查证据与未闭合事项',level=1)
    d.add_paragraph(f'压力来源 ZIP：{source_hash[:32]}…；冻结候选结果：{result_hash[:32]}…；'
                    f'规范方法复核 {len(normative_reviews)} 份；构件与节点复核 {len(component_reviews)} 份。')
    for item in result.get('unresolvedEngineeringChecks',[])[:4]:
        d.add_paragraph('未闭合：'+str(item))
    buf=BytesIO();d.save(buf);return buf.getvalue()


def make_m3_review_package(result,source_audit,normative_reviews=(),component_reviews=()):
    if result.get('status')!='nonlinear_contact_reference_solved_only' or result.get('engineeringQualified') is not False or result.get('formalConstructionIssue') is not False:
        raise ValueError('Review package is restricted to unqualified frozen research results')
    initial=make_candidate_work_bundle(result,source_audit)
    with ZipFile(BytesIO(initial)) as z:
        files={n:z.read(n) for n in z.namelist() if n!='manifest.json'}
    result_hash=sha256(_json(result)).hexdigest()
    for r in list(normative_reviews)+list(component_reviews):
        if r['report']['candidateResultSha256']!=result_hash or r['report']['sourceArchiveSha256']!=source_audit['archiveSha256']:
            raise ValueError('Review references a different frozen source/candidate')
    graph=result['structuralGraph']
    files['review_drawing.dxf']=annotated_review_dxf(graph,result_hash)
    files['reference_axes.ifc']=ifc4_axis_reference(graph,result_hash)
    files['engineering_calculation_review.docx']=calculation_book_docx(result,normative_reviews,component_reviews,
                                                        result_hash,source_audit['archiveSha256'])
    files['normative_review_ledger.json']=_json(list(normative_reviews))
    files['component_review_ledger.json']=_json(list(component_reviews))
    files['review_gates.json']=_json({'schemaVersion':'pitguard.m3-review-gates/3.89.5',
        'sourceArchiveSha256':source_audit['archiveSha256'],'candidateResultSha256':result_hash,
        'normativeReviewCount':len(normative_reviews),'componentReviewCount':len(component_reviews),
        'engineeringQualified':False,'formalConstructionIssue':False,
        'cadStatus':'review_geometry_only','ifcStatus':'curve_axis_reference_only',
        'docxStatus':'unapproved_calculation_review_only',
        'standardsMethodComplete':False,'professionalSignaturesVerified':False,
        'requiredBeforeFormalIssue':['full_normative_methods','ULS_SLS_combinations',
         'complete_node_and_rebar_detailing','source_authenticity','engineer_signatures']})
    files['review_index.csv']=_csv(['type','path','qualification'],[
        ('Calculation','engineering_calculation_review.docx','WORKING COPY'),
        ('CAD','review_drawing.dxf','REFERENCE ONLY'),
        ('BIM','reference_axes.ifc','AXES ONLY'),
        ('Frozen values','model_result.json','UNQUALIFIED')])
    files['manifest.json']=_json({'schemaVersion':'pitguard.m3-review-bundle/3.89.5',
        'candidateResultSha256':result_hash,'sourceArchiveSha256':source_audit['archiveSha256'],
        'status':'not_for_construction','engineeringQualified':False,'formalConstructionIssue':False,
        'files':{n:{'sha256':sha256(raw).hexdigest(),'bytes':len(raw)} for n,raw in sorted(files.items())}})
    b=BytesIO()
    with ZipFile(b,'w',compression=ZIP_DEFLATED) as z:
        for n,raw in sorted(files.items()):z.writestr(n,raw)
    return b.getvalue()
