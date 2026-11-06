"""Preliminary reports/section/curves/rebar checklist from ONE verified, reviewed result.

No re-solving, auto-section sizing or rebar auto-approval occurs here.
"""
from __future__ import annotations
from pathlib import Path
from math import pi
from tempfile import TemporaryDirectory
from zipfile import ZipFile,ZIP_DEFLATED
from io import BytesIO
import csv,json,hashlib

from .analysis import canonical_hash

LABEL='计算复核工作版 - 不得用于施工'

CHECK_LABELS={
    '4.2.2_embedment':'4.2.2 单支点嵌固稳定',
    '4.2.3_global_sliding':'4.2.3 整体滑动适用性',
    '4.2.4_basal_heave':'4.2.4 坑底隆起',
    '4.2.5_soft_soil_heave':'4.2.5 软土滑弧隆起',
    '4.2.6_groundwater':'4.2.6 地下水与附录C',
    '4.2.7_minimum_embedment':'4.2.7 建议最小嵌固',
    '3.1_load_combinations':'3.1 作用组合',
    'construction_adverse_stability':'施工不利工况稳定性',
    '4.9_internal_brace':'4.9 内支撑杆件与节点',
    'concrete_circular_pile':'圆桩配筋及构造',
}
ISSUE_LABELS={
  'SOURCE_MISSING':'工程输入来源待补', 'SOURCE_NOT_APPROVED':'输入参数尚未确认',
  'VERIFICATION_ONLY':'数值验证例用途限制', 'ACTION_COMBINATION_REQUIRES_REVIEW':'作用组合缺少全部必要项',
  'STABILITY_NOT_INTEGRATED':'稳定验算尚未全部完成',
  'REBAR_NOT_FULLY_VERIFIED':'圆桩配筋仍有未验项目',
  'NORMATIVE_3_1_LOAD_COMBINATIONS':'作用组合与条文覆盖待完成',
  'NORMATIVE_4_9_INTERNAL_BRACE':'实际内支撑体系待复核',
  'NORMATIVE_CONCRETE_CIRCULAR_PILE':'圆桩承载力或构造未通过',
  'NORMATIVE_4_2_2_EMBEDMENT':'单支点嵌固稳定待完成',
  'NORMATIVE_4_2_4_BASAL_HEAVE':'坑底隆起验算待完成',
  'NORMATIVE_4_2_5_SOFT_SOIL_HEAVE':'软土圆弧抗隆起待完成',
  'NORMATIVE_4_2_6_GROUNDWATER':'地下水与渗透稳定待完成',
  'NORMATIVE_4_2_7_MINIMUM_EMBEDMENT':'最小嵌固深度待完成',
  'NORMATIVE_CONCRETE_CIRCULAR_CRACK_CONTROL':'圆桩裂缝宽度尚无适用方法',
  'NORMATIVE_CONCRETE_CIRCULAR_MATCHED_NM_V':'同阶段轴弯剪计算待确认',
  'NORMATIVE_CONSTRUCTION_ADVERSE_STABILITY':'施工不利工况稳定仍需复核',
}
SOURCE_LABELS={
  'pile.diameter_m':'桩径','pile.spacing_m':'桩中心距','pile.length_m':'桩长',
  'pile.elastic_modulus_kpa':'弹性模量','pile.inertia_factor':'抗弯刚度折减系数',
  'strut.stiffness_kn_m':'支撑等效刚度','strut.depth_m':'支撑深度',
  'displacement_limit_mm':'水平位移限值','design_effect_factor':'设计作用系数',
}

STATUS_LABELS={
    'pass':'本项通过', 'fail':'本项未通过', 'missing_input':'缺少输入',
    'not_calculated':'未完成验算', 'outside_method_scope':'当前方法不适用',
    'not_applicable':'经确认不适用', 'not_generally_mandated_for_supported_type':'该体系通常不要求',
    'calculated_scope_limited':'仅完成受限计算', 'review_required':'仍需工程复核',
    'engineering_fail':'工程构造未通过', 'screening_only':'仅完成初筛'
}


def _table_docx(doc, headers,rows):
    t=doc.add_table(rows=1, cols=len(headers));t.style='Light Grid Accent 1'
    for cell,head in zip(t.rows[0].cells,headers):cell.text=str(head)
    for row in rows:
        for cell,value in zip(t.add_row().cells,row):cell.text=str(value)

def _font(doc):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    styles=doc.styles
    for name in ('Normal','Title','Heading 1','Heading 2'):
        s=styles[name]
        s.font.name='SimSun' if name=='Normal' else 'SimHei'; s.font.size=Pt(10.5 if name=='Normal' else 14)
        s._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体' if name=='Normal' else '黑体')

def _docx(result,review,path):
    from docx import Document
    from docx.shared import Mm
    doc=Document();_font(doc)
    sec=doc.sections[0];sec.top_margin=Mm(19);sec.bottom_margin=Mm(19);sec.left_margin=Mm(24);sec.right_margin=Mm(22)
    data=result['snapshot'];geometry=data['pile'];control=result['governing']
    doc.add_heading('单排桩一道内支撑计算复核书',0)
    doc.add_paragraph(LABEL)
    doc.add_paragraph('项目：'+data['project_id']+'  剖面：'+data['section_id'])
    doc.add_paragraph('复核人：'+review['reviewer']+'    复核说明：'+review['notes'])
    doc.add_paragraph('输入快照：'+result['snapshotHash'])
    doc.add_paragraph('封存结果：'+result['resultHash'])
    doc.add_paragraph('复核记录：'+review['reviewHash'])
    doc.add_heading('1. 方法与适用范围',1)
    for term in result['methodAssumptions']:doc.add_paragraph(term,style='List Bullet')
    doc.add_paragraph('依据：JGJ120-2012第3.4、4.1节；GB55003-2021第7章。')
    doc.add_heading('2. 冻结输入与来源',1)
    _table_docx(doc,['项目','数值','单位'],[
        ('桩径',geometry['diameter_m'],'m'),('桩中心距',geometry['spacing_m'],'m'),
        ('桩长',geometry['length_m'],'m'),('弹性模量',geometry['elastic_modulus_kpa'],'kPa'),
        ('EI',round(result['mesh']['eiKnM2'],2),'kN.m²'),
        ('土反力宽度b0',round(result['mesh']['b0M'],4),'m'),
        ('一道支撑标高（桩顶向下）',data['strut']['depth_m'],'m'),
        ('单根支撑等效水平刚度',data['strut']['stiffness_kn_m'],'kN/m')])
    doc.add_paragraph('参数逐项依据及确认状态见封存输入 JSON。项目事实或强度指标缺少来源时仅输出受限计算复核。')
    doc.add_heading('3. 开挖与施工工况',1)
    _table_docx(doc,['阶段','事件','开挖(m)','最大位移(mm)','最大弯矩(kN.m)','坑内抗力上限'],[
        (x['id'],x['event'],x['excavationDepthM'],
         round(x['uMm'],3) if x['uMm'] is not None else '未计算',
         round(x['maxAbsMomentKnM'],3) if x['maxAbsMomentKnM'] is not None else '未计算',
         '满足' if x['passiveLimitSatisfied'] else '未满足/未计算') for x in result['stages']])
    doc.add_heading('4. 控制包络与配筋需求',1)
    re=result['reinforcement']
    _table_docx(doc,['项目','值','说明'],[
        ('最大位移',str(round(result['checks']['displacement']['valueMm'],3))+'mm' if result['checks']['displacement']['valueMm'] is not None else '未计算','按当前基准模型'),
        ('设计弯矩',round(re['momentDesignKnM'],3) if re['momentDesignKnM'] is not None else '未计算','kN·m，控制阶段可追溯'),('设计剪力',round(re['shearDesignKn'],3) if re['shearDesignKn'] is not None else '未计算','kN，控制阶段可追溯'),
        ('给定纵筋',f"{re['barCount']}×Φ{re['barDiameterMm']}",'仅列输入'),
        ('给定纵筋面积',round(re['providedAreaMm2'],2),'mm²'),
        ('显式最低面积',round(re['minimumAreaMm2'],2),'mm²；取值有待核实'),
        ('外箍净保护层',round(re['outerStirrupClearCoverMm'],2),'mm，缺项目限值时不宣称通过')])
    doc.add_paragraph('控制结果位置（桩顶向下，深度单位 m）：')
    for key,title,unit in [('uMm','最大位移','mm'),('momentKnM','最大弯矩','kN·m'),('shearKn','最大剪力','kN')]:
        item=control.get(key)
        if item:
            doc.add_paragraph(f"{title}：阶段 {item['stageId']}，深度 {item['depthM']:.3f} m，"
                              f"节点侧 {'上' if item['side']=='above' else '下'}，"
                              f"带符号值 {item['value']:.3f} {unit}",style='List Bullet')
    displacement=result['checks']['displacement']
    doc.add_paragraph(f"位移控制：计算 {displacement['valueMm']:.3f} mm / 输入限值 {displacement['limitMm']:.3f} mm；"
                      +('在当前限值内' if displacement['satisfied'] else '超出输入限值')
                      + '。该限值必须由工程师按环境及适用规范核定。' if displacement['valueMm'] is not None else '位移控制未计算。')
    doc.add_paragraph('圆形桩配筋按已提供的工程模型分项检查；任一缺项或条文不适用时，不以其他局部通过替代。')
    doc.add_heading('5. 标准/基本组合与施工不利工况',1)
    actions=result.get('actionCombinations',{})
    doc.add_paragraph('标准组合用于变形，稳定性采用适用的标准作用；基本组合承载效应依据已确认的γF与γ0，且不线性放大非线性位移。')
    if actions:
        _table_docx(doc,['项目','状态或系数','控制结果'],[
            ('作用组合状态',STATUS_LABELS.get(actions.get('status'),actions.get('status')),'按当前受限方法及输入'),
            ('γF',data.get('design_effect_factor'),'承载能力效应综合分项系数'),
            ('γ0',data.get('gamma0'),'结构重要性系数'),
            ('不利工况数量',len(actions.get('constructionAdverse',[])),'均需重新逐阶段求解'),
            ('控制设计弯矩',round(re['momentDesignKnM'],3) if re['momentDesignKnM'] is not None else '未计算',
                f"来源：{re.get('governingMomentScenario','standard')}/{re.get('governingMomentStageId','未知')}"),
            ('控制设计剪力',round(re['shearDesignKn'],3) if re['shearDesignKn'] is not None else '未计算',
                f"来源：{re.get('governingShearScenario','standard')}/{re.get('governingShearStageId','未知')}")])
        for case in actions.get('constructionAdverse',[]):
            doc.add_paragraph(f"施工不利工况：{case['case']}；状态：{STATUS_LABELS.get(case['status'],case['status'])}；依据：{case.get('rationale','')}。")
    doc.add_heading('6. 稳定、地下水、支撑及圆桩专项验算',1)
    coverage=result.get('normativeCoverage',{}).get('requiredChecks',{})
    if coverage:
        _table_docx(doc,['规范与验算','状态','控制值或缺项'],[
           (CHECK_LABELS.get(name,name),STATUS_LABELS.get(item['status'],item['status']),(
              f"K={item['governingStage']['safetyFactor']:.3f}，需求={item['governingStage'].get('requiredFactor','-')}"
              if isinstance(item.get('governingStage'),dict) and item['governingStage'].get('safetyFactor') is not None
              else (item.get('reason') or item.get('note') or '具体明细见封存结果')))
           for name,item in coverage.items()])
    doc.add_paragraph('软土4.2.5、地下水附录C、支撑节点和圆桩裂缝等条件应独立满足；软件禁止由局部通过推断正式可发行。')
    doc.add_heading('7. 原项目对照与来源限制',1)
    comparison=result.get('historicalReferenceComparison')
    if comparison:
        doc.add_paragraph(f"原项目：{comparison['project']}，剖面 {comparison['reference']}。阶段曲线对照：{comparison['fullCurveComparisonStatus']}。")
        doc.add_paragraph('原报告仅提供图示及部分极值，缺原始逐点数值和同等计算模型，当前不能计算可信误差率。')
        for reason in comparison['whyNotComparable'][:6]:doc.add_paragraph(reason,style='List Bullet')
    else:
        doc.add_paragraph('当前计算尚未绑定经过核准的同条件原工程完整曲线，不生成参考误差率。')
    doc.add_heading('8. 必须完成的后续工程验算',1)
    from collections import Counter
    grouped=Counter(x['code'] for x in result['issues'])
    # Printed report is for engineer reading. The full, unabridged register
    # remains in sealed_result.json / normative_checks.json for audit.
    issue_order=['SOURCE_MISSING','SOURCE_NOT_APPROVED','ACTION_COMBINATION_REQUIRES_REVIEW',
       'STABILITY_NOT_INTEGRATED','REBAR_NOT_FULLY_VERIFIED',
       'NORMATIVE_4_2_2_EMBEDMENT','NORMATIVE_4_2_4_BASAL_HEAVE',
       'NORMATIVE_4_2_5_SOFT_SOIL_HEAVE','NORMATIVE_4_2_6_GROUNDWATER',
       'NORMATIVE_CONCRETE_CIRCULAR_CRACK_CONTROL','NORMATIVE_4_9_INTERNAL_BRACE']
    keys=sorted(grouped,key=lambda key:(issue_order.index(key) if key in issue_order else 99,key))
    for code in keys[:9]:
        count=grouped[code]
        sample=next(x for x in result['issues'] if x['code']==code)
        suffix=f'（共 {count} 项）' if count>1 else ''
        doc.add_paragraph(f"{ISSUE_LABELS.get(code,code)}{suffix}：{sample['message']}",style='List Bullet')
        if code in ('SOURCE_MISSING','SOURCE_NOT_APPROVED'):
            fields=[x['field'] for x in result['issues'] if x['code']==code]
            doc.add_paragraph('优先核对：'+ '、'.join(SOURCE_LABELS.get(x,x) for x in fields[:7]) + (' 等；完整清单见封存结果。' if len(fields)>7 else '。'))
    if len(keys)>9:
        doc.add_paragraph(f'另有 {len(keys)-9} 类问题详见封存结果、规范检查明细和 P1-P4 校审记录。')
    doc.add_paragraph('本计算书由不可变快照生成，正式施工图仍需全部稳定验算、内支撑体系、详细配筋和项目审签。')
    doc.save(path)

def _curves_pdf(result,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib import font_manager
    font_candidates=font_manager.findSystemFonts()
    chinese=next((f for f in font_candidates if 'gbsn00lp' in f),None)
    if chinese:
        font_manager.fontManager.addfont(chinese)
        plt.rcParams['font.family']=font_manager.FontProperties(fname=chinese).get_name()
    plt.rcParams['axes.unicode_minus']=False
    with PdfPages(path) as pdf:
        for attr,label,unit in [('uMm','水平位移','mm'),('momentKnM','弯矩','kN.m'),('shearKn','剪力','kN')]:
            fig,ax=plt.subplots(figsize=(8.2,6.4))
            for stage in result['stages']:
                if not stage['points']:continue
                ax.plot([p[attr] for p in stage['points']],[p['depthM'] for p in stage['points']],label=stage['id'],linewidth=1.2)
            ax.invert_yaxis();ax.grid(alpha=.25);ax.set_xlabel(label+' ('+unit+')');ax.set_ylabel('桩顶向下深度 (m)');ax.legend(loc='best',ncol=2)
            fig.suptitle('施工阶段'+label+' - '+LABEL,fontsize=12)
            fig.text(.05,.04,'快照: '+result['snapshotHash'][:16]+' 结果: '+result['resultHash'][:16],fontsize=8)
            fig.tight_layout(rect=[.03,.06,.97,.94]);pdf.savefig(fig);plt.close(fig)

def _section_pdf(result,path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A3
    from reportlab.pdfbase.pdfmetrics import registerFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase import pdfmetrics
    import os
    font='/usr/share/fonts/truetype/arphic-gbsn00lp/gbsn00lp.ttf'
    if os.path.exists(font):
        try:registerFont(TTFont('PitCn',font));family='PitCn'
        except Exception:family='Helvetica'
    else:family='Helvetica'
    c=canvas.Canvas(str(path),pagesize=(A3[1],A3[0]))
    w,h=A3[1],A3[0]
    c.setFont(family,15);c.drawString(45,h-48,'单排桩一道内支撑剖面（计算复核）')
    c.setFont(family,10);c.drawString(45,h-68,LABEL)
    pile=result['snapshot']['pile'];st=result['snapshot']['stages'][-1];depth=st['excavation_depth_m'];D=pile['diameter_m'];length=pile['length_m'];scale=72/25.4*1000/100  # paper points per model metre 1:100
    x=w*.40;y=h-135
    c.setLineWidth(2);c.rect(x-D*scale/2,y-length*scale,D*scale,length*scale)
    c.setLineWidth(.8);c.line(55,y,w-55,y)
    for layer in result['snapshot']['layers']:
        z=layer['bottom_m']
        if z<length:c.setDash(3,3);c.line(65,y-z*scale,w-70,y-z*scale);c.setDash()
    c.setStrokeColorRGB(.2,.4,.6);c.line(x+30,y-depth*scale,w-65,y-depth*scale)
    c.setStrokeColorRGB(0,0,0)
    brace=result['snapshot']['strut'];zs=brace['depth_m'];c.setLineWidth(2)
    c.line(x+D*scale/2,y-zs*scale,w-110,y-zs*scale)
    c.setFont(family,10);c.drawString(w-250,y-zs*scale+8,'水平支撑')
    c.drawString(65,y-depth*scale-15,f'最终开挖 {depth:.2f} m')
    c.drawString(65,y-length*scale-30,f'桩长 {length:.2f} m, 直径 {D*1000:.0f} mm')
    # Native-size dimension line; the graphics are printed at 1:100 on A3 paper.
    dimx=x-D*scale/2-25
    c.setLineWidth(.8);c.line(dimx,y,dimx,y-length*scale)
    for dimy in (y,y-length*scale):
        c.line(dimx-6,dimy,dimx+6,dimy)
    c.setFont(family,9);c.drawRightString(dimx-9,y-length*scale/2,f'{length*1000:.0f} mm')
    # Engineering information panel fills the remaining A3 sheet without stretching the section.
    box_left,box_bottom,box_width,box_height=55,124,w-110,254
    c.setFillColorRGB(.963,.971,.98);c.setStrokeColorRGB(.63,.68,.74)
    c.roundRect(box_left,box_bottom,box_width,box_height,6,stroke=1,fill=1)
    c.setFillColorRGB(.1,.21,.34);c.setFont(family,13)
    c.drawString(72,box_bottom+box_height-28,'设计输入与施工阶段')
    c.drawString(w*.53,box_bottom+box_height-28,'控制结果与适用边界')
    c.setStrokeColorRGB(.75,.78,.82);c.line(w*.515,box_bottom+15,w*.515,box_bottom+box_height-43)
    c.setFillColorRGB(0,0,0);c.setFont(family,9)
    c.drawString(73,box_bottom+box_height-48,f'桩径 {D*1000:.0f} mm，中心距 {pile["spacing_m"]:.2f} m，桩长 {length:.2f} m')
    c.drawString(73,box_bottom+box_height-64,f'支撑深度 {zs:.3f} m，最终开挖 {depth:.2f} m')
    c.drawString(73,box_bottom+box_height-81,'工况     施工事件                    开挖(m)   位移(mm)   弯矩(kN·m)')
    event_names={'initial':'初始','excavate':'开挖','install_strut':'安装支撑','change_water':'改变水位','change_surcharge':'改变超载'}
    for i,stage in enumerate(result['stages'][:7]):
        row_y=box_bottom+box_height-99-i*21
        c.drawString(73,row_y,str(stage['id']))
        c.drawString(120,row_y,event_names.get(stage['event'],stage['event']))
        c.drawRightString(312,row_y,f'{stage["excavationDepthM"]:.2f}')
        u=stage['uMm'];m=stage['maxAbsMomentKnM']
        c.drawRightString(385,row_y,'—' if u is None else f'{u:.2f}')
        c.drawRightString(490,row_y,'—' if m is None else f'{m:.2f}')
    rv=result['reinforcement'];ck=result['checks'];gov=result['governing']
    rx=w*.53
    c.drawString(rx,box_bottom+box_height-48,f'最大位移 {ck["displacement"]["valueMm"]:.2f} mm / 限值 {ck["displacement"]["limitMm"]:.2f} mm')
    c.drawString(rx,box_bottom+box_height-65,f'设计弯矩 {rv["momentDesignKnM"]:.2f} kN·m  (阶段 {rv["governingMomentStageId"]})')
    c.drawString(rx,box_bottom+box_height-82,f'设计剪力 {rv["shearDesignKn"]:.2f} kN  (阶段 {rv["governingShearStageId"]})')
    c.drawString(rx,box_bottom+box_height-99,f'给定纵筋 {rv["barCount"]}Φ{rv["barDiameterMm"]:.0f}；面积 {rv["providedAreaMm2"]:.0f} mm²')
    c.drawString(rx,box_bottom+box_height-116,f'最低配筋面积输入要求 {rv["minimumAreaMm2"]:.0f} mm²（待核定）')
    c.setFillColorRGB(.55,.14,.12);c.setFont(family,10)
    c.drawString(rx,box_bottom+box_height-139,'工程结论：需校审，未完成正式结构设计验算')
    c.setFillColorRGB(0,0,0);c.setFont(family,9)
    c.drawString(rx,box_bottom+box_height-160,'仍待核定：软土、地下水适用性、完整组合与')
    c.drawString(rx,box_bottom+box_height-177,'支撑节点、裂缝及配筋构造；详见分项记录。')
    c.drawString(rx,box_bottom+box_height-195,'计算结果和钢筋需求同源，建议不得视为已采用配筋。')
    c.drawString(72,box_bottom+19,'说明：显示桩体二维计算剖面；土层、水位和阶段具体输入以封存数据为准。')
    c.setFillColorRGB(0,0,0);c.setStrokeColorRGB(0,0,0)
    c.drawString(55,65,'1:100（A3 横向宽页）  |  模型：mm  |  输入: '+result['snapshotHash'][:16])
    c.drawString(55,49,'封存结果: '+result['resultHash'][:16])
    c.showPage();c.save()

def _dxf(result,path):
    import ezdxf
    doc=ezdxf.new('R2013');m=doc.modelspace();d=result['snapshot']['pile'];s=result['snapshot']['strut'];h=result['snapshot']['stages'][-1]['excavation_depth_m']
    if 'PILE' not in doc.layers:doc.layers.new('PILE',dxfattribs={'color':7})
    if 'STRUT' not in doc.layers:doc.layers.new('STRUT',dxfattribs={'color':1})
    if 'EXCAVATION' not in doc.layers:doc.layers.new('EXCAVATION',dxfattribs={'color':5})
    w=d['diameter_m']*1000;L=d['length_m']*1000
    m.add_lwpolyline([(-w/2,0),(w/2,0),(w/2,-L),(-w/2,-L)],close=True,dxfattribs={'layer':'PILE'})
    m.add_line((w/2,-s['depth_m']*1000),(7500,-s['depth_m']*1000),dxfattribs={'layer':'STRUT'})
    m.add_line((w/2,-h*1000),(9000,-h*1000),dxfattribs={'layer':'EXCAVATION'})
    m.add_text(f"1:100  PILE D={w:.0f}mm L={L:.0f}mm",dxfattribs={'height':250,'insert':(1000,500)})
    m.add_text('PRELIMINARY - NOT FOR CONSTRUCTION',dxfattribs={'height':200,'insert':(1200,-L-600)})
    m.add_text('HASH: '+result['snapshotHash'][:20],dxfattribs={'height':160,'insert':(1200,-L-1000)})
    doc.saveas(path)

def export_bundle(result,review):
    if result['analysisStatus']!='complete' or review['resultHash']!=result['resultHash']:
        raise ValueError('只允许导出同一已完成结果对应的已确认复核版')
    with TemporaryDirectory() as td:
        p=Path(td)
        _docx(result,review,p/'calculation.docx')
        _curves_pdf(result,p/'stage_envelopes.pdf')
        _section_pdf(result,p/'section_A3.pdf')
        _dxf(result,p/'section.dxf')
        (p/'frozen_input.json').write_text(json.dumps(result['snapshot'],ensure_ascii=False,indent=2),encoding='utf-8')
        (p/'sealed_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (p/'review_record.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
        (p/'normative_checks.json').write_text(json.dumps(result.get('normativeCoverage'),ensure_ascii=False,indent=2),encoding='utf-8')
        (p/'action_combinations.json').write_text(json.dumps(result.get('actionCombinations'),ensure_ascii=False,indent=2),encoding='utf-8')
        if result.get('historicalReferenceComparison'):
            (p/'historical_comparison.json').write_text(json.dumps(result['historicalReferenceComparison'],ensure_ascii=False,indent=2),encoding='utf-8')
        with (p/'normative_checks.csv').open('w',encoding='utf-8-sig',newline='') as f:
            rows=csv.writer(f);rows.writerow(['方法模块','状态','控制阶段','安全系数','要求','结果身份'])
            for name,check in result.get('normativeCoverage',{}).get('requiredChecks',{}).items():
                governing=check.get('governingStage') or {}
                rows.writerow([name,check.get('status'),governing.get('stageId'),governing.get('safetyFactor'),
                    governing.get('requiredFactor'),result['resultHash']])
        with (p/'rebar_demand.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['项目','值','单位','追溯'])
            rb=result['reinforcement']
            for row in [('设计弯矩',rb['momentDesignKnM'],'kN.m',rb['governingMomentStageId']),
                        ('设计剪力',rb['shearDesignKn'],'kN',rb['governingShearStageId']),
                        ('给定钢筋数量',rb['barCount'],'根','工程师输入'),
                        ('给定钢筋面积',rb['providedAreaMm2'],'mm2','工程师输入'),
                        ('最小配筋面积',rb['minimumAreaMm2'],'mm2','确认最小配筋率'),
                        ('圆桩N-M分项状态',result.get('normativeCoverage',{}).get('requiredChecks',{}).get('concrete_circular_pile',{}).get('status'),'状态','具体截面计算见 normative_checks.json')]:writer.writerow(row)
        files=sorted(x for x in p.iterdir() if x.is_file())
        manifest={'schemaVersion':'pitguard.one-strut-delivery/1','snapshotHash':result['snapshotHash'],
                  'resultHash':result['resultHash'],'reviewHash':review['reviewHash'],
                  'formalIssueReady':False,'files':[{ 'name':x.name,'bytes':x.stat().st_size,
                  'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in files]}
        (p/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        buf=BytesIO()
        with ZipFile(buf,'w',ZIP_DEFLATED,compresslevel=8) as z:
            for x in sorted(p.iterdir()):z.write(x,arcname=x.name)
        return buf.getvalue(),manifest
