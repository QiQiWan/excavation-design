"""计算书解析：挖深、等级、土层表（取最后一组）、桩参数。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents.calculation_book_autopilot import parse_book  # noqa: E402

SAMPLE = """
1-1剖面 土层参数
编号 土层名称 厚度 重度 c φ
①-0 填土 0.85 18 10 15
⑩-2 岩石(强风化) 2 21 30 30

4.3 2-2剖面
工程概况 基坑设计深度 4.9m, 基坑安全等级为三级。
土层参数
①-0 填土 1.67 18 10 15
② 淤泥 2.6 16.22 5.55 6.36
⑩-2 岩石(强风化) 0.5 21 30 30
⑩-3 岩石(中风化) 50 22 50 30
挡墙类型: 灌注桩; 桩径: 0.7m; 桩间距: 0.9m; 桩长: 7.6m
"""


def test_parse_depth_grade_and_last_layer_block() -> None:
    parsed = parse_book(SAMPLE)
    assert parsed["depthM"] == 4.9
    assert parsed["depthFromBook"] is True
    assert parsed["safetyGrade"] == "三"
    assert len(parsed["layers"]) == 4
    assert parsed["layers"][1]["code"] == "②"
    assert parsed["layers"][1]["cohesionKpa"] == 5.55
    assert parsed["layers"][1]["phiDeg"] == 6.36


def test_parse_pile_parameters() -> None:
    parsed = parse_book(SAMPLE)
    assert parsed["pile"] == {"diameter": 0.7, "spacing": 0.9, "length": 7.6}


def test_defaults_when_book_lacks_depth() -> None:
    parsed = parse_book("仅有一份说明，没有开挖深度。")
    assert parsed["depthM"] == 5.0
    assert parsed["depthFromBook"] is False
    assert parsed["layers"] == []
