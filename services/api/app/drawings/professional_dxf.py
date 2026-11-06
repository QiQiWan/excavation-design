from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment


_PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}


def _parse_scale(value: str | None, fallback: int = 100) -> int:
    text = str(value or "").strip().upper()
    if text in {"NTS", "NOT TO SCALE", "不按比例"}:
        return fallback
    if ":" in text:
        try:
            return max(1, int(round(float(text.split(":", 1)[1]))))
        except (TypeError, ValueError):
            pass
    return fallback


class ProfessionalDxfWriter:
    """R2018 DXF writer with model-space engineering geometry and paper-space publication.

    Input coordinates use metres to preserve the existing PitGuard drawing generators.
    Entities are written in millimetres at 1:1 in model space.  A paper-space layout,
    locked viewport, enterprise title block, Unicode text style and native dimensions
    are generated for every sheet.
    """

    def __init__(self) -> None:
        self.doc = ezdxf.new("R2018", setup=True)
        self.doc.units = units.MM
        self.doc.header["$INSUNITS"] = units.MM
        self.doc.header["$MEASUREMENT"] = 1
        self.msp = self.doc.modelspace()
        self.layers: set[str] = set()
        self.min_x = math.inf
        self.max_x = -math.inf
        self.min_y = math.inf
        self.max_y = -math.inf
        self.unit_scale = 1000.0
        self._title: dict[str, Any] | None = None
        self._ensure_styles()

    def _ensure_styles(self) -> None:
        if "PIT_CN" not in self.doc.styles:
            style = self.doc.styles.new("PIT_CN")
            style.dxf.font = "msyh.ttf"
        try:
            if "PIT_DIM" not in self.doc.dimstyles:
                self.doc.dimstyles.duplicate_entry("EZDXF", "PIT_DIM")
            dim = self.doc.dimstyles.get("PIT_DIM")
            dim.dxf.dimtxsty = "PIT_CN"
            dim.dxf.dimtxt = 3.0
            dim.dxf.dimasz = 2.5
            dim.dxf.dimexo = 1.0
            dim.dxf.dimexe = 1.5
            dim.dxf.dimgap = 1.0
            dim.dxf.dimdec = 0
        except Exception:
            # The drawing remains valid even when an older ezdxf setup lacks EZDXF.
            pass

    def _u(self, value: float) -> float:
        return float(value) * self.unit_scale

    def _update_bounds(self, *points: tuple[float, float]) -> None:
        for x, y in points:
            x_mm, y_mm = self._u(x), self._u(y)
            self.min_x = min(self.min_x, x_mm)
            self.max_x = max(self.max_x, x_mm)
            self.min_y = min(self.min_y, y_mm)
            self.max_y = max(self.max_y, y_mm)

    def drawing_bounds(self) -> tuple[float, float, float, float]:
        if not math.isfinite(self.min_x):
            return 0.0, 120.0, 0.0, 20.0
        return self.min_x / self.unit_scale, self.max_x / self.unit_scale, self.min_y / self.unit_scale, self.max_y / self.unit_scale

    def _add_layer(self, layer: str) -> None:
        if layer in self.layers:
            return
        self.layers.add(layer)
        if layer not in self.doc.layers:
            # Deterministic ACI assignment; enterprise CTB can override line weights.
            color = 1 + (sum(ord(c) for c in layer) % 7)
            self.doc.layers.add(layer, color=color, linetype="CONTINUOUS")

    def line(self, layer: str, x1: float, y1: float, x2: float, y2: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x1, y1), (x2, y2))
        self.msp.add_line((self._u(x1), self._u(y1)), (self._u(x2), self._u(y2)), dxfattribs={"layer": layer})

    def circle(self, layer: str, x: float, y: float, r: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x - r, y - r), (x + r, y + r))
        self.msp.add_circle((self._u(x), self._u(y)), max(self._u(r), 1.0), dxfattribs={"layer": layer})

    def text(self, layer: str, x: float, y: float, value: str, height: float = 0.35, rotation: float = 0.0) -> None:
        self._add_layer(layer)
        safe = str(value).replace("\n", " ")[:500]
        self._update_bounds((x, y), (x + max(len(safe), 1) * height * 0.62, y + height))
        entity = self.msp.add_text(
            safe,
            height=max(self._u(height), 1.0),
            dxfattribs={"layer": layer, "style": "PIT_CN", "rotation": float(rotation)},
        )
        entity.set_placement((self._u(x), self._u(y)), align=TextEntityAlignment.LEFT)

    def lwpolyline(self, layer: str, points: Iterable[Any], closed: bool = False) -> None:
        pts = list(points)
        if not pts:
            return
        self._add_layer(layer)
        self._update_bounds(*[(float(p.x), float(p.y)) for p in pts])
        self.msp.add_lwpolyline(
            [(self._u(float(p.x)), self._u(float(p.y))) for p in pts],
            close=bool(closed),
            dxfattribs={"layer": layer},
        )

    def arc(self, layer: str, x: float, y: float, r: float, start_angle: float, end_angle: float) -> None:
        self._add_layer(layer)
        self._update_bounds((x - r, y - r), (x + r, y + r))
        self.msp.add_arc(
            (self._u(x), self._u(y)),
            max(self._u(r), 1.0),
            float(start_angle),
            float(end_angle),
            dxfattribs={"layer": layer},
        )

    def leader(self, layer: str, x1: float, y1: float, x2: float, y2: float, text: str, text_height: float = 0.28) -> None:
        self._add_layer(layer)
        self._update_bounds((x1, y1), (x2, y2))
        p1, p2 = (self._u(x1), self._u(y1)), (self._u(x2), self._u(y2))
        try:
            self.msp.add_leader([p1, p2], dxfattribs={"layer": layer})
        except Exception:
            self.msp.add_line(p1, p2, dxfattribs={"layer": layer})
        self.text(layer, x2 + 0.2, y2 + 0.1, text, text_height)

    def rectangle(self, layer: str, x: float, y: float, w: float, h: float) -> None:
        class P:
            def __init__(self, px: float, py: float) -> None:
                self.x = px
                self.y = py
        self.lwpolyline(layer, [P(x, y), P(x + w, y), P(x + w, y + h), P(x, y + h)], closed=True)

    def dim_line(self, layer: str, x1: float, y1: float, x2: float, y2: float, label: str, offset: float = 2.0) -> None:
        self._add_layer(layer)
        self._update_bounds((x1, y1), (x2, y2))
        p1 = (self._u(x1), self._u(y1))
        p2 = (self._u(x2), self._u(y2))
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        base = ((p1[0] + p2[0]) / 2 + nx * self._u(offset), (p1[1] + p2[1]) / 2 + ny * self._u(offset))
        try:
            dim = self.msp.add_linear_dim(base=base, p1=p1, p2=p2, dimstyle="PIT_DIM", dxfattribs={"layer": layer})
            dim.render()
            if label:
                dim.dimension.dxf.text = str(label)
        except Exception:
            # Keep a readable fallback while preserving a valid R2018 file.
            self.msp.add_line(p1, p2, dxfattribs={"layer": layer})
            self.text(layer, (x1 + x2) / 2.0, (y1 + y2) / 2.0 + offset, label, 0.28)

    def title_block(
        self,
        sheet_no: str,
        title: str,
        scale: str = "1:100",
        project_name: str = "PitGuard project",
        stage: str = "施工图深化接口",
        designer: str = "AI",
        checker: str = "REVIEW",
        approver: str = "CHIEF",
        template: dict | None = None,
    ) -> None:
        self._title = {
            "sheetNo": sheet_no,
            "title": title,
            "scale": scale,
            "projectName": project_name,
            "stage": stage,
            "designer": designer,
            "checker": checker,
            "approver": approver,
            "template": template or {},
        }

    def _paper_settings(self) -> tuple[str, str, float, float]:
        title = self._title or {}
        template = title.get("template") or {}
        paper = str(template.get("activePaperSize") or template.get("paperSize") or "A1").upper()
        orientation = str(template.get("activeOrientation") or template.get("orientation") or "landscape").lower()
        width, height = _PAPER_SIZES_MM.get(paper, _PAPER_SIZES_MM["A1"])
        if orientation in {"portrait", "vertical"} and width > height:
            width, height = height, width
        elif orientation not in {"portrait", "vertical"} and height > width:
            width, height = height, width
        return paper, orientation, width, height

    def _add_paperspace(self) -> None:
        title = self._title or {
            "sheetNo": "S-00", "title": "PitGuard drawing", "scale": "1:100", "projectName": "PitGuard",
            "stage": "设计辅助", "designer": "AI-DRAFT", "checker": "REVIEW", "approver": "CHIEF", "template": {},
        }
        paper, orientation, paper_w, paper_h = self._paper_settings()
        layout_name = f"{paper}-{orientation[:1].upper()}"
        try:
            psp = self.doc.layouts.get(layout_name)
        except Exception:
            psp = self.doc.layouts.new(layout_name)
        psp.page_setup(
            size=(paper_w, paper_h),
            margins=(0.0, 0.0, 0.0, 0.0),
            units="mm",
            rotation=0,
            scale=(1.0, 1.0),
            name=paper,
            device="DWG to PDF.pc3",
        )
        psp.plot_centered(True)

        frame_layer = "PIT_FRAME"
        title_layer = "PIT_TITLE"
        text_layer = "PIT_TEXT"
        for layer in (frame_layer, title_layer, text_layer, "PIT_VIEWPORT"):
            self._add_layer(layer)

        margin = 10.0
        title_h = 36.0
        view_x0, view_y0 = margin, margin + title_h + 4.0
        view_w, view_h = paper_w - 2 * margin, paper_h - view_y0 - margin

        # Paper border and title block.
        psp.add_lwpolyline([(margin, margin), (paper_w - margin, margin), (paper_w - margin, paper_h - margin), (margin, paper_h - margin)], close=True, dxfattribs={"layer": frame_layer})
        title_y = margin
        title_x = paper_w - margin - min(300.0, paper_w * 0.44)
        title_w = paper_w - margin - title_x
        if "PIT_TITLE_BLOCK_GEOMETRY" not in self.doc.blocks:
            block = self.doc.blocks.new("PIT_TITLE_BLOCK_GEOMETRY")
            block.add_lwpolyline([(0, 0), (title_w, 0), (title_w, title_h), (0, title_h)], close=True, dxfattribs={"layer": frame_layer})
            for yy in (9.0, 18.0, 27.0):
                block.add_line((0, yy), (title_w, yy), dxfattribs={"layer": frame_layer})
            for xx in (title_w * 0.58, title_w * 0.76, title_w * 0.88):
                block.add_line((xx, 0), (xx, title_h), dxfattribs={"layer": frame_layer})
        psp.add_blockref("PIT_TITLE_BLOCK_GEOMETRY", (title_x, title_y), dxfattribs={"layer": frame_layer})

        def pstext(x: float, y: float, value: str, height: float = 3.0, layer: str = text_layer) -> None:
            entity = psp.add_text(str(value)[:160], height=height, dxfattribs={"layer": layer, "style": "PIT_CN"})
            entity.set_placement((x, y), align=TextEntityAlignment.LEFT)

        pstext(margin + 3, paper_h - margin - 6, str(title.get("title")), 4.5, title_layer)
        pstext(title_x + 3, title_y + 29.5, f"工程：{title.get('projectName')}", 2.8)
        pstext(title_x + 3, title_y + 20.5, f"图名：{title.get('title')}", 2.8)
        pstext(title_x + 3, title_y + 11.5, f"阶段：{title.get('stage')}", 2.8)
        pstext(title_x + title_w * 0.60, title_y + 29.5, f"图号：{title.get('sheetNo')}", 2.6)
        pstext(title_x + title_w * 0.60, title_y + 20.5, f"比例：{title.get('scale')}", 2.6)
        pstext(title_x + title_w * 0.60, title_y + 11.5, f"设计：{title.get('designer')}", 2.6)
        pstext(title_x + title_w * 0.60, title_y + 3.0, f"校核：{title.get('checker')}", 2.6)
        pstext(title_x + title_w * 0.79, title_y + 3.0, f"审定：{title.get('approver')}", 2.6)

        if not math.isfinite(self.min_x):
            self.min_x, self.max_x, self.min_y, self.max_y = 0.0, 100000.0, 0.0, 60000.0
        model_w = max(self.max_x - self.min_x, 1000.0)
        model_h = max(self.max_y - self.min_y, 1000.0)
        model_cx = (self.min_x + self.max_x) / 2.0
        model_cy = (self.min_y + self.max_y) / 2.0
        denominator = _parse_scale(str(title.get("scale")), 100)
        view_height = view_h * denominator
        required = model_h * 1.08
        required_by_width = model_w * 1.08 * (view_h / max(view_w, 1.0))
        view_height = max(view_height, required, required_by_width)
        vp = psp.add_viewport(
            center=(view_x0 + view_w / 2.0, view_y0 + view_h / 2.0),
            size=(view_w, view_h),
            view_center_point=(model_cx, model_cy),
            view_height=view_height,
            dxfattribs={"layer": "PIT_VIEWPORT"},
        )
        vp.dxf.status = 1
        vp.dxf.flags = int(vp.dxf.flags or 0) | 16384  # viewport locked

        template = title.get("template") or {}
        issue_mode = str(template.get("issueMode") or "review")
        if issue_mode != "construction":
            pstext(margin + 4, margin + 4, "审查版 / AI-DRAFT / 不得直接用于施工", 3.2, title_layer)

    def write(self, path: Path) -> None:
        self._add_paperspace()
        self.doc.header["$PROJECTNAME"] = str((self._title or {}).get("projectName") or "PitGuard")
        self.doc.header["$TDCREATE"] = 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.saveas(path)
