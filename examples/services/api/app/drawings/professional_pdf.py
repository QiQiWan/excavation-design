from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A0, A1, A2, A3, landscape, portrait
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.schemas.domain import Project
from app.version import SOFTWARE_VERSION


_PAGE_SIZES = {"A0": A0, "A1": A1, "A2": A2, "A3": A3}


def _register_fonts() -> None:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass


def _page_size(sheet: dict[str, Any]) -> tuple[float, float]:
    paper = str(sheet.get("paperSize") or "A1").upper()
    orientation = str(sheet.get("orientation") or "landscape").lower()
    size = _PAGE_SIZES.get(paper, A1)
    return portrait(size) if orientation in {"portrait", "vertical"} else landscape(size)


def _draw_title_block(c: canvas.Canvas, width: float, height: float, project: Project, sheet: dict[str, Any], review: dict[str, Any]) -> tuple[float, float, float, float]:
    margin = 10 * mm
    title_h = 35 * mm
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.35)
    c.rect(margin, margin, width - 2 * margin, height - 2 * margin)
    tb_w = min(300 * mm, (width - 2 * margin) * 0.48)
    tb_x = width - margin - tb_w
    c.rect(tb_x, margin, tb_w, title_h)
    for y in (margin + 9 * mm, margin + 18 * mm, margin + 27 * mm):
        c.line(tb_x, y, tb_x + tb_w, y)
    for x in (tb_x + tb_w * 0.58, tb_x + tb_w * 0.76, tb_x + tb_w * 0.88):
        c.line(x, margin, x, margin + title_h)
    c.setFont("STSong-Light", 8)
    c.drawString(tb_x + 3 * mm, margin + 29 * mm, f"工程：{project.name}")
    c.drawString(tb_x + 3 * mm, margin + 20 * mm, f"图名：{sheet.get('title', '')}")
    c.drawString(tb_x + 3 * mm, margin + 11 * mm, "阶段：基坑围护结构施工图")
    c.drawString(tb_x + tb_w * 0.60, margin + 29 * mm, f"图号：{sheet.get('sheetNo', '')}")
    c.drawString(tb_x + tb_w * 0.60, margin + 20 * mm, f"比例：{sheet.get('scale', 'NTS')}")
    c.drawString(tb_x + tb_w * 0.60, margin + 11 * mm, "设计：AI-DRAFT")
    c.drawString(tb_x + tb_w * 0.60, margin + 2.5 * mm, "校核：ENGINEER")
    c.drawString(tb_x + tb_w * 0.79, margin + 2.5 * mm, "审定：CHIEF")
    c.setFont("STSong-Light", 7)
    c.drawString(margin + 2 * mm, margin + 2.5 * mm, f"PitGuard V{SOFTWARE_VERSION} · snapshot {review.get('currentSnapshotHash')}")
    if not review.get("approvalValid"):
        c.setFillColor(colors.red)
        c.setFont("STSong-Light", 9)
        c.drawString(margin + 2 * mm, margin + 8 * mm, "审查版 / AI-DRAFT / 未完成正式审签，不得直接用于施工")
        c.setFillColor(colors.black)
    return margin, margin + title_h + 4 * mm, width - margin, height - margin


def _fit_transform(points: list[tuple[float, float]], box: tuple[float, float, float, float], padding: float = 0.06):
    x0, y0, x1, y1 = box
    xs = [p[0] for p in points] or [0.0, 1.0]
    ys = [p[1] for p in points] or [0.0, 1.0]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    dx, dy = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    avail_w = (x1 - x0) * (1 - 2 * padding)
    avail_h = (y1 - y0) * (1 - 2 * padding)
    scale = min(avail_w / dx, avail_h / dy)
    ox = x0 + (x1 - x0 - dx * scale) / 2 - minx * scale
    oy = y0 + (y1 - y0 - dy * scale) / 2 - miny * scale
    return lambda x, y: (ox + x * scale, oy + y * scale), scale


def _draw_plan(c: canvas.Canvas, project: Project, box: tuple[float, float, float, float], level: int | None = None) -> None:
    if not project.excavation:
        return
    outline = [(float(p.x), float(p.y)) for p in project.excavation.outline.points]
    all_points = list(outline)
    if project.retaining_system:
        for s in project.retaining_system.supports:
            if level is None or s.level_index == level:
                all_points.extend([(s.start.x, s.start.y), (s.end.x, s.end.y)])
    tf, scale = _fit_transform(all_points, box)
    if outline:
        c.setStrokeColor(colors.HexColor("#333333")); c.setLineWidth(1.2)
        path = c.beginPath(); x, y = tf(*outline[0]); path.moveTo(x, y)
        for p in outline[1:]:
            x, y = tf(*p); path.lineTo(x, y)
        path.close(); c.drawPath(path)
    ret = project.retaining_system
    if not ret:
        return
    for wall in ret.diaphragm_walls:
        if len(wall.axis.points) < 2: continue
        a, b = wall.axis.points[0], wall.axis.points[-1]
        x1, y1 = tf(a.x, a.y); x2, y2 = tf(b.x, b.y)
        c.setStrokeColor(colors.HexColor("#202020")); c.setLineWidth(max(0.6, wall.thickness * scale * 0.12))
        c.line(x1, y1, x2, y2)
        c.setFont("STSong-Light", 6); c.drawCentredString((x1+x2)/2, (y1+y2)/2 + 2*mm, wall.panel_code)
    for support in ret.supports:
        if level is not None and support.level_index != level: continue
        x1, y1 = tf(support.start.x, support.start.y); x2, y2 = tf(support.end.x, support.end.y)
        color = colors.HexColor("#1f5fbf") if support.support_role == "main_strut" else colors.HexColor("#cc4c02") if support.support_role == "corner_diagonal" else colors.HexColor("#2d8a45")
        c.setStrokeColor(color); c.setLineWidth(max(1.0, float(support.section.width or support.section.diameter or 0.8) * scale * 0.22))
        c.line(x1, y1, x2, y2)
        c.setFillColor(colors.black); c.setFont("STSong-Light", 5.5)
        c.drawCentredString((x1+x2)/2, (y1+y2)/2 + 1.5*mm, support.code)
    for column in ret.columns:
        x, y = tf(column.location.x, column.location.y)
        c.setFillColor(colors.HexColor("#6b3d1f")); c.circle(x, y, 1.5*mm, fill=1, stroke=0)
        c.setFillColor(colors.black); c.setFont("STSong-Light", 5.5); c.drawString(x + 1.8*mm, y + 1.0*mm, column.code)
    c.setStrokeColor(colors.black); c.setLineWidth(0.35)


def _draw_section(c: canvas.Canvas, project: Project, box: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = box
    ret = project.retaining_system
    depth = float(project.excavation.depth if project.excavation else 12.0)
    top = 0.0
    bottom = -max(depth + 8.0, 20.0)
    tf, scale = _fit_transform([(0, bottom), (30, top)], box)
    gx0, gy = tf(0, 0); gx1, _ = tf(30, 0)
    c.setLineWidth(1.0); c.line(gx0, gy, gx1, gy)
    wx, wt = tf(5, top); _, wb = tf(5, bottom)
    c.setLineWidth(2.0); c.line(wx, wt, wx, wb)
    wx2, _ = tf(25, top); _, wb2 = tf(25, bottom)
    c.line(wx2, wt, wx2, wb2)
    if ret:
        levels = sorted({s.level_index: s.elevation for s in ret.supports}.items())
        for level, elev in levels:
            xa, yy = tf(5, elev); xb, _ = tf(25, elev)
            c.setStrokeColor(colors.HexColor("#1f5fbf")); c.setLineWidth(1.2); c.line(xa, yy, xb, yy)
            c.setFillColor(colors.black); c.setFont("STSong-Light", 6); c.drawString(xa + 2*mm, yy + 1*mm, f"第{level}道支撑 EL.{elev:.3f}")
    if project.excavation:
        xa, yy = tf(5, project.excavation.bottom_elevation); xb, _ = tf(25, project.excavation.bottom_elevation)
        c.setStrokeColor(colors.red); c.setDash(4, 2); c.line(xa, yy, xb, yy); c.setDash()
        c.setFont("STSong-Light", 6); c.drawString(xa + 2*mm, yy - 3*mm, f"坑底 EL.{project.excavation.bottom_elevation:.3f}")
    c.setStrokeColor(colors.black)


def _draw_wall_rebar(c: canvas.Canvas, project: Project, box: tuple[float, float, float, float], wall_index: int | None = None) -> None:
    ret = project.retaining_system
    if not ret or not ret.diaphragm_walls:
        return
    walls = ret.diaphragm_walls
    if wall_index is not None and 1 <= wall_index <= len(walls):
        walls = [walls[wall_index-1]]
    else:
        walls = walls[: min(6, len(walls))]
    x0, y0, x1, y1 = box
    gap = 8*mm
    cell_w = (x1-x0-gap*(len(walls)-1))/max(len(walls),1)
    for idx, wall in enumerate(walls):
        cx0 = x0 + idx*(cell_w+gap); cx1 = cx0+cell_w
        top, bottom = float(wall.top_elevation), float(wall.bottom_elevation)
        tf, _ = _fit_transform([(0,bottom),(max(float(wall.design_length or 6.0),3.0),top)], (cx0,y0,cx1,y1), .10)
        length = max(float(wall.design_length or 6.0),3.0)
        ax, ay = tf(0,bottom); bx, by = tf(length,top)
        c.setLineWidth(1.0); c.rect(ax, ay, bx-ax, by-ay)
        # Main bars and distribution bars are drawn explicitly as schematic placement lines.
        for i in range(1,8):
            x = ax + (bx-ax)*i/8
            c.setStrokeColor(colors.HexColor("#b22222")); c.setLineWidth(.5); c.line(x, ay+2*mm, x, by-2*mm)
        for i in range(1,10):
            y = ay + (by-ay)*i/10
            c.setStrokeColor(colors.HexColor("#1f5fbf")); c.setLineWidth(.35); c.line(ax+2*mm, y, bx-2*mm, y)
        c.setFillColor(colors.black); c.setFont("STSong-Light", 6.5); c.drawCentredString((ax+bx)/2, by+3*mm, wall.panel_code)
        ytext = ay-4*mm
        for group in (wall.reinforcement or [])[:5]:
            token = f"{group.name} D{group.diameter:g}" + (f"@{group.spacing:g}" if group.spacing else f"×{group.count}" if group.count else "")
            c.setFont("STSong-Light", 5.5); c.drawString(ax, ytext, token); ytext -= 3*mm
    c.setStrokeColor(colors.black)


def _draw_stage_sequence(c: canvas.Canvas, project: Project, box: tuple[float,float,float,float]) -> None:
    x0,y0,x1,y1=box
    cases=project.calculation_cases
    stages=list(cases[-1].stages) if cases else []
    if not stages:
        levels=sorted({(int(s.level_index),float(s.elevation)) for s in (project.retaining_system.supports if project.retaining_system else [])})
        current=float(project.excavation.top_elevation if project.excavation else 0.0)
        for level,elev in levels:
            stages.append(type("Stage",(),{"name":f"安装第{level}道支撑","stage_type":"support_installation","excavation_elevation":current,"active_support_levels":[level]})())
            current=elev-1.0
            stages.append(type("Stage",(),{"name":f"开挖至EL.{current:.2f}","stage_type":"excavation","excavation_elevation":current,"active_support_levels":[x[0] for x in levels if x[0]<=level]})())
    cols=4; rows=max(1,math.ceil(min(len(stages),12)/cols)); gap=5*mm
    cw=(x1-x0-gap*(cols-1))/cols; ch=(y1-y0-gap*(rows-1))/rows
    top=float(project.excavation.top_elevation if project.excavation else 0.0); bottom=float(project.excavation.bottom_elevation if project.excavation else -12.0)
    wb=min((float(w.bottom_elevation) for w in (project.retaining_system.diaphragm_walls if project.retaining_system else [])),default=bottom-8)
    for i,stage in enumerate(stages[:12]):
        col=i%cols; row=i//cols; bx=x0+col*(cw+gap); by=y1-(row+1)*ch-row*gap
        c.rect(bx,by,cw,ch); c.setFont("STSong-Light",6); c.drawString(bx+2*mm,by+ch-4*mm,f"阶段{i+1:02d} {stage.name}")
        sx0=bx+5*mm; sx1=bx+cw*0.63; sy0=by+6*mm; sy1=by+ch-8*mm
        def zmap(z:float)->float: return sy0+(z-wb)/max(top-wb,1e-9)*(sy1-sy0)
        c.setLineWidth(1.1); c.line(sx0,zmap(wb),sx0,zmap(top)); c.line(sx1,zmap(wb),sx1,zmap(top))
        exc=float(stage.excavation_elevation); c.setStrokeColor(colors.red); c.line(sx0,zmap(exc),sx1,zmap(exc)); c.setStrokeColor(colors.black)
        active=set(int(v) for v in (getattr(stage,"active_support_levels",[]) or []))
        if project.retaining_system:
            levels={}
            for support in project.retaining_system.supports: levels.setdefault(int(support.level_index),float(support.elevation))
            c.setStrokeColor(colors.HexColor("#1f5fbf"))
            for level,elev in levels.items():
                if level in active: c.line(sx0,zmap(elev),sx1,zmap(elev))
            c.setStrokeColor(colors.black)
        tx=bx+cw*0.67; c.setFont("STSong-Light",5.5)
        c.drawString(tx,by+ch-10*mm,f"类型：{stage.stage_type}"); c.drawString(tx,by+ch-14*mm,f"开挖：EL.{exc:.2f}")
        c.drawString(tx,by+ch-18*mm,"支撑："+",".join(str(v) for v in sorted(active)))


def _drawing_reference_rows(manifest: dict[str, Any]) -> list[list[Any]]:
    calc={
        "master_plan":"几何/拓扑","wall_panel_layout":"墙段/嵌固","support_level_plan":"轴力/跨度/节点",
        "excavation_section":"土水压力/位移/稳定","longitudinal_section":"地层/地下水/稳定",
        "construction_stage_sequence":"分阶段激活/换撑","wall_rebar_general":"墙体配筋",
        "wall_rebar_elevation":"包络/分区配筋","support_rebar_general":"轴压/偏压配筋","wale_rebar_general":"连续梁内力",
    }
    standards={
        "master_plan":"JGJ120/GB55003","wall_panel_layout":"JGJ120/GB50202","support_level_plan":"JGJ120/GB50010/17",
        "excavation_section":"JGJ120/GB55003/GB50007","longitudinal_section":"JGJ120/GB50007",
        "construction_stage_sequence":"JGJ120/GB50497","wall_rebar_general":"GB55008/GB50010",
        "wall_rebar_elevation":"GB55008/GB50010","support_rebar_general":"GB55008/GB50010","wale_rebar_general":"GB55008/GB50010",
    }
    return [[s.get("sheetNo"),s.get("title"),";".join(s.get("modelBinding") or []),calc.get(str(s.get("renderer")),"专项复核"),standards.get(str(s.get("renderer")),"项目规范"),s.get("file")] for s in manifest.get("sheets",[])]


def _draw_table(c: canvas.Canvas, rows: list[list[Any]], headers: list[str], box: tuple[float,float,float,float], max_rows: int = 28) -> None:
    x0,y0,x1,y1=box
    data=[headers]+[[str(v) for v in row] for row in rows[:max_rows]]
    cols=len(headers); row_h=min(7*mm,(y1-y0)/max(len(data)+1,2)); col_w=(x1-x0)/max(cols,1)
    c.setFont("STSong-Light", 6)
    for r,row in enumerate(data):
        yy=y1-(r+1)*row_h
        for col,val in enumerate(row):
            xx=x0+col*col_w
            c.rect(xx,yy,col_w,row_h)
            c.drawString(xx+1*mm,yy+2*mm,str(val)[:34])


def _draw_detail(c: canvas.Canvas, box: tuple[float,float,float,float], title: str) -> None:
    x0,y0,x1,y1=box; cx=(x0+x1)/2; cy=(y0+y1)/2
    c.setLineWidth(1.5); c.rect(cx-55*mm,cy-28*mm,110*mm,56*mm)
    c.setStrokeColor(colors.HexColor("#1f5fbf")); c.setLineWidth(5); c.line(cx-80*mm,cy,cx+80*mm,cy)
    c.setStrokeColor(colors.HexColor("#b22222")); c.setLineWidth(1.0)
    for offset in (-35,-20,-5,10,25,40):
        c.line(cx+offset*mm,cy-25*mm,cx+offset*mm,cy+25*mm)
    c.setStrokeColor(colors.black); c.setFont("STSong-Light", 8)
    c.drawCentredString(cx,cy+35*mm,title)
    notes=["1. 构件尺寸、钢筋、承压板及预埋件均应与模型和钢筋表一致。","2. 焊缝、锚固、机械连接及施工偏差按项目专项要求复核。","3. 节点施工前应完成实体碰撞和可施工性复核。"]
    for i,note in enumerate(notes): c.drawString(x0+5*mm,y0+(12-i*5)*mm,note)


def export_professional_batch_pdf(project: Project, output_path: Path, manifest: dict[str, Any], detailing: dict[str, Any], suite: dict[str, Any], review: dict[str, Any]) -> Path:
    _register_fonts()
    c = canvas.Canvas(str(output_path), pageCompression=1)
    for sheet in manifest.get("sheets", []):
        width,height=_page_size(sheet); c.setPageSize((width,height))
        box=_draw_title_block(c,width,height,project,sheet,review)
        x0,y0,x1,y1=box
        c.setFont("STSong-Light", 12); c.drawString(x0+3*mm,y1-8*mm,str(sheet.get("title") or ""))
        content=(x0+5*mm,y0+5*mm,x1-5*mm,y1-15*mm)
        renderer=str(sheet.get("renderer") or "")
        if renderer in {"master_plan","wall_panel_layout","legacy_support_plan","support_level_plan","monitoring_plan","wall_rebar_general","rebar_geometry_plan"}:
            level=sheet.get("level") if renderer=="support_level_plan" else None
            _draw_plan(c,project,content,int(level) if level is not None else None)
        elif renderer in {"excavation_section","longitudinal_section"}:
            _draw_section(c,project,content)
        elif renderer=="construction_stage_sequence":
            _draw_stage_sequence(c,project,content)
        elif renderer in {"wall_rebar_elevation","single_wall_rebar_elevation","wall_rebar_cage"}:
            _draw_wall_rebar(c,project,content,int(sheet.get("wallIndex")) if sheet.get("wallIndex") else None)
        elif renderer=="general_notes":
            rows=[[s.get("sheetNo"),s.get("title"),s.get("scale"),s.get("paperSize")] for s in manifest.get("sheets",[])]; _draw_table(c,rows,["图号","图名","比例","图幅"],content,24)
        elif renderer=="drawing_reference_matrix":
            _draw_table(c,_drawing_reference_rows(manifest),["图号","图名","模型","计算","规范","文件"],content,28)
        elif renderer=="drawing_quality_summary":
            rows=[[s.get("sheetNo"),s.get("title"),s.get("paperSize"),s.get("scale"),"是" if s.get("required") else "否","图层/尺寸/图签/纸空间"] for s in manifest.get("sheets",[])]; _draw_table(c,rows,["图号","图名","图幅","比例","必要","检查"],content,28)
        elif renderer in {"rebar_bending_schedule","cage_lifting_plan","splice_layout","cover_conflict_check","shop_signoff","support_rebar_general","wale_rebar_general","cage_hoisting_analysis","coupler_schedule_detail","embedded_collision_quality"}:
            if renderer=="rebar_bending_schedule": rows=[[x.get("barMark"),x.get("hostCode"),x.get("diameterMm"),x.get("fabricationPieceCount"),x.get("pieceLengthsM"),x.get("status")] for x in detailing.get("fabricationBbs",[])]; headers=["编号","构件","直径","件数","下料长度","状态"]
            elif renderer=="cage_lifting_plan": rows=[[x.get("segmentId"),x.get("hostCode"),x.get("lengthM"),x.get("estimatedCageWeightT"),x.get("liftingPointCount"),x.get("status")] for x in detailing.get("cageSegments",[])]; headers=["分段","墙幅","长度","重量/t","吊点","状态"]
            elif renderer=="splice_layout": rows=[[x.get("spliceId"),x.get("barMark"),x.get("hostCode"),x.get("spliceType"),x.get("staggerGroup"),x.get("status")] for x in detailing.get("fabricationSplices",[])]; headers=["接头","钢筋","构件","形式","错开组","状态"]
            elif renderer=="cage_hoisting_analysis": rows=[[x.get("segmentId"),x.get("hostCode"),x.get("weightT"),x.get("liftingPointCount"),x.get("lineTensionKn"),x.get("liftingBarDiameterMm"),x.get("status")] for x in (detailing.get("deepDetailing") or {}).get("cageHoisting",[])]; headers=["分段","墙幅","重量/t","吊点","索力/kN","吊筋","状态"]
            elif renderer=="coupler_schedule_detail": rows=[[x.get("couplerId"),x.get("barMark"),x.get("hostCode"),x.get("diameterMm"),x.get("specification"),x.get("staggerGroup"),x.get("status")] for x in (detailing.get("deepDetailing") or {}).get("couplerSchedule",[])]; headers=["套筒","钢筋","构件","直径","规格","错开组","状态"]
            elif renderer=="embedded_collision_quality": rows=[[x.get("embeddedItemId"),x.get("barMark"),x.get("hostCode"),x.get("status"),x.get("message")] for x in (detailing.get("deepDetailing") or {}).get("embeddedItemCollisionChecks",[])]; headers=["预埋件","钢筋","构件","状态","问题"]
            else: rows=[[x.get("id") or x.get("barMark"),x.get("label") or x.get("hostCode"),x.get("status"),x.get("evidenceCount") or x.get("quantity"),x.get("message") or ""] for x in detailing.get("signoffChecklist",[])]; headers=["编号","项目","状态","数量","说明"]
            _draw_table(c,rows,headers,content)
        elif renderer in {"serviceability_quality","collision_quality","node_local_quality","monitoring_calibration"}:
            if renderer=="collision_quality": rows=[[x.get("id"),x.get("objectA"),x.get("objectB"),x.get("status"),x.get("message")] for x in suite.get("collisions",{}).get("collisions",[])]; headers=["编号","对象A","对象B","状态","问题"]
            elif renderer=="node_local_quality": rows=[[x.get("nodeCode"),x.get("supportCode"),x.get("maxUtilization"),x.get("localSlipMm"),x.get("status")] for x in suite.get("nodeLocal",{}).get("nodes",[])]; headers=["节点","支撑","利用率","滑移/mm","状态"]
            elif renderer=="serviceability_quality": rows=[[x.get("hostCode"),x.get("face"),x.get("estimatedCrackWidthMm"),x.get("limitMm"),x.get("status")] for x in suite.get("serviceability",{}).get("wallZoneChecks",[])]; headers=["构件","侧别","裂缝/mm","限值/mm","状态"]
            else: rows=[["监测记录",suite.get("monitoring",{}).get("recordCount"),"需复算",suite.get("monitoring",{}).get("requiresRecalculation"),""]]; headers=["项目","值","项目2","值2","备注"]
            _draw_table(c,rows,headers,content)
        else:
            _draw_detail(c,content,str(sheet.get("title") or renderer))
        c.showPage()
    c.save()
    return output_path
