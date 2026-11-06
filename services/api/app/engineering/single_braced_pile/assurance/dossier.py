"""Issue-blocked P1-P4 working-copy delivery from one immutable result.

The file contains a preliminary engineer-reviewed calculation, never a
construction issue or a normative completion certificate.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
import csv,io,json
from xml.sax.saxutils import escape
from .quality_protocol import acceptance_plan
from .workflow import UnifiedAuditInput
from ..artifacts import export_bundle


def _json(data):
    return json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False,sort_keys=True).encode('utf-8')


def _pdf(plan:dict)->bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    styles=getSampleStyleSheet()
    normal=ParagraphStyle('CNBody',parent=styles['Normal'],fontName='STSong-Light',fontSize=9.5,
                          leading=16,spaceAfter=8)
    heading=ParagraphStyle('CNHead',parent=normal,fontSize=15,leading=21,spaceBefore=12,spaceAfter=12)
    title=ParagraphStyle('CNTitle',parent=heading,fontSize=18,alignment=TA_CENTER,leading=26)
    small=ParagraphStyle('CNSmall',parent=normal,fontSize=8,leading=13)
    content=[]
    content.append(Paragraph('单排桩支护工程  P1-P4 计算与校审记录',title))
    content.append(Paragraph('计算复核工作版 - 不得用于施工；本记录不具有正式设计签发效力。',normal))
    for label,key in [('项目','projectId'),('剖面','sectionId'),('输入快照','snapshotHash'),
                      ('计算结果','resultHash'),('校审记录','reviewHash')]:
        content.append(Paragraph(escape(f'{label}：{plan.get(key) or "未提供"}'),small))
    content.append(Spacer(1,10))
    content.append(Paragraph('分项验收与未完成事项',heading))
    for phase in plan['phases']:
        section=[]
        rows=[[Paragraph('阶段 / 领域',small),Paragraph('当前状态',small)],
              [Paragraph(escape(f'{phase["phase"]}  {phase["name"]}'),normal),
               Paragraph(escape(phase['assessmentStatus']),small)]]
        t=Table(rows,colWidths=[295,185],hAlign='LEFT')
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#CBD5E1')),
              ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EEF2F6')),
              ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),
              ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),
              ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        section.append(t)
        section.append(Paragraph(escape('依据：'+phase['basis']),small))
        missing=phase.get('outstanding') or []
        for item in missing[:6]:section.append(Paragraph(escape('待补：'+str(item)),small))
        if len(missing)>6:section.append(Paragraph(f'另有 {len(missing)-6} 项缺项，详见 P1_P4_gate_index.json。',small))
        review=phase.get('engineerReview') or {}
        label=review.get('decision') or '未记录'
        section.append(Paragraph(escape('工程师复核：'+label),small))
        section.append(Spacer(1,7))
        content.append(KeepTogether(section))
    content.append(Paragraph('工程边界和结果使用限制',heading))
    for item in plan['limitations']:
        content.append(Paragraph(escape('限制：'+item),normal))
    content.append(Paragraph('所有计算书、CAD图、配筋表与冻结输入的 SHA256 见本包 manifest.json。未完成的分项必须独立补齐证据和校审；不得以研究算例或截图上的峰值代替真实工程计算。',normal))
    out=BytesIO()
    doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=48,leftMargin=48,topMargin=45,
                          bottomMargin=42,title='P1-P4 engineering review (working copy)')
    doc.build(content)
    return out.getvalue()


def export_assurance_dossier(payload:UnifiedAuditInput,result:dict,review:dict,*,phase_reviews:tuple[dict,...]=())->tuple[bytes,dict]:
    plan=acceptance_plan(payload,result,review,independent=True,phase_reviews=phase_reviews)
    if not plan['workCopyExportEligible']:
        raise ValueError('Only a completed and properly reviewed frozen result can be exported')
    original_bundle,orig_manifest=export_bundle(result,review)
    artifacts={}
    with ZipFile(BytesIO(original_bundle),'r') as nested:
        for name in nested.namelist():
            if '..' in name or name.startswith('/'):
                raise ValueError('Unsafe existing export path')
            artifacts[name]=nested.read(name)
    # Keep the existing ordinary-export manifest as immutable proof of the
    # source bundle, but under a distinct name. The outer manifest replaces
    # manifest.json and must only describe the actual outer archive bytes.
    original_manifest=artifacts.pop('manifest.json')
    artifacts['original_export_manifest.json']=original_manifest
    artifacts['P1_P4_gate_index.json']=_json(plan)
    artifacts['P1_P4_phase_reviews.json']=_json(list(phase_reviews))
    artifacts['P1_P4_engineering_review.pdf']=_pdf(plan)
    summary=io.StringIO();writer=csv.writer(summary)
    writer.writerow(['阶段','领域','状态','缺项数量','正式签发'])
    for p in plan['phases']:
        writer.writerow([p['phase'],p['name'],p['assessmentStatus'],len(p['outstanding']),'否'])
    artifacts['P1_P4_review_status.csv']=summary.getvalue().encode('utf-8-sig')
    artifacts['P1_P4_usage_note.txt']=('工程复核工作版，不得用于施工。\n该成果由冻结输入和封存结果生成。\n'
        'P1 水力、P2 圆桩、P3 真实工程、P4 正式成果的证据及方法未全部闭合，正式签发状态恒为 false。\n').encode('utf-8')
    # Do not reuse the prior manifest: it does not cover newly included files.
    manifest={'schemaVersion':'pitguard.P1-P4-review-bundle/1',
              'snapshotHash':result['snapshotHash'],'resultHash':result['resultHash'],
              'reviewHash':review['reviewHash'],'formalConstructionIssue':False,
              'previousManifestSha256':sha256(original_manifest).hexdigest(),
              # The new manifest replaces the nested one. It cannot hash
              # itself and must not list the superseded original under the
              # same path: every listed file must be present with exact bytes.
              'files':[{'name':name,'bytes':len(blob),'sha256':sha256(blob).hexdigest()}
                       for name,blob in sorted(artifacts.items()) if name!='manifest.json']}
    artifacts['manifest.json']=_json(manifest)
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED,compresslevel=8) as z:
        for name,blob in sorted(artifacts.items()):z.writestr(name,blob)
    return buffer.getvalue(),manifest
