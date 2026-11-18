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
