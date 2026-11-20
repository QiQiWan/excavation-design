"""导出的钢筋必须是圆截面实体而不是方棍，并带材质颜色。

会议反馈“导出模型里钢筋呈现效果差”，根因是每根钢筋都用矩形轮廓挤出，
查看器里看起来像一截截木条；同时没有任何表面样式，钢筋和混凝土同色。
"""
from __future__ import annotations

from app.ifc.exporter import IfcWriter, _add_reinforcing_bar


def _writer_with_style() -> IfcWriter:
    writer = IfcWriter()
    writer.rebar_style_id = writer.surface_style("Rebar steel", 0.72, 0.26, 0.20)
    return writer


def _add(writer: IfcWriter, *, vertical: bool, refdir: tuple[float, float, float], length: float) -> None:
    _add_reinforcing_bar(
        writer,
        owner=1,
        context=1,
        storey_place=0,
        name="RB-TEST",
        description="test bar",
        x=0.0,
        y=0.0,
        z=0.0,
        refdir=refdir,
        length=length,
        diameter_mm=20.0,
        grade="HRB400",
        properties={},
        vertical=vertical,
    )


def test_bars_use_circular_section_not_rectangular_boxes() -> None:
    writer = _writer_with_style()
    _add(writer, vertical=True, refdir=(0, 0, 1), length=2.5)
    _add(writer, vertical=False, refdir=(1, 0, 0), length=6.0)

    text = "\n".join(writer.lines)
    assert text.count("IFCCIRCLEPROFILEDEF") == 2
    assert "IFCRECTANGLEPROFILEDEF" not in text


def test_bar_radius_matches_nominal_diameter() -> None:
    writer = _writer_with_style()
    _add(writer, vertical=True, refdir=(0, 0, 1), length=2.5)

    text = "\n".join(writer.lines)
    # φ20 → 半径 0.01 m
    assert ",0.01)" in text


def test_bars_carry_a_visible_surface_style() -> None:
    writer = _writer_with_style()
    _add(writer, vertical=True, refdir=(0, 0, 1), length=2.5)
    _add(writer, vertical=False, refdir=(0, 1, 0), length=4.0)

    text = "\n".join(writer.lines)
    assert "IFCCOLOURRGB" in text
    assert "IFCSURFACESTYLE" in text
    assert text.count("IFCSTYLEDITEM") == 2


def test_horizontal_bar_extrudes_along_its_own_axis() -> None:
    writer = _writer_with_style()
    _add(writer, vertical=False, refdir=(0, 1, 0), length=4.0)

    lines = writer.lines
    directions = [line for line in lines if "IFCDIRECTION" in line]
    # 钢筋方向作为局部 Z 轴，参考方向取水平面内的垂线
    assert any("(0.,1.,0.)" in line for line in directions), directions
    assert any("(-1.,0.,0.)" in line for line in directions), directions


def test_wall_cage_emits_the_whole_grid() -> None:
    from types import SimpleNamespace

    from app.ifc.exporter import _emit_wall_cage_bars

    cage = {
        "start": {"x": 0.0, "y": 0.0, "z": 0.0},
        "end": {"x": 6.0, "y": 0.0, "z": 0.0},
        "bottomElevation": -10.0,
        "heightM": 10.0,
        "thicknessM": 0.7,
        "coverM": 0.07,
        "faces": [
            {"face": "outer", "diameterMm": 25, "spacingMm": 140, "estimatedVerticalBarCount": 43},
            {"face": "inner", "diameterMm": 25, "spacingMm": 140, "estimatedVerticalBarCount": 43},
        ],
        "horizontal": {"diameterMm": 16, "spacingMm": 150, "estimatedBarCountPerFace": 67},
        "ties": {"diameterMm": 12, "spacingMm": 350},
    }
    writer = _writer_with_style()
    bars = _emit_wall_cage_bars(
        writer,
        owner=1,
        context=1,
        storey_place=0,
        wall=SimpleNamespace(rebar_grade="HRB400", thickness=0.7,
                             bottom_elevation=-10.0, top_elevation=0.0, panel_code="DW-1"),
        panel={"panelCode": "S1-P01"},
        cage=cage,
    )

    # 两面竖向 86 + 两面水平 134 + 拉结筋 29
    assert len(bars) == 249
    text = "\n".join(writer.lines)
    assert text.count("IFCREINFORCINGBAR") == 249
    assert text.count("IFCCIRCLEPROFILEDEF") == 249
    assert "IFCRECTANGLEPROFILEDEF" not in text


def test_support_cage_emits_longitudinal_bars_and_closed_hoops() -> None:
    from types import SimpleNamespace

    from app.ifc.exporter import _emit_support_cage_bars

    groups = [
        SimpleNamespace(bar_type="longitudinal", diameter=28.0, spacing=None, count=16, grade="HRB400"),
        SimpleNamespace(bar_type="distribution", diameter=16.0, spacing=200.0, count=None, grade="HRB400"),
        SimpleNamespace(bar_type="stirrup", diameter=12.0, spacing=160.0, count=None, grade="HRB400"),
    ]
    support = SimpleNamespace(
        code="DB-L1-1",
        start=SimpleNamespace(x=0.0, y=0.0),
        end=SimpleNamespace(x=3.0, y=0.0),
        section=SimpleNamespace(width=1.2, height=1.2),
        elevation=-1.0,
        reinforcement=groups,
    )
    writer = _writer_with_style()
    bars = _emit_support_cage_bars(
        writer, owner=1, context=1, storey_place=0, support=support, max_per_support=400,
    )

    # 纵筋 16 + 两侧分布筋 6 行 × 2 = 12 + 箍筋 19 站 × 4 段
    assert len(bars) == 16 + 12 + 76
    text = "\n".join(writer.lines)
    assert text.count("IFCCIRCLEPROFILEDEF") == len(bars)
    assert "IFCRECTANGLEPROFILEDEF" not in text


def test_beam_cage_emits_longitudinal_and_stirrups() -> None:
    from types import SimpleNamespace

    from app.ifc.exporter import _emit_beam_cage_bars

    groups = [
        SimpleNamespace(bar_type="longitudinal", diameter=25.0, spacing=125.0, count=12, grade="HRB400"),
        SimpleNamespace(bar_type="distribution", diameter=14.0, spacing=200.0, count=None, grade="HRB400"),
        SimpleNamespace(bar_type="stirrup", diameter=10.0, spacing=160.0, count=None, grade="HRB400"),
    ]
    beam = SimpleNamespace(
        code="WB-L1-S1",
        axis=SimpleNamespace(points=[SimpleNamespace(x=0.0, y=0.0), SimpleNamespace(x=60.0, y=0.0)]),
        section=SimpleNamespace(width=0.9, height=0.7),
        elevation=-1.5,
        reinforcement=groups,
    )
    writer = _writer_with_style()
    bars = _emit_beam_cage_bars(writer, owner=1, context=1, storey_place=0, beam=beam)

    # 纵筋 12 + 两侧分布 2×4 + 箍筋 120 站 × 4 段
    assert len(bars) == 12 + 8 + 480
    text = "\n".join(writer.lines)
    assert text.count("IFCCIRCLEPROFILEDEF") == len(bars)
    assert "IFCRECTANGLEPROFILEDEF" not in text


def test_wall_cover_follows_design_not_thickness_fraction() -> None:
    from types import SimpleNamespace

    from app.ifc.rebar_visualization import _wall_cover_m

    wall = SimpleNamespace(thickness=0.7)
    # 旧公式 0.7 × 0.38 = 266 mm，两片网会被挤到墙心
    assert 0.05 <= _wall_cover_m(wall) <= 0.075
    assert abs(_wall_cover_m(wall, 0.05) - 0.05) < 1e-9
    assert _wall_cover_m(wall, 0.5) <= 0.1


def test_pile_row_spec_reads_the_book_parameters() -> None:
    from types import SimpleNamespace

    from app.ifc.exporter import _pile_row_spec

    project = SimpleNamespace(advanced_engineering={
        "calculationBookAutopilot": {"parsed": {
            "pile": {"diameter": 0.7, "spacing": 0.9, "length": 7.6},
            "pileCage": {"mainDiameterMm": 25, "barCount": 18, "hoopDiameterMm": 8,
                         "hoopSpacingMm": 128, "clearCoverMm": 50},
        }}})
    spec = _pile_row_spec(project)
    assert spec["diameterM"] == 0.7 and spec["spacingM"] == 0.9
    assert spec["mainBarCount"] == 18 and spec["coverM"] == 0.05


def test_pile_row_exports_circular_piles_and_cages() -> None:
    from types import SimpleNamespace

    from app.ifc.exporter import _emit_pile_row

    wall = SimpleNamespace(
        panel_code="DW-S1-001",
        top_elevation=0.0,
        bottom_elevation=-10.9,
        axis=SimpleNamespace(points=[SimpleNamespace(x=0.0, y=0.0), SimpleNamespace(x=9.0, y=0.0)]),
    )
    spec = {"diameterM": 0.7, "spacingM": 0.9, "lengthM": 7.6,
            "mainDiameterMm": 25.0, "mainBarCount": 18, "barSpacingMm": 120.0,
            "hoopDiameterMm": 8.0, "hoopSpacingMm": 128.0, "coverM": 0.05}
    writer = _writer_with_style()
    piles, bars = _emit_pile_row(
        writer, owner=1, context=1, storey_place=0, wall=wall, spec=spec,
    )

    # 9 m / 0.9 m 间距 → 11 根桩；每根 18 主筋 + 6 环×8 + 4 加强箍×8
    assert len(piles) == 11
    assert len(bars) == 11 * (18 + 48 + 32)
    text = "\n".join(writer.lines)
    assert text.count("IFCPILE") == 11
    assert text.count("IFCCIRCLEPROFILEDEF") == 11 + len(bars)
    assert "IFCRECTANGLEPROFILEDEF" not in text
