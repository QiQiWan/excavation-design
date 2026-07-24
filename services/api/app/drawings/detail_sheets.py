from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.domain import DrawingSheetResult, Project


def _svg_header(width: int = 1400, height: int = 900) -> str:
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>
<rect x='0' y='0' width='{width}' height='{height}' fill='white'/>
<rect x='35' y='35' width='{width-70}' height='{height-70}' fill='none' stroke='black' stroke-width='2'/>
<style>text{{font-family:Arial,'Microsoft YaHei',sans-serif;font-size:20px}} .small{{font-size:14px}} .thin{{stroke:#333;stroke-width:1;fill:none}} .thick{{stroke:#111;stroke-width:4;fill:none}} .rebar{{stroke:#111;stroke-width:3}} .dim{{stroke:#777;stroke-width:1;fill:none;stroke-dasharray:6 4}}</style>
"""


def _title_block(title: str, scale: str) -> str:
    return f"""<rect x='880' y='750' width='470' height='105' fill='none' stroke='black' stroke-width='1'/>
<line x1='880' y1='785' x2='1350' y2='785' stroke='black'/>
<line x1='1040' y1='750' x2='1040' y2='855' stroke='black'/>
<text x='895' y='775' class='small'>图名</text><text x='1055' y='775' class='small'>{title}</text>
<text x='895' y='812' class='small'>比例</text><text x='1055' y='812' class='small'>{scale}</text>
<text x='895' y='842' class='small'>阶段</text><text x='1055' y='842' class='small'>方案/施工图深化接口</text>
"""


def _write_svg(path: Path, title: str, scale: str, body: str) -> None:
    path.write_text(_svg_header() + body + _title_block(title, scale) + "</svg>\n", encoding="utf-8")


def generate_construction_detail_sheets(project: Project, output_dir: str | Path) -> list[DrawingSheetResult]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sheets: list[DrawingSheetResult] = []
    retaining = project.retaining_system
    if not retaining:
        return sheets
    # 1. Support plan.
    plan_path = out / "D-01_support_plan.svg"
    points = project.excavation.outline.points if project.excavation else []
    def tx(x: float) -> float:
        xs = [p.x for p in points] or [0, 60]
        return 120 + (x - min(xs)) / max(max(xs) - min(xs), 1.0) * 900
    def ty(y: float) -> float:
        ys = [p.y for p in points] or [0, 30]
        return 650 - (y - min(ys)) / max(max(ys) - min(ys), 1.0) * 500
    body = "<text x='80' y='85'>支撑平面布置图</text>\n"
    if points:
        coords = " ".join(f"{tx(p.x):.1f},{ty(p.y):.1f}" for p in points)
        body += f"<polygon points='{coords}' fill='none' stroke='#111' stroke-width='4'/>\n"
    for s in retaining.supports[:80]:
        width = max(2, min(8, (s.design_axial_force or 1000) / 3000))
        body += f"<line x1='{tx(s.start.x):.1f}' y1='{ty(s.start.y):.1f}' x2='{tx(s.end.x):.1f}' y2='{ty(s.end.y):.1f}' stroke='#0b4' stroke-width='{width:.1f}'/>\n"
        mx, my = (tx((s.start.x+s.end.x)/2), ty((s.start.y+s.end.y)/2))
        body += f"<text x='{mx+5:.1f}' y='{my-5:.1f}' class='small'>{s.code}</text>\n"
    for c in retaining.columns[:60]:
        body += f"<circle cx='{tx(c.location.x):.1f}' cy='{ty(c.location.y):.1f}' r='6' fill='#111'/><text x='{tx(c.location.x)+8:.1f}' y='{ty(c.location.y)-8:.1f}' class='small'>{c.code}</text>\n"
    _write_svg(plan_path, "支撑平面布置图", "1:200", body)
    sheets.append(DrawingSheetResult(sheet_id="D-01", title="支撑平面布置图", scale="1:200", file_path=str(plan_path), sheet_type="plan", model_objects=[s.id for s in retaining.supports]))
    # 2. Wale-node detail.
    node_path = out / "D-02_wale_node_detail.svg"
    body = "<text x='80' y='85'>支撑—围檩节点详图</text>\n"
    body += "<rect x='180' y='300' width='760' height='120' fill='none' stroke='black' stroke-width='4'/><text x='450' y='285'>钢筋混凝土围檩</text>\n"
    body += "<rect x='500' y='220' width='220' height='280' fill='none' stroke='#444' stroke-width='3'/><text x='520' y='210'>承压板 / 节点刚域</text>\n"
    body += "<line x1='610' y1='120' x2='610' y2='640' stroke='#0b4' stroke-width='16'/><text x='635' y='160'>水平支撑</text>\n"
    for i in range(6):
        y = 320 + i * 16
        body += f"<line x1='205' y1='{y}' x2='915' y2='{y}' class='rebar'/>\n"
    body += "<text x='180' y='470' class='small'>围檩主筋连续通过；节点两侧 1.5h 范围箍筋加密；承压板后附加 U 形筋和抗裂分布筋。</text>\n"
    _write_svg(node_path, "支撑—围檩节点详图", "1:50", body)
    sheets.append(DrawingSheetResult(sheet_id="D-02", title="支撑—围檩节点详图", scale="1:50", file_path=str(node_path), sheet_type="node_detail", model_objects=[n.id for n in retaining.support_nodes[:20]]))
    # 3. Diaphragm wall rebar cage.
    wall_path = out / "D-03_wall_rebar_cage.svg"
    body = "<text x='80' y='85'>地下连续墙钢筋笼示意图</text>\n"
    body += "<rect x='260' y='130' width='360' height='650' fill='none' stroke='black' stroke-width='4'/><text x='300' y='115'>地连墙钢筋笼</text>\n"
    for i in range(9):
        x = 285 + i * 38
        body += f"<line x1='{x}' y1='150' x2='{x}' y2='760' class='rebar'/>\n"
    for j in range(18):
        y = 160 + j * 34
        body += f"<line x1='280' y1='{y}' x2='600' y2='{y}' stroke='#555' stroke-width='2'/>\n"
    body += "<text x='650' y='240' class='small'>主筋、水平分布筋、拉结筋、吊筋与接头加强筋按构件属性表深化。</text>\n"
    _write_svg(wall_path, "地下连续墙钢筋笼示意图", "1:50", body)
    sheets.append(DrawingSheetResult(sheet_id="D-03", title="地下连续墙钢筋笼示意图", scale="1:50", file_path=str(wall_path), sheet_type="rebar_cage", model_objects=[w.id for w in retaining.diaphragm_walls[:10]]))
    # 4. Column pile detail.
    pile_path = out / "D-04_column_pile_detail.svg"
    body = "<text x='80' y='85'>临时立柱桩详图</text>\n"
    body += "<line x1='500' y1='120' x2='500' y2='760' stroke='black' stroke-width='8'/><rect x='430' y='250' width='140' height='80' fill='none' stroke='black' stroke-width='3'/><text x='590' y='300'>立柱承台</text>\n"
    body += "<line x1='500' y1='330' x2='500' y2='780' stroke='#555' stroke-width='30'/><text x='545' y='600'>钻孔灌注桩 / 临时立柱桩</text>\n"
    body += "<text x='130' y='820' class='small'>注：桩径、桩长、单桩承载力和利用率由 FoundationDesign 输出；正式施工图应补桩身配筋、桩端持力层和施工偏差要求。</text>\n"
    _write_svg(pile_path, "临时立柱桩详图", "1:50", body)
    sheets.append(DrawingSheetResult(sheet_id="D-04", title="临时立柱桩详图", scale="1:50", file_path=str(pile_path), sheet_type="pile_detail", model_objects=[c.id for c in retaining.columns[:20]]))

    # 5-17. V3.86 design-institute delivery sheet system. These sheets are
    # generated from the same project snapshot and keep explicit model-object
    # references. SVG is an auditable source for CAD/PDF packaging; downstream
    # drawing rules still decide whether a construction issue is allowed.
    generic_specs = [
        ("D-05", "设计总说明", "general_note", "1:100", ["设计依据", "材料与耐久性", "施工控制条件", "监测控制要求", "人工复核边界"]),
        ("D-06", "基坑平面定位图", "location_plan", "1:500", ["坐标基准", "基坑轮廓", "周边环境", "控制点与尺寸链"]),
        ("D-07", "围护墙平面与分幅图", "wall_plan", "1:200", ["墙段编号", "槽段分幅", "墙厚", "墙趾分区", "接头类型"]),
        ("D-08", "典型围护剖面图", "section", "1:100", ["地层", "水位", "支撑层", "坑底", "墙趾", "控制标高"]),
        ("D-09", "各道支撑平面图", "support_plan", "1:200", ["支撑编号", "围檩", "立柱", "转接梁", "施工净空"]),
        ("D-10", "围檩与支撑构件截面", "member_section", "1:50", ["截面尺寸", "材料", "设计内力", "主筋", "箍筋", "保护层"]),
        ("D-11", "立柱及立柱桩详图", "column_detail", "1:50", ["立柱截面", "连接节点", "基础尺寸", "桩长", "承载力"]),
        ("D-12", "换撑与拆撑设计原则", "replacement_principle", "1:100", ["设计控制工况", "传力交接", "拆撑前置条件", "永久结构要求"]),
        ("D-13", "围檩与环梁钢筋图", "wale_rebar", "1:50", ["轴弯剪扭组合", "抗扭纵筋", "闭合箍筋", "节点附加筋", "锚固"]),
        ("D-14", "混凝土支撑钢筋图", "support_rebar", "1:50", ["纵筋", "端部加密箍筋", "中段箍筋", "侧面分布筋", "搭接与锚固"]),
        ("D-15", "构件明细表", "member_schedule", "NTS", ["构件编号", "截面", "材料", "长度", "控制工况", "利用率"]),
        ("D-16", "钢筋明细表", "rebar_schedule", "NTS", ["宿主构件", "钢筋编号", "级别", "直径", "间距/根数", "长度", "数量"]),
        ("D-17", "监测与设计控制要求", "monitoring_control", "NTS", ["监测项目", "设计控制值", "变化速率", "通知设计条件", "复核要求"]),
    ]
    object_ids = [w.id for w in retaining.diaphragm_walls] + [b.id for b in retaining.wale_beams] + [s.id for s in retaining.supports] + [c.id for c in retaining.columns]
    for sheet_id, title, sheet_type, scale, notes in generic_specs:
        path = out / f"{sheet_id}_{sheet_type}.svg"
        body = f"<text x='80' y='85'>{title}</text>\n"
        body += "<rect x='100' y='125' width='1120' height='540' fill='none' stroke='#222' stroke-width='2'/>\n"
        for index, note in enumerate(notes):
            y = 180 + index * 72
            body += f"<circle cx='145' cy='{y-7}' r='5' fill='#244f78'/><text x='170' y='{y}'>{index+1}. {note}</text>\n"
        body += "<text x='100' y='710' class='small'>本图由当前 DesignSnapshotId 对应模型生成；正式发行前执行图面规则、构件编号、标高、尺寸和成果一致性校核。</text>\n"
        _write_svg(path, title, scale, body)
        sheets.append(DrawingSheetResult(sheet_id=sheet_id, title=title, scale=scale, file_path=str(path), sheet_type=sheet_type, model_objects=object_ids[:200], notes=notes))
    return sheets
