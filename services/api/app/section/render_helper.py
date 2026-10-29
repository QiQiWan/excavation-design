"""Rendering primitives for the frozen section package, without Project dependencies."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
from typing import Any


STATIC_TEXT = '''剖面验证成果 输入几何 计算完成 求解失败 未完成工程验证 来源见计算书
单桩排桩模型 标高 尺寸 单位 比例 图号 桩径 桩距 桩长 桩顶 桩底 地面 坑内 坑外
开挖面 水位 支点 激活 拆除 弹性 刚性 计算约束符号 非实际支撑跨长 土层 深度
阶段 工况 事件 初始 开挖 安装 移除 荷载 已冻结 详见计算书与数据文件
弯矩 剪力 位移 标准特征内力 设计内力 正负包络 标准位移不乘效应系数
设计位移未单独定义 标准位移见第一页 每桩 来源 快照 结果 分析 数据完整
输入来源 工程状态 软件版本 未验证 抗剪 裂缝 稳定性 构造 尚未计算
支点反力 正值向坑内 土反力有效宽度 水平竖向等比例 显示范围按桩长裁切
弹塑性地基梁 圆形截面 二维 独立验证 输入来源和完整阶段表见计算书
本图仅表达冻结输入与分析结果 不作为正式施工图 下部图层按显示范围裁切
不生成设计位移曲线 参考标准位移页 图形完整性未验证 文档 第页
细线为阶段曲线 粗线为正负包络 阶段名称和完整来源见计算书
工程结论待复核 完整哈希和来源见 其余支点的完整参数见计算书 仅输入几何
0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz πΔγφρσ εηκΣ∫±·²³⁴×≤≥∞−—：，。；（）/=_-+.,:;[]{}% '" #
'''


def number(value: Any, digits: int = 3) -> str:
    if value is None:
        return '未提供'
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            return '无有效值'
        formatted = f'{float(value):,.{digits}f}'
        return formatted.rstrip('0').rstrip('.') if digits else formatted
    return str(value)


def resolve_cjk_font() -> Path:
    configured = os.getenv('PITGUARD_CJK_FONT_PATH')
    # The font path can be supplied by the deployment environment. Release
    # archives do not redistribute standalone font files; the existing local
    # installation may already have an approved CJK font available.
    candidates = [Path(__file__).parent/'assets'/'NotoSansCJKsc-Regular.otf']
    if configured:
        candidates.append(configured)
    candidates += [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
        '/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simsun.ttc',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ValueError('中文成果需要可用的中文字体。请设置 PITGUARD_CJK_FONT_PATH 为 Noto CJK 或中文 TTF/TTC/OTF 字体路径。')


@dataclass(frozen=True)
class RenderFont:
    path: Path
    reportlab_name: str
    source_name: str
    source_sha256: str
    family_name: str


def prepare_render_font(directory: Path, text: str) -> RenderFont:
    """Embed a Unicode TrueType subset, including when the source has CFF outlines.

    ReportLab cannot embed CFF OTF. FontTools converts only the used outlines to
    quadratic curves at a maximum error of one font unit. The intermediate font
    stays outside the deliverable; all resulting PDFs embed their own subset.
    """
    from fontTools import subset
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as ReportLabFont

    source_path = resolve_cjk_font()
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = TTFont(str(source_path), fontNumber=0)
    cmap = source.getBestCmap()
    # Control characters are layout, not glyphs. Fail visibly for missing CJK.
    requested = {ord(c) for c in STATIC_TEXT + text if not c.isspace()}
    missing_cjk = [chr(c) for c in sorted(requested) if 0x3400 <= c <= 0x9fff and c not in cmap]
    if missing_cjk:
        raise ValueError('配置字体缺少成果中的中文字形：' + ''.join(missing_cjk[:20]))
    family = source['name'].getDebugName(1) or 'Noto Sans CJK SC'
    original_notices = {key: source['name'].getDebugName(name_id) for key, name_id in
                        [('copyright', 0), ('licenseDescription', 13), ('licenseInfoURL', 14)]}
    subsetter = subset.Subsetter(options=subset.Options())
    subsetter.populate(unicodes=requested | set(range(32, 127)))
    subsetter.subset(source)
    if 'CFF ' in source or 'CFF2' in source:
        glyph_set = source.getGlyphSet()
        glyphs = {}
        for name in source.getGlyphOrder():
            pen = TTGlyphPen(glyph_set)
            glyph_set[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
            glyphs[name] = pen.glyph()
        builder = FontBuilder(source['head'].unitsPerEm, isTTF=True)
        builder.setupGlyphOrder(source.getGlyphOrder())
        builder.setupCharacterMap(source.getBestCmap())
        builder.setupGlyf(glyphs)
        builder.setupHorizontalMetrics(source['hmtx'].metrics)
        builder.setupHorizontalHeader(ascent=source['hhea'].ascent, descent=source['hhea'].descent)
        os2 = source['OS/2']
        builder.setupOS2(sTypoAscender=os2.sTypoAscender, sTypoDescender=os2.sTypoDescender,
                        usWinAscent=os2.usWinAscent, usWinDescent=os2.usWinDescent, fsType=os2.fsType)
        names = {'familyName': 'PitGuard Section Subset', 'styleName': 'Regular',
                 'uniqueFontIdentifier': 'PitGuardSectionSubset',
                 'fullName': 'PitGuard Section Subset', 'psName': 'PitGuardSectionSubset'}
        for key, value in original_notices.items():
            if value:
                names[key] = value
        builder.setupNameTable(names)
        builder.setupPost()
        builder.setupMaxp()
        converted = builder.font
    else:
        converted = source
    directory.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256((source_hash + ''.join(map(chr, sorted(requested)))).encode()).hexdigest()[:16]
    path = directory / f'section-{identity}.ttf'
    converted.save(str(path))
    registered_name = f'SectionCJK{identity}'
    pdfmetrics.registerFont(ReportLabFont(registered_name, str(path)))
    return RenderFont(path, registered_name, source_path.name, source_hash, family)


def build_scene(data: dict, result: dict) -> dict:
    """One model-space scene shared by the PDF and editable DXF renderers."""
    pile, datum = data['pile'], data['datum']
    length, diameter, top = float(pile['length_m']), float(pile['diameter_m']), float(datum['wall_top_elevation_m'])
    scale = next(n for n in (100, 150, 200, 250, 300, 400, 500, 750, 1000)
                 if length * 1000 / n + 30 <= 195)
    left, right = -0.075 * scale, 0.075 * scale
    final_stage = data['stages'][-1]
    excavation = float(final_stage['excavation_depth_m'])
    lines, fills, labels = [], [], []

    def line(layer, x1, z1, x2, z2, color='#555555', dashed=False):
        lines.append({'layer': layer, 'points': [(x1, z1), (x2, z2)], 'color': color, 'dashed': dashed})

    for index, layer in enumerate(data['soil_layers']):
        a, b = max(0.0, float(layer['top_m'])), min(length, float(layer['bottom_m']))
        if a >= b:
            continue
        color = '#f3efe4' if index % 2 == 0 else '#e8edf0'
        fills.append({'layer': 'SV_SOIL', 'points': [(left, top-a), (-diameter/2, top-a),
                    (-diameter/2, top-b), (left, top-b)], 'color': color})
        if b > excavation:
            ai = max(a, excavation)
            fills.append({'layer': 'SV_SOIL', 'points': [(diameter/2, top-ai), (right, top-ai),
                        (right, top-b), (diameter/2, top-b)], 'color': color})
        line('SV_SOIL', left, top-b, -diameter/2, top-b, '#889296', True)
        if b > excavation:
            line('SV_SOIL', diameter/2, top-b, right, top-b, '#889296', True)
        labels.append({'x': left + .004*scale, 'z': top-(a+b)/2,
                       'text': f"{layer['id']} {layer['name']}", 'size': 8, 'anchor': 'left'})
    line('SV_GROUND', left, top, -diameter/2, top, '#30383d')
    line('SV_EXCAVATION', diameter/2, top-excavation, right, top-excavation, '#94562b')
    used_depths = set()
    for stage in data['stages']:
        depth = float(stage['excavation_depth_m'])
        if 0 < depth < excavation and depth not in used_depths:
            line('SV_STAGE', diameter/2, top-depth, right, top-depth, '#a0a0a0', True)
            labels.append({'x': right, 'z': top-depth+.0025*scale, 'text': stage['id'], 'size': 7.5, 'anchor': 'right'})
            used_depths.add(depth)
    for key, xa, xb, layer in [('water_outside_depth_m', left, -diameter/2, 'SV_WATER_OUT'),
                              ('water_inside_depth_m', diameter/2, right, 'SV_WATER_IN')]:
        depth = float(final_stage[key])
        if depth <= length:
            line(layer, xa, top-depth, xb, top-depth, '#217da8', True)
    active = set()
    for stage in data['stages']:
        active.update(stage['install_support_ids'])
        active.difference_update(stage['remove_support_ids'])
    supports = []
    for index, support in enumerate(data['supports']):
        z, x = top-float(support['depth_m']), diameter/2
        live = support['id'] in active
        color = '#244c70' if live else '#aaa9a4'
        end = x + .022*scale
        line('SV_SUPPORT', x, z, end, z, color, not live)
        line('SV_SUPPORT', end, z-.003*scale, end, z+.003*scale, color)
        label = f"P{index+1:02d}"
        labels.append({'x': end+.002*scale, 'z': z, 'text': label, 'size': 8, 'anchor': 'left'})
        supports.append({'label': label, **support, 'active': live})
    labels += [
        {'x': left+.02*scale, 'z': top+.009*scale, 'text': '坑外', 'size': 10, 'anchor': 'left'},
        {'x': right-.02*scale, 'z': top+.009*scale, 'text': '坑内', 'size': 10, 'anchor': 'right'},
        {'x': right, 'z': top-excavation+.003*scale, 'text': f'开挖面 {top-excavation:.3f} m', 'size': 8, 'anchor': 'right'},
        {'x': diameter/2+.003*scale, 'z': top+.002*scale, 'text': f'桩顶 {top:.3f} m', 'size': 8, 'anchor': 'left'},
        {'x': diameter/2+.003*scale, 'z': top-length-.005*scale, 'text': f'桩底 {top-length:.3f} m', 'size': 8, 'anchor': 'left'},
    ]
    return {'scale': scale, 'topM': top, 'lengthM': length, 'diameterM': diameter,
            'datum': datum['label'], 'stage': final_stage, 'supports': supports,
            'viewportMm': (15.0, 58.0, 230.0, 195.0), 'modelCenterM': (0.0, top-length/2),
            'pile': [(-diameter/2, top), (diameter/2, top), (diameter/2, top-length), (-diameter/2, top-length)],
            'lines': lines, 'fills': fills, 'labels': labels,
            'dimensions': [{'p1': (0.0, top), 'p2': (0.0, top-length), 'base': (-.045*scale, top-length/2), 'angle': 90},
                           {'p1': (-diameter/2, top), 'p2': (diameter/2, top), 'base': (0.0, top+.008*scale), 'angle': 0}]}


def export_dxf(data: dict, result: dict, scene: dict, path: Path) -> None:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new('R2018', setup=True)
    doc.units = units.MM
    doc.header['$INSUNITS'] = units.MM
    doc.header['$MEASUREMENT'] = 1
    doc.styles.new('PIT_CN', dxfattribs={'font': 'simsun.ttf'})
    scale = scene['scale']
    layers = ['SV_PILE', 'SV_SOIL', 'SV_GROUND', 'SV_EXCAVATION', 'SV_STAGE', 'SV_WATER_OUT',
              'SV_WATER_IN', 'SV_SUPPORT', 'SV_DIM', 'SV_TEXT', 'SV_FRAME', 'SV_VIEWPORT']
    for layer in layers:
        doc.layers.new(layer, dxfattribs={'color': 5 if 'WATER' in layer else 7})
    msp = doc.modelspace()
    mmpt = lambda pt: (pt[0]*1000, pt[1]*1000)
    msp.add_lwpolyline([mmpt(p) for p in scene['pile']], close=True, dxfattribs={'layer': 'SV_PILE'})
    for item in scene['lines']:
        attrs = {'layer': item['layer']}
        if item['dashed'] and 'DASHED' in doc.linetypes:
            attrs['linetype'] = 'DASHED'
            attrs['ltscale'] = float(scale)
        msp.add_line(mmpt(item['points'][0]), mmpt(item['points'][1]), dxfattribs=attrs)
    for label in scene['labels']:
        entity = msp.add_text(label['text'], height=label['size']*25.4/72*scale,
                              dxfattribs={'layer': 'SV_TEXT', 'style': 'PIT_CN'})
        align = TextEntityAlignment.RIGHT if label['anchor'] == 'right' else TextEntityAlignment.LEFT
        entity.set_placement(mmpt((label['x'], label['z'])), align=align)
    for dimension in scene['dimensions']:
        dim = msp.add_linear_dim(base=mmpt(dimension['base']), p1=mmpt(dimension['p1']),
            p2=mmpt(dimension['p2']), angle=dimension['angle'], dimstyle='EZDXF',
            override={'dimscale': float(scale), 'dimtxt': 2.5, 'dimasz': 2.0, 'dimexo': 1.0,
                      'dimexe': 1.5, 'dimdec': 0, 'dimtxsty': 'PIT_CN'}, dxfattribs={'layer': 'SV_DIM'})
        dim.render()
    psp = doc.layouts.new('A3-SECTION')
    psp.page_setup(size=(420, 297), margins=(0, 0, 0, 0), units='mm', scale=(1, 1), name='A3')
    psp.add_lwpolyline([(10, 10), (410, 10), (410, 287), (10, 287)], close=True, dxfattribs={'layer': 'SV_FRAME'})
    x, y, w, h = scene['viewportMm']
    vp = psp.add_viewport(center=(x+w/2, y+h/2), size=(w, h),
        view_center_point=mmpt(scene['modelCenterM']), view_height=h*scale, dxfattribs={'layer': 'SV_VIEWPORT'})
    vp.dxf.status = 1
    vp.dxf.flags = int(vp.dxf.flags or 0) | 16384

    def text(x, y, value, height=3):
        psp.add_text(str(value), height=height, dxfattribs={'layer': 'SV_TEXT', 'style': 'PIT_CN'}).set_placement((x, y))
    text(17, 276, data['name'] + ' 剖面验证', 4.5)
    text(17, 266, '未完成工程验证  本图不作为正式施工图', 3)
    text(260, 252, f"水平竖向比例 1:{scale}", 3.5)
    text(260, 244, '尺寸 mm   标高 m   单桩模型')
    text(260, 234, f"桩径 {scene['diameterM']*1000:g} mm  桩距 {data['pile']['spacing_m']:g} m")
    text(260, 225, f"桩长 {scene['lengthM']:g} m  阶段 {scene['stage']['id']}")
    text(260, 215, f"基准 {str(scene['datum'])[:32]}")
    for index, support in enumerate(scene['supports'][:10]):
        text(260, 199-index*8, f"{support['label']} {support['id'][:20]}  深 {support['depth_m']:g} m  {'激活' if support['active'] else '拆除'}", 2.5)
    text(17, 42, '支点为计算约束符号，非实际支撑跨长；完整阶段、来源和参数见计算书。', 2.7)
    text(17, 31, f"Snapshot {result['snapshotHash'][:16]}   Result {result['resultHash'][:16]}", 2.7)
    text(17, 20, f"SV-01   {data['section_id']}   1:{scale}   A3   未完成工程验证", 2.7)
    doc.header['$PROJECTNAME'] = data['name']
    doc.saveas(path)


def _wrapped(c, font: str, value: str, x: float, y: float, width: float, size=8.5, leading=12) -> float:
    from reportlab.pdfbase import pdfmetrics
    c.setFont(font, size)
    for paragraph in str(value).splitlines() or ['']:
        line = ''
        for char in paragraph:
            if line and pdfmetrics.stringWidth(line+char, font, size) > width:
                c.drawString(x, y, line)
                y -= leading
                line = char
            else:
                line += char
        c.drawString(x, y, line)
        y -= leading
    return y


def export_section_pdf(data: dict, result: dict, scene: dict, font: RenderFont, path: Path) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(str(path), pagesize=(420*mm, 297*mm), pageCompression=1, invariant=1)
    c.setTitle(data['name'] + ' 剖面验证')
    c.setSubject(f"snapshot={result['snapshotHash']} result={result['resultHash']}")
    c.setCreator('PitGuard frozen section export')
    c.setLineWidth(.5)
    c.rect(10*mm, 10*mm, 400*mm, 277*mm)
    c.setFont(font.reportlab_name, 15)
    c.drawString(17*mm, 276*mm, data['name'] + ' 剖面验证')
    c.setFillColor(HexColor('#8b3f2e'))
    c.setFont(font.reportlab_name, 9)
    c.drawString(17*mm, 267*mm, '未完成工程验证  本图仅表达冻结输入与分析结果，不作为正式施工图')
    c.setFillColor(HexColor('#17232b'))
    x0, y0, w, h = scene['viewportMm']
    cx, cz = scene['modelCenterM']
    scale = scene['scale']

    def point(x, z):
        return ((x0+w/2 + (x-cx)*1000/scale)*mm, (y0+h/2+(z-cz)*1000/scale)*mm)

    def polygon(points, fill, stroke=False):
        p = c.beginPath()
        p.moveTo(*point(*points[0]))
        for pt in points[1:]:
            p.lineTo(*point(*pt))
        p.close()
        c.setFillColor(HexColor(fill))
        c.drawPath(p, stroke=int(stroke), fill=1)
    for fill in scene['fills']:
        polygon(fill['points'], fill['color'])
    polygon(scene['pile'], '#b4bec7', True)
    for item in scene['lines']:
        c.setStrokeColor(HexColor(item['color']))
        c.setLineWidth(.7 if 'SOIL' not in item['layer'] else .4)
        c.setDash(3, 2) if item['dashed'] else c.setDash()
        c.line(*point(*item['points'][0]), *point(*item['points'][1]))
    c.setDash()
    c.setFillColor(HexColor('#17232b'))
    for label in scene['labels']:
        x, y = point(label['x'], label['z'])
        c.setFont(font.reportlab_name, label['size'])
        if label['anchor'] == 'right':
            c.drawRightString(x, y, label['text'])
        else:
            c.drawString(x, y, label['text'])
    c.setStrokeColor(HexColor('#333333'))
    c.setLineWidth(.5)
    for dim in scene['dimensions']:
        p1, p2, base = dim['p1'], dim['p2'], dim['base']
        if dim['angle'] == 90:
            a, b = point(base[0], p1[1]), point(base[0], p2[1])
            c.line(*point(p1[0]-.003*scale, p1[1]), *a)
            c.line(*point(p2[0]-.003*scale, p2[1]), *b)
            c.line(*a, *b)
            for px, py in (a, b):
                c.line(px-1.5*mm, py-1.5*mm, px+1.5*mm, py+1.5*mm)
            c.saveState()
            c.translate(a[0]-2*mm, (a[1]+b[1])/2)
            c.rotate(90)
            c.setFont(font.reportlab_name, 8.5)
            c.drawCentredString(0, 0, f'{abs(p2[1]-p1[1])*1000:g}')
            c.restoreState()
        else:
            a, b = point(p1[0], base[1]), point(p2[0], base[1])
            c.line(*point(*p1), *a)
            c.line(*point(*p2), *b)
            c.line(*a, *b)
            for px, py in (a, b):
                c.line(px-1*mm, py-1*mm, px+1*mm, py+1*mm)
            c.setFont(font.reportlab_name, 8.5)
            c.drawCentredString((a[0]+b[0])/2, a[1]+2*mm, f'{abs(p2[0]-p1[0])*1000:g}')
    x, y, width = 260*mm, 251*mm, 140*mm
    y = _wrapped(c, font.reportlab_name, f'图号 SV-01    水平竖向 1:{scale}\n尺寸 mm    标高 m    单桩模型', x, y, width, 10, 16)
    y -= 5*mm
    stage = scene['stage']
    info = [f"桩径 {scene['diameterM']*1000:g} mm；桩距 {data['pile']['spacing_m']:g} m；桩长 {scene['lengthM']:g} m",
            f"土反力有效宽度 {data['soil_effective_width_m']:g} m",
            f"标高基准 {data['datum']['label']}", f"显示阶段 {stage['id']}  {stage['name']}",
            f"开挖深度 {stage['excavation_depth_m']:g} m",
            f"坑外水位深度 {stage['water_outside_depth_m']:g} m；坑内 {stage['water_inside_depth_m']:g} m"]
    y = _wrapped(c, font.reportlab_name, '\n'.join(info), x, y, width, 8.5, 15)
    y -= 5*mm
    y = _wrapped(c, font.reportlab_name, '支点  计算约束符号', x, y, width, 10, 16)
    for support in scene['supports'][:8]:
        line = f"{support['label']} {support['id']}\n深 {support['depth_m']:g} m  {'激活' if support['active'] else '拆除'}  {'刚性' if support['kind']=='rigid' else '弹性'}"
        y = _wrapped(c, font.reportlab_name, line, x, y, width, 8, 12)
    if len(scene['supports']) > 8:
        y = _wrapped(c, font.reportlab_name, '其余支点的完整参数见计算书', x, y, width, 8, 12)
    y -= 4*mm
    if y > 65*mm:
        y = _wrapped(c, font.reportlab_name, '施工阶段  完整工况见计算书', x, y, width, 9, 14)
        for row in data['stages']:
            if y < 65*mm:
                break
            y = _wrapped(c, font.reportlab_name,
                         f"{row['id']}  {row['name']}  开挖 {row['excavation_depth_m']:g} m", x, y, width, 8, 12)
    c.setFont(font.reportlab_name, 8)
    c.drawString(17*mm, 43*mm, '支点为计算约束符号，非实际支撑跨长；土层按桩长裁切显示。')
    c.drawString(17*mm, 35*mm, f"来源 {data['purpose']}   阶段 {scene['stage']['id']}   {'求解失败，仅输入几何' if result['status']!='complete' else '计算完成，工程结论待复核'}")
    c.drawString(17*mm, 26*mm, f"Snapshot {result['snapshotHash'][:16]}    Result {result['resultHash'][:16]}")
    c.drawString(17*mm, 17*mm, f"{data['section_id']}    SV-01    A3    比例 1:{scale}    完整哈希和来源见 manifest.json")
    c.showPage()
    c.save()


def export_envelopes(data: dict, result: dict, font: RenderFont, output: Path) -> list[Path]:
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib import pyplot as plt, font_manager
    import numpy as np

    fp = font_manager.FontProperties(fname=str(font.path))
    font_manager.fontManager.addfont(str(font.path))
    top = float(data['datum']['wall_top_elevation_m'])
    envelope = result['envelope']['points']
    stages = [s for s in result['stages'] if s.get('status') in {'complete', 'success'}]
    colors = ['#315e86', '#a86d35', '#5d7c48', '#94616e', '#66799b', '#3e8681', '#8869a5', '#666666']

    def make_figure(design=False, compact=False):
        fig = plt.figure(figsize=(6.9, 3.9) if compact else (420/25.4, 297/25.4))
        axes = [fig.add_axes([.09+i*.30, .22 if compact else .23, .235, .60 if compact else .59]) for i in range(3)]
        fs = 8 if compact else 11
        keysets = [('momentDesignKnM' if design else 'momentCharacteristicKnM',
                    'mdMinKnM' if design else 'mMinKnM', 'mdMaxKnM' if design else 'mMaxKnM', 'M / (kN·m)'),
                   ('shearDesignKn' if design else 'shearCharacteristicKn',
                    'vdMinKn' if design else 'vMinKn', 'vdMaxKn' if design else 'vMaxKn', 'V / kN'),
                   ('displacementMm', 'uMinMm', 'uMaxMm', 'u / mm')]
        handles, labels = [], []
        for index, (ax, keys) in enumerate(zip(axes, keysets)):
            if design and index == 2:
                ax.axis('off')
                ax.text(.5, .55, '设计位移未单独定义\n\n标准位移不乘效应系数\n完整标准位移见第一页',
                        ha='center', va='center', transform=ax.transAxes, fontproperties=fp, fontsize=fs+1, linespacing=1.8)
                continue
            stage_key, low, high, xlabel = keys
            for stage_index, stage in enumerate(stages):
                pts = stage.get('points') or []
                values = [p.get(stage_key) if p.get(stage_key) is not None else np.nan for p in pts]
                z = [top-float(p['depthM']) for p in pts]
                artist, = ax.plot(values, z, color=colors[stage_index % len(colors)], lw=.8, alpha=.55, label=stage['id'])
                if index == 0:
                    handles.append(artist); labels.append(stage['id'])
            zs = [top-float(p['depthM']) for p in envelope]
            mins = [p.get(low) if p.get(low) is not None else np.nan for p in envelope]
            maxs = [p.get(high) if p.get(high) is not None else np.nan for p in envelope]
            ax.fill_betweenx(zs, mins, maxs, color='#315e86', alpha=.10)
            ax.plot(mins, zs, color='#172c3c', lw=1.8)
            ax.plot(maxs, zs, color='#172c3c', lw=1.8)
            ax.axvline(0, color='#777777', lw=.6, ls='--')
            ax.set_ylim(top-data['pile']['length_m']-.2, top+.2)
            ax.set_xlabel(xlabel, fontproperties=fp, fontsize=fs)
            ax.set_title(['弯矩', '剪力', '位移'][index], fontproperties=fp, fontsize=fs+2, pad=8)
            ax.set_ylabel('标高 / m' if index == 0 else '', fontproperties=fp, fontsize=fs)
            ax.grid(True, color='#d4d9de', linewidth=.5)
            ax.tick_params(direction='in', labelsize=fs-1)
            for tick in ax.get_xticklabels()+ax.get_yticklabels():
                tick.set_fontproperties(fp); tick.set_fontsize(fs-1)
            candidates = [(abs(float(v)), float(v), zs[i]) for values in (mins, maxs)
                          for i, v in enumerate(values) if math.isfinite(float(v))]
            if candidates:
                _, value, z = max(candidates)
                # Point into the plot, including extrema at the wall top; the
                # value must not run into an adjacent panel or its title.
                dx, align = (-4, 'right') if value >= 0 else (4, 'left')
                dy = -11 if z > top-.05*data['pile']['length_m'] else 5
                ax.annotate(f'{value:.2f}', (value, z), xytext=(dx, dy), textcoords='offset points',
                            ha=align, fontproperties=fp, fontsize=fs-1, color='#172c3c')
        title = '设计内力包络' if design else '标准特征内力与位移包络'
        fig.text(.06, .94, title, fontproperties=fp, fontsize=12 if compact else 18)
        fig.text(.06, .885, '单桩内力  细线为阶段曲线  粗线为正负包络  未完成工程验证', fontproperties=fp, fontsize=7 if compact else 10)
        fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .085), ncol=min(8, max(1, len(labels))),
                   prop=font_manager.FontProperties(fname=str(font.path), size=7 if compact else 9), frameon=False)
        fig.text(.06, .04, f"快照 {result['snapshotHash'][:12]}  结果 {result['resultHash'][:12]}  阶段名称和完整来源见计算书", fontproperties=fp, fontsize=6.5 if compact else 9)
        return fig

    path = output / 'envelopes_A3.pdf'
    with matplotlib.rc_context({'pdf.fonttype': 42, 'axes.unicode_minus': False}):
        with PdfPages(path, metadata={'Title': data['name']+' 内力包络', 'Subject': f"snapshot={result['snapshotHash']} result={result['resultHash']}"}) as pdf:
            for design in (False, True):
                fig = make_figure(design)
                pdf.savefig(fig)
                plt.close(fig)
        for design, filename in [(False, 'envelope_characteristic.png'), (True, 'envelope_design.png')]:
            fig = make_figure(design, compact=True)
            fig.savefig(output / filename, dpi=220)
            plt.close(fig)
    return [path, output / 'envelope_characteristic.png', output / 'envelope_design.png']
