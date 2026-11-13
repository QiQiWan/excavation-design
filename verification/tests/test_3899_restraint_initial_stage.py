"""初始（未开挖）阶段不应被判成「支撑层未闭合」。"""
from __future__ import annotations

from app.calculation.engine import _restraint_status


def test_initial_stage_without_excavation_is_not_a_restraint_failure():
    # 真实回归：初始状态 excavation_elevation=0、无支撑，曾被判 fail，
    # 使每个项目凭空多出 4 条硬失败，A7 永远进不去。
    assert _restraint_status(0.0, [], 'fail') == 'pass'
    assert _restraint_status(0.0, [], 'manual_review') == 'pass'


def test_cantilever_stage_without_supports_is_not_a_load_path_failure():
    # 真实回归：第 1 道支撑安装前的悬臂开挖阶段无支撑，
    # 曾被判「支撑层未闭合」，同样让 A7 永远进不去。
    assert _restraint_status(2.0, [], 'fail') == 'pass'
    assert _restraint_status(2.0, [object()], 'pass') == 'pass'
    assert _restraint_status(2.0, [object()], 'manual_review') == 'manual_review'


def test_zero_depth_with_supports_keeps_the_audit_verdict():
    # 有支撑却没有开挖，属于异常工况，不能顺手判通过。
    assert _restraint_status(0.0, [object()], 'fail') == 'fail'
