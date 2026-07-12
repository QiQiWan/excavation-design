from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.schemas.domain import CalculationCase, Project, StageCalculationResult
from app.services.support_layout import unrestrained_concave_face_codes


def _status_rank(status: str | None) -> int:
    return {"pass": 0, "manual_review": 1, "warning": 2, "fail": 3}.get(str(status or ""), 1)


def _root_cause(code: str, title: str, description: str, *, severity: str, objects: list[str] | None = None, action: str) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "description": description,
        "severity": severity,
        "objectIds": objects or [],
        "recommendedAction": action,
    }


def _support_counts(project: Project) -> tuple[dict[str, dict[int, int]], dict[str, int]]:
    by_face_level: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    system = project.retaining_system
    if not system:
        return {}, {}
    for support in system.supports:
        seen: set[str] = set()
        for face in (support.start_face_code, support.end_face_code):
            if face and face not in seen:
                by_face_level[str(face)][int(support.level_index)] += 1
                totals[str(face)] += 1
                seen.add(str(face))
    return ({face: dict(sorted(levels.items())) for face, levels in by_face_level.items()}, dict(totals))


def _wall_envelopes(project: Project, stage_results: list[StageCalculationResult]) -> list[dict[str, Any]]:
    by_segment: dict[str, dict[str, float | str | None]] = {}
    for item in stage_results:
        wall = item.wall_internal_force
        if not wall:
            continue
        row = by_segment.setdefault(
            str(item.segment_id),
            {
                "segmentId": str(item.segment_id),
                "maxMomentKnMPerM": 0.0,
                "maxShearKnPerM": 0.0,
                "maxDisplacementMm": 0.0,
                "governingStageId": None,
            },
        )
        values = {
            "maxMomentKnMPerM": abs(float(wall.max_moment_design or wall.max_moment or 0.0)),
            "maxShearKnPerM": abs(float(wall.max_shear_design or wall.max_shear or 0.0)),
            "maxDisplacementMm": abs(float(wall.max_displacement or 0.0)),
        }
        if values["maxDisplacementMm"] >= float(row["maxDisplacementMm"] or 0.0):
            row["governingStageId"] = str(item.stage_id)
        for key, value in values.items():
            row[key] = max(float(row[key] or 0.0), value)
    if project.retaining_system:
        metadata = {
            str(wall.segment_id): {
                "wallId": wall.id,
                "wallCode": wall.panel_code,
                "faceCode": wall.design_face_code,
            }
            for wall in project.retaining_system.diaphragm_walls
        }
        for segment_id, row in by_segment.items():
            row.update(metadata.get(segment_id, {}))
    return list(by_segment.values())


def build_calculation_diagnostics(
    project: Project,
    case: CalculationCase,
    stage_results: list[StageCalculationResult],
    checks: list[dict[str, Any]],
    *,
    topology_preflight: dict[str, Any] | None = None,
    support_case_sync: dict[str, Any] | None = None,
    governing_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    topology_preflight = dict(topology_preflight or {})
    support_case_sync = dict(support_case_sync or {})
    by_face_level, face_totals = _support_counts(project)
    missing_faces = unrestrained_concave_face_codes(project.excavation, project.retaining_system.supports) if project.excavation and project.retaining_system else []
    envelopes = _wall_envelopes(project, stage_results)
    for row in envelopes:
        display_face = str(row.get("faceCode") or "")
        support_face = str(row.get("segmentId") or display_face)
        row["supportFaceCode"] = support_face
        row["directSupportCount"] = face_totals.get(support_face, face_totals.get(display_face, 0))
        row["directSupportCountByLevel"] = by_face_level.get(support_face, by_face_level.get(display_face, {}))
        row["supportCoverageStatus"] = "fail" if support_face in missing_faces else ("warning" if support_face and int(row["directSupportCount"]) == 0 else "pass")

    roots: list[dict[str, Any]] = []
    if topology_preflight.get("changed"):
        faces = list(topology_preflight.get("missingFacesBefore") or topology_preflight.get("missingFaces") or [])
        roots.append(_root_cause(
            "UNRESTRAINED_CONCAVE_RETURN_WALL",
            "凹角回墙缺少直接支撑",
            f"计算前发现回墙 {', '.join(faces) or '局部墙面'} 未形成法向直接传力路径，已增补 {int(topology_preflight.get('addedSupportCount') or 0)} 根局部次对撑。",
            severity="fail",
            objects=faces,
            action="保留自动增补支撑，复核交叉节点、临时立柱和施工净空后重新计算。",
        ))
    elif missing_faces:
        roots.append(_root_cause(
            "UNRESTRAINED_CONCAVE_RETURN_WALL",
            "凹角回墙仍缺少直接支撑",
            f"墙面 {', '.join(missing_faces)} 未检测到直接支撑端点。",
            severity="fail",
            objects=missing_faces,
            action="运行诊断修复，生成局部法向次对撑并同步施工工况。",
        ))
    if support_case_sync.get("synchronized"):
        roots.append(_root_cause(
            "STALE_STAGE_SUPPORT_REFERENCES",
            "施工阶段支撑引用已失效",
            "支撑方案更新后施工阶段仍引用旧构件 ID，本次计算已按支撑层和当前拓扑重建激活关系。",
            severity="fail",
            action="核对支撑安装、换撑和拆撑时序，确认后保存新工况。",
        ))

    fail_rules = Counter(str(item.get("ruleId") or "UNKNOWN") for item in checks if item.get("status") == "fail")
    warning_rules = Counter(str(item.get("ruleId") or "UNKNOWN") for item in checks if item.get("status") == "warning")
    if any("SHEAR" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "WALL_SHEAR_CAPACITY",
            "围护墙抗剪承载力不足",
            "控制墙段的设计剪力超过当前墙厚与混凝土抗剪承载力。",
            severity="fail",
            action="先检查墙面支撑覆盖和施工阶段，再进行墙厚、混凝土强度或局部抗剪构造升级。",
        ))
    if any("MAINBAR" in rule or "FLEXURE" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "WALL_REBAR_OR_FLEXURE",
            "墙体受弯配筋或构造不满足",
            "控制弯矩对应的纵向钢筋面积、净距或双排布置未满足当前规则集。",
            severity="fail",
            action="排除异常工况后，执行分区双筋设计或增大墙厚，禁止只压缩钢筋间距。",
        ))
    if any("DEFORMATION" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "WALL_DEFORMATION",
            "围护墙变形超限",
            "控制阶段墙体位移超过项目限值。",
            severity="fail",
            action="核对开挖卸载、支撑激活和被动区土弹簧，再优化支撑层位或刚度。",
        ))

    counts = Counter(str(item.get("status") or "manual_review") for item in checks)
    status = "fail" if counts.get("fail", 0) else ("warning" if counts.get("warning", 0) or roots else "pass")
    prior = project.calculation_results[-1] if project.calculation_results else None
    comparison = None
    current = governing_values or {}
    if prior:
        old = prior.governing_values
        comparison = {
            "previousResultId": prior.id,
            "maxDisplacementMm": {"before": old.max_displacement, "after": current.get("maxDisplacement")},
            "maxWallMomentKnMPerM": {"before": old.max_wall_moment, "after": current.get("maxWallMoment")},
            "maxWallShearKnPerM": {"before": old.max_wall_shear, "after": current.get("maxWallShear")},
            "maxSupportAxialForceKn": {"before": old.max_support_axial_force, "after": current.get("maxSupportAxialForce")},
            "failCount": {"before": int((prior.check_summary or {}).get("fail") or 0), "after": int(counts.get("fail", 0))},
            "warningCount": {"before": int((prior.check_summary or {}).get("warning") or 0), "after": int(counts.get("warning", 0))},
        }

    actions: list[dict[str, Any]] = []
    if topology_preflight.get("changed"):
        actions.append({"code": "REVIEW_ADDED_SUPPORTS", "label": "复核新增局部次对撑", "targetStep": "retaining", "primary": True})
    if support_case_sync.get("synchronized"):
        actions.append({"code": "REVIEW_STAGE_SEQUENCE", "label": "复核更新后的施工工况", "targetStep": "calculation", "primary": not actions})
    if counts.get("fail", 0):
        actions.append({"code": "LOCATE_GOVERNING_FAILURE", "label": "定位控制墙段和条文", "targetStep": "calculation", "primary": not actions})
    else:
        actions.append({"code": "CONTINUE_REBAR_REVIEW", "label": "进入配筋与出图复核", "targetStep": "deliverables", "primary": not actions})

    return {
        "status": status,
        "caseId": case.id,
        "topologyPreflight": topology_preflight,
        "supportTopologySynchronization": support_case_sync,
        "rootCauses": roots,
        "wallCoverage": sorted(envelopes, key=lambda row: (-_status_rank(str(row.get("supportCoverageStatus"))), -float(row.get("maxDisplacementMm") or 0.0))),
        "issueGroups": {
            "failRules": [{"ruleId": key, "count": value} for key, value in fail_rules.most_common()],
            "warningRules": [{"ruleId": key, "count": value} for key, value in warning_rules.most_common()],
        },
        "checkSummary": {
            "pass": int(counts.get("pass", 0)),
            "fail": int(counts.get("fail", 0)),
            "warning": int(counts.get("warning", 0)),
            "manualReview": int(counts.get("manual_review", 0)),
        },
        "comparisonWithPrevious": comparison,
        "nextActions": actions,
        "professionalBoundary": "自动诊断用于定位计算链路、支撑拓扑和构件控制项；新增支撑及计算结果仍需岩土、结构和施工专业复核。",
    }
