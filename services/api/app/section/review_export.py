"""Views of sealed v2 evidence. No design values or approval are invented here."""
from __future__ import annotations

import json
from math import cos, pi, radians, sin

from .render_helper import number


CHECK_NAMES = {'axial_flexural_interaction': '轴力弯矩相互作用', 'shear': '圆截面抗剪',
    'minimum_cover': '最外层钢筋净保护层', 'bar_spacing': '纵筋弦向净距',
    'stirrup_detailing': '给定箍筋几何', 'anchorage': '直线受拉锚固长度',
    'anchorage_confinement': '锚固区横向钢筋', 'lap': '钢筋连接',
    'service_stress': '使用阶段裂后应力', 'crack_width': '裂缝宽度'}
HISTORY_NAMES = {'carry_plastic_history_v1': '继承塑性位移',
    'reset_plastic_offset_to_zero_v1': '塑性偏移清零',
    'rebase_soil_at_current_displacement_v1': '按前阶段位移重设土体参考'}
REVIEW_GLYPHS = '''原项目条件与全曲线 工程配筋分项检查 轴力弯矩相互作用 圆截面抗剪
最外层钢筋净保护层 纵筋弦向净距 给定箍筋几何 直线受拉锚固长度 锚固区横向钢筋
钢筋连接 使用阶段裂后应力 裂缝宽度 继承塑性位移 塑性偏移清零 按前阶段位移重设土体参考
各阶段两侧压力 动员压力 初始压力 主动界限 被动界限 另计水压 土体已移除
坑外侧 坑内侧 总侧压力 深度自桩顶向下 单位 几何核对 给定钢筋截面 钢筋中心圆
外包圆箍 净保护层 弦向净距 本图只表达给定钢筋几何 尚不具备节点或施工配筋依据
需求或限值 能力或实测 项目 状态 仅应力计算 数值检查未通过 超出本方法范围 需补输入 Φ
分层绘制总侧压力 两侧Gauss材料站按权重投影 层界和端点不外推
'''


def write_review_data(snapshot, result, output, reference):
    from .export import _csv, _identity, _json, IDENTITY_COLUMNS
    from .reference_curves import section_curve_bundle
    data = snapshot.payload(); identity = _identity(data, result)
    if data.get('method_extensions'):
        checks = result['engineeringChecks']
        _json(output/'engineering_checks.json', checks)
        records = []
        for check in checks['checks']:
            records.append({**identity, 'check_id': check['id'], 'name': CHECK_NAMES.get(check['id'], check['id']),
                'status': check['status'], 'method': check['method'], 'demand': check.get('demand'),
                'capacity': check.get('capacity'), 'units': check.get('units'), 'utilization': check.get('utilization'),
                'governing_case': json.dumps(check.get('governingCase'), ensure_ascii=False),
                'required_inputs': '; '.join(check['requiredInputs']),
                'scope': check['scope'], 'details': json.dumps(check['details'], ensure_ascii=False),
                'basis': json.dumps(check['basis'], ensure_ascii=False), 'limitations': '; '.join(check['limitations'])})
        _csv(output/'engineering_checks.csv', IDENTITY_COLUMNS + ['check_id', 'name', 'status', 'method',
             'demand', 'capacity', 'units', 'utilization', 'governing_case', 'required_inputs', 'scope',
             'details', 'basis', 'limitations'], records)
        records = []
        spacing = data['pile']['spacing_m']
        for stage in result['stages']:
            for soil in stage.get('soilStates') or []:
                pressure = soil['pressureKn']/(spacing*soil['weightM'])
                records.append({**identity, 'stage_id': stage['id'], 'station_id': soil['id'],
                    'node': soil['node'], 'depth_m': soil['depthM'], 'side': soil['side'], 'layer_id': soil['layerId'],
                    'weight_m': soil['weightM'], 'pressure_basis': soil['pressureBasis'],
                    'mobilized_pressure_basis_kpa': pressure, 'external_water_kpa': soil['externalWaterKpa'],
                    'mobilized_total_pressure_kpa': pressure+soil['externalWaterKpa'], 'physical_pore_pressure_kpa': soil['waterPressureKpa'],
                    'total_vertical_kpa_without_q': soil['totalVerticalKpa'], 'effective_vertical_kpa_without_q': soil['effectiveVerticalKpa'],
                    'surcharge_vertical_kpa': soil['surchargeVerticalKpa'], 'p0_kpa_in_basis': soil['p0Kpa'],
                    'active_kpa_in_basis': soil['activeKpa'], 'passive_kpa_in_basis': soil['passiveKpa'],
                    'material_force_kn': soil['pressureKn'], 'previous_plastic_displacement_m': soil['previousPlasticDisplacementM'],
                    'plastic_displacement_m': soil['plasticDisplacementM'], 'soil_history_action': stage['soilHistoryAction'],
                    'note': '固定Gauss材料站的相邻节点分权状态；同深双状态不合并冒充独立测点'})
        _csv(output/'soil_pressure_states.csv', IDENTITY_COLUMNS + ['stage_id', 'station_id', 'node', 'depth_m', 'side',
             'layer_id', 'weight_m', 'pressure_basis', 'mobilized_pressure_basis_kpa', 'external_water_kpa',
             'mobilized_total_pressure_kpa', 'physical_pore_pressure_kpa', 'total_vertical_kpa_without_q',
             'effective_vertical_kpa_without_q', 'surcharge_vertical_kpa', 'p0_kpa_in_basis', 'active_kpa_in_basis',
             'passive_kpa_in_basis', 'material_force_kn', 'previous_plastic_displacement_m', 'plastic_displacement_m',
             'soil_history_action', 'note'], records)
        _json(output/'section_curves.json', section_curve_bundle(snapshot, result))
    if reference:
        _json(output/'reference_supplement.json', reference)
        _json(output/'reference_contract.json', reference['contract'])
        if reference['referenceCurves'] is not None:
            _json(output/'reference_curves.json', reference['referenceCurves'])
        if reference['comparison'] is not None:
            _json(output/'reference_comparison.json', reference['comparison'])
        _csv(output/'reference_parameters.csv', ['contract_hash', 'parameter_id', 'identity', 'original_value',
             'value', 'unit', 'status', 'requires_confirmation', 'derivation', 'sources', 'confirmation', 'input_path'],
             [{'contract_hash': reference['contract']['contractHash'], 'parameter_id': p['id'], 'identity': p['identity'],
               'original_value': json.dumps(p['originalValue'], ensure_ascii=False), 'value': json.dumps(p['value'], ensure_ascii=False),
               'unit': p['unit'], 'status': p['status'], 'requires_confirmation': p['requiresConfirmation'],
               'derivation': json.dumps(p['derivation'], ensure_ascii=False), 'sources': json.dumps(p['sourceRefs'], ensure_ascii=False),
               'confirmation': json.dumps(p['confirmation'], ensure_ascii=False), 'input_path': p['inputPath']}
              for p in reference['contract']['parameters']])


def append_engineering_document(doc, data, result, table, state):
    engineering = result.get('engineeringChecks')
    if engineering is None:
        return
    doc.add_heading('工程配筋分项检查', level=2)
    doc.add_paragraph('以下检查使用本冻结输入中的工程条件及本结果全部阶段作用。前表为N=0纯弯需求；各阶段显式轴力、使用组合和构造判据在本节分别处理。局部检查通过不改变整体待复核状态。')
    def summary_row(c):
        demand, actual, units = c.get('demand'), c.get('capacity'), c.get('units')
        details = c.get('details') or {}
        if c['id'] in {'stirrup_detailing', 'anchorage_confinement'} and all(k in details for k in (
                'minimumDiameterMm', 'maximumSpacingMm', 'providedDiameterMm', 'providedSpacingMm')):
            demand = f"直径≥{number(details['minimumDiameterMm'])}\n间距≤{number(details['maximumSpacingMm'])}"
            actual = f"Φ{number(details['providedDiameterMm'])}@{number(details['providedSpacingMm'])}"
        elif c['id']=='lap' and details.get('arrangement')=='continuous_no_lap':
            demand, actual, units = '显式接头安排', '全长连续无搭接', '—'
        elif c['id']=='service_stress' and details.get('cases'):
            demand = f"使用组合\n{len({r['stageId'] for r in details['cases']})}/{len(details.get('requiredStageIds') or [])}阶段"
            actual, units = '应力见下表', 'MPa'
        elif c['id']=='crack_width' and details.get('declaredLimitMm') is not None:
            demand, actual, units = details['declaredLimitMm'], '尚未计算', 'mm'
        return [CHECK_NAMES.get(c['id'], c['id']), demand, actual, units, state(c['status'])]
    table(doc, ['检查项目', '需求或限值', '计算或给定', '单位', '状态'], [
        summary_row(c)
        for c in engineering['checks']], [2.05, 1.2, 1.2, .65, 2.0])
    checks = {c['id']: c for c in engineering['checks']}
    nm = checks.get('axial_flexural_interaction', {})
    if nm.get('details', {}).get('cases'):
        doc.add_heading('同阶段轴力与正负弯矩', level=2)
        table(doc, ['阶段与位置 m', 'N设计 kN', 'M设计 kN·m', '弯矩容量 kN·m', '状态'], [
            [f"{r['stageId']} @ {number(r['depthM'])} {r.get('side') or ''}", r['axialForceDesignKn'],
             r['momentDesignKnM'], r.get('capacityKnM'), state(r['status'])] for r in nm['details']['cases']],
             [1.9, 1.2, 1.2, 1.4, 1.4])
        doc.add_paragraph('圆截面逐条钢筋与混凝土积分按N平衡求中和轴；每个需求取自该轴力所属阶段的正、负弯矩极值。高轴压、全截面受压或受拉、构件二阶与稳定问题需使用适用方法另算。')
    shear = checks.get('shear', {}).get('details', {})
    if 'equivalentWidthMm' in shear:
        doc.add_paragraph('圆截面抗剪按明确适用域采用 b=1.76r、h0=1.6r。'
            f"本例 b={number(shear['equivalentWidthMm'])} mm，h0={number(shear['equivalentDepthMm'])} mm；"
            f"混凝土项{number(shear['concreteContributionKn'])} kN，箍筋项{number(shear['stirrupContributionKn'])} kN，"
            f"截面压碎上限{number(shear['sectionLimitKn'])} kN。给定有效箍筋面积{number(shear['providedEffectiveHoopAreaMm2'])} mm²。")
    cover = checks.get('minimum_cover', {}).get('details', {})
    if cover:
        doc.add_paragraph(f"纵筋轴线距边{number(cover.get('barAxisToSurfaceMm'))} mm，纵筋净保护层{number(cover.get('longitudinalClearCoverMm'))} mm，"
                          f"外包箍筋净保护层{number(cover.get('outerHoopClearCoverMm'))} mm。判据来源于显式项目输入，不由程序自动选择环境或施工类别。")
    anchor = checks.get('anchorage', {}).get('details', {})
    if 'basicLengthMm' in anchor:
        doc.add_paragraph(f"直锚基本长度 lab=0.14 fy d/ft={number(anchor['basicLengthMm'])} mm；"
                          f"按已声明修正系数与200 mm下限，需{number(anchor['requiredLengthMm'])} mm。ft取锚固区混凝土；可用长度由锚固起算截面起量取。")
        table(doc, ['锚固端', '需求 mm', '可用 mm', '状态'], [
            ['上端' if r['end']=='top' else '下端', r['requiredLengthMm'], r['providedLengthMm'], state(r['status'])]
            for r in anchor['ends']], [1.5, 1.8, 1.8, 2.0])
    conf = checks.get('anchorage_confinement', {}).get('details', {})
    if conf.get('confinementRequiredBy5dCondition'):
        doc.add_paragraph(f"显式锚固区横向钢筋：直径{number(conf['providedDiameterMm'])} mm，间距{number(conf['providedSpacingMm'])} mm；"
                          f"本项适用规则要求直径不小于{number(conf['minimumDiameterMm'])} mm，间距不大于{number(conf['maximumSpacingMm'])} mm。该条件与全桩抗剪分别判定。")
    service = checks.get('service_stress', {}).get('details', {})
    if service.get('cases'):
        doc.add_heading('使用组合与裂后截面应力', level=2)
        table(doc, ['阶段', '使用弯矩 kN·m', '使用轴力 kN', '最大受拉钢筋应力 MPa', '状态'], [
            [r['stageId'], r['momentServiceKnM'], r['axialForceServiceKn'], r.get('maximumTensileSteelStressMpa'), state(r['status'])]
            for r in service['cases']], [1.0, 1.45, 1.25, 1.9, 1.5])
    doc.add_paragraph('裂缝宽度保持未验证：目前尚未建立有适用依据的周边均布钢筋圆桩裂缝宽度模型。裂后弹性钢筋应力只是中间结果；不能据此签认裂缝限值。')
    if engineering['requiredInputs']:
        doc.add_paragraph('仍需补充字段：'+'；'.join(engineering['requiredInputs']))
    sources = {}
    for c in engineering['checks']:
        for source in c.get('basis') or []:
            if isinstance(source, dict) and source.get('url'):
                sources[source['url']] = source
    for url, source in sources.items():
        doc.add_paragraph(f"方法依据：{source.get('standard', '')} {source.get('clause', '')}；{url}")


def append_reference_document(doc, reference, table, state):
    if reference is None:
        return
    contract = reference['contract']; assessment = reference['assessment']
    doc.add_heading('原项目条件与全曲线', level=2)
    doc.add_paragraph(f"条件合同{contract['contractHash'][:16]}，与结果{reference['resultHash'][:16]}关联。"
        f"记录{len(contract['parameters'])}项参数：原文事实{assessment['counts'].get('original_fact', 0)}项、"
        f"工程解释{assessment['counts'].get('derived_interpretation', 0)}项、未确认{assessment['counts'].get('unconfirmed', 0)}项。"
        '完整原值、推导、来源页码及确认记录在reference_parameters.csv和reference_contract.json。')
    fields = {p['id']: p for p in contract['parameters']}
    selected = [p for p in contract['parameters'] if p['id'].startswith('datum.local_') or p['id']=='datum.wall_top_offset_m']
    if selected:
        table(doc, ['坐标项目', '原文或推导值', '证据状态'], [
            [p['identity'], json.dumps(p['value'], ensure_ascii=False),
             {'original_fact': '原文事实', 'derived_interpretation': '工程解释', 'unconfirmed': '未确认'}[p['status']]
             + ('；需确认' if p['requiresConfirmation'] else '')]
            for p in selected], [3.6, 1.5, 2.0])
    doc.add_paragraph('坐标值与证据状态以上表及当前条件记录为准，不以其他项目或历史版本的转换代替。水位高于桩顶时局部深度可为负值，不得截为零；不得裁去上部土层后遗漏覆土、坡台或水头。')
    if assessment['missingConditions']:
        table(doc, ['关键待确认条件', '原因'], [
            [fields[r['id']]['identity'], '缺原项目参数或选项' if r['reason']=='original_condition_not_provided' else '工程解释尚无来源确认']
            for r in assessment['missingConditions']], [4.7, 2.4])
    original = reference.get('referenceCurves')
    has_samples = bool(original and any(c['samples'] for s in original['stages'] for c in s['curves']))
    if not has_samples:
        doc.add_paragraph('尚未取得原程序节点曲线。当前空模板不包含任何补造点值，不能计算原项目曲线误差或证明同条件复现。需逐阶段提供坑内外压力、位移、弯矩、剪力、支撑水平反力及安装参考位移，并确认设计系数与配筋需求口径。')
    comparison = reference.get('comparison')
    if comparison:
        report = comparison['report']
        doc.add_paragraph(f"比较记录{comparison['comparisonHash'][:16]}：{state(report['status'])}；"
                          f"同条件已确认：{'是' if report['sameConditionsVerified'] else '否'}。每个数值通道的覆盖和差异分别保存，资料缺失不判为零误差。")
        if has_samples:
            table(doc, ['阶段与曲线', '状态', '覆盖比例', '最大绝对差'], [
                [r['stageId']+' '+r['quantity'], state(r['status']), r.get('coverageFraction'), r.get('maxAbsoluteError')]
                for r in report['curves']], [2.1, 2.2, 1.4, 1.4])
    doc.add_paragraph('压力采用固定Gauss站的相邻节点加权投影；未编造层界和墙端点值。原程序采用不同输出位置或投影时须先建立有依据的对应关系；位移弯矩剪力的完整性与压力投影完整性分开报告。')


def export_pressure_pages(data, result, font, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.font_manager import FontProperties
    prop = FontProperties(fname=str(font.path), size=10)
    spacing = data['pile']['spacing_m']; length=data['pile']['length_m']
    colors={'actual':'#155E75','p0':'#64748B','pa':'#B45309','pp':'#7C3AED'}
    with PdfPages(output/'stage_pressures.pdf') as pdf:
        for stage in result['stages']:
            if stage['status']!='success':
                continue
            fig, axes = plt.subplots(1, 2, figsize=(11.69, 8.27), sharey=True)
            fig.subplots_adjust(top=.86, bottom=.13, left=.09, right=.96, wspace=.22)
            for axis, side, label in zip(axes, ('outside','inside'), ('坑外侧','坑内侧')):
                grouped={}
                for soil in stage['soilStates']:
                    if soil['side']!=side:continue
                    key=(soil['layerId'], soil['depthM'])
                    g=grouped.setdefault(key, {'w':0., 'actual':0., 'p0':0., 'pa':0., 'pp':0.})
                    w=soil['weightM'];water=soil['externalWaterKpa'];g['w']+=w
                    g['actual']+=soil['pressureKn']/spacing+water*w
                    for name, field in (('p0','p0Kpa'),('pa','activeKpa'),('pp','passiveKpa')):
                        g[name]+=(soil[field]+water)*w
                for layer_index, layer in enumerate(data['soil_layers']):
                    entries=sorted((z,g) for (lid,z),g in grouped.items() if lid==layer['id'])
                    if not entries:continue
                    for field,title in (('actual','动员压力'),('p0','初始压力'),('pa','主动界限'),('pp','被动界限')):
                        axis.plot([g[field]/g['w'] for z,g in entries], [z for z,g in entries],
                                  color=colors[field], lw=1.8 if field=='actual' else 1.,
                                  linestyle='-' if field=='actual' else '--', label=title if layer_index==0 else None)
                axis.set_title(label, fontproperties=prop, pad=12)
                axis.set_xlabel('总侧压力  kPa', fontproperties=prop)
                axis.grid(alpha=.2);axis.set_ylim(length, 0)
                axis.axhline(stage['excavationDepthM'], color='#94A3B8', lw=.8)
                if side=='inside' and stage['excavationDepthM']>0:
                    axis.axhspan(0,stage['excavationDepthM'],color='#F1F5F9')
                    axis.text(.5, .96, '土体已移除',transform=axis.transAxes,ha='center',va='top',fontproperties=prop)
                handles, labels = axis.get_legend_handles_labels()
                if not handles and grouped:
                    # When all of the first soil layer has been excavated,
                    # use a representative line for each defined pressure.
                    from matplotlib.lines import Line2D
                    handles=[Line2D([],[],color=colors[k],ls='-' if k=='actual' else '--') for k in colors]
                    labels=['动员压力','初始压力','主动界限','被动界限']
                axis.legend(handles,labels,prop=prop,loc='lower right',frameon=False)
            axes[0].set_ylabel('深度自桩顶向下  m',fontproperties=prop)
            fig.suptitle(f"{stage['id']} {stage['name']} 各阶段两侧压力",fontproperties=prop,fontsize=16,y=.96)
            fig.text(.5,.905,HISTORY_NAMES[stage['soilHistoryAction']],ha='center',fontproperties=prop)
            fig.text(.09,.06,'分层绘制总侧压力；两侧Gauss材料站按权重投影，层界和端点不外推。',fontproperties=prop,fontsize=9)
            fig.text(.09,.025,f"snapshot {result['snapshotHash'][:20]}  result {result['resultHash'][:20]}",fontsize=8)
            pdf.savefig(fig);plt.close(fig)


def export_rebar_geometry(data, result, font, output):
    """A dimensioned view of given bars only; never invent anchorage or node geometry."""
    import ezdxf
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    r=data['reinforcement'];design=data.get('engineering_design') or {};detail=design.get('detailing') or {}
    if r['bar_count'] is None or detail.get('hoop_arrangement')!='external_tangent_closed_circular' or r['stirrup_diameter_mm'] is None:
        return False
    radius=data['pile']['diameter_m']*500;bar_radius=r['bar_diameter_mm']/2
    rs=radius-r['bar_axis_cover_mm'];hoop=r['stirrup_diameter_mm']
    centers=[(rs*cos(radians(r['bar_start_angle_deg'])+i*2*pi/r['bar_count']),
              rs*sin(radians(r['bar_start_angle_deg'])+i*2*pi/r['bar_count'])) for i in range(r['bar_count'])]
    clear=radius-rs-bar_radius-hoop;spacing=2*rs*sin(pi/r['bar_count'])-2*bar_radius
    drawing=ezdxf.new('R2018');drawing.header['$INSUNITS']=4
    for name,color in [('PILE_OUTLINE',7),('REBAR_LONGITUDINAL',1),('REBAR_HOOP',4),('REBAR_DIMS',7)]:
        drawing.layers.new(name,dxfattribs={'color':color})
    model=drawing.modelspace();model.add_circle((0,0),radius,dxfattribs={'layer':'PILE_OUTLINE'})
    for x,y in centers:model.add_circle((x,y),bar_radius,dxfattribs={'layer':'REBAR_LONGITUDINAL'})
    for h in (rs+bar_radius,rs+bar_radius+hoop):model.add_circle((0,0),h,dxfattribs={'layer':'REBAR_HOOP'})
    model.add_diameter_dim(center=(0,0),radius=radius,angle=135,dxfattribs={'layer':'REBAR_DIMS'},
                           override={'dimtxt':20,'dimasz':15}).render()
    model.add_text(f"GIVEN CAGE ONLY - D={radius*2:g}  {r['bar_count']}x{bar_radius*2:g}  cover={clear:g} mm",
                   dxfattribs={'height':22,'insert':(-radius,-radius-70)})
    model.add_text(f"snapshot {result['snapshotHash']} result {result['resultHash']}",
                   dxfattribs={'height':9,'insert':(-radius,-radius-110)})
    drawing.saveas(output/'rebar_geometry.dxf')
    canvas=Canvas(str(output/'rebar_geometry.pdf'),pagesize=(297*mm,210*mm))
    canvas.setTitle('给定钢筋截面几何核对')
    scale=5 if radius*2<=700 else 10 if radius*2<=1500 else 20
    x0,y0=91*mm,112*mm;unit=mm/scale
    canvas.setFont(font.reportlab_name,15);canvas.drawString(18*mm,190*mm,'给定钢筋截面几何核对')
    canvas.setStrokeColorRGB(.1,.15,.2);canvas.setLineWidth(.7)
    canvas.circle(x0,y0,radius*unit)
    canvas.setStrokeColorRGB(.12,.45,.55)
    for h in (rs+bar_radius,rs+bar_radius+hoop):canvas.circle(x0,y0,h*unit)
    canvas.setFillColorRGB(.65,.18,.14)
    for x,y in centers:canvas.circle(x0+x*unit,y0+y*unit,bar_radius*unit,stroke=1,fill=1)
    canvas.setFillColorRGB(0,0,0);canvas.setFont(font.reportlab_name,10)
    items=[f"桩径 {radius*2:g} mm",f"给定纵筋 {r['bar_count']} 根 Φ{bar_radius*2:g}",
           f"外包圆箍 Φ{hoop:g}@{r['stirrup_spacing_mm']:g}", f"纵筋轴线距边 {r['bar_axis_cover_mm']:g} mm",
           f"外箍净保护层 {number(clear)} mm",f"纵筋弦向净距 {number(spacing)} mm",f"比例 1:{scale}  打印100%"]
    for i,line in enumerate(items):canvas.drawString(178*mm,(159-11*i)*mm,line)
    canvas.setStrokeColorRGB(.1,.15,.2);left=x0-radius*unit;right=x0+radius*unit;dy=y0-radius*unit-8*mm
    canvas.line(left,dy,right,dy);canvas.line(left,dy-2*mm,left,dy+2*mm);canvas.line(right,dy-2*mm,right,dy+2*mm)
    canvas.drawCentredString(x0,dy-5*mm,f"D = {radius*2:g} mm")
    canvas.setFont(font.reportlab_name,9);canvas.drawString(18*mm,20*mm,'本图只表达给定钢筋几何，尚不具备节点或施工配筋依据。')
    canvas.setFont(font.reportlab_name,7);canvas.drawString(18*mm,11*mm,f"snapshot {result['snapshotHash'][:24]}  result {result['resultHash'][:24]}")
    canvas.save();return True
