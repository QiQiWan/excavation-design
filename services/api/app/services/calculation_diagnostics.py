from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.schemas.domain import CalculationCase, Project, StageCalculationResult
from app.services.support_layout import unrestrained_concave_face_codes


def _status_rank(status: str | None) -> int:
    return {"pass": 0, "indirect_corner_transfer": 1, "manual_review": 1, "warning": 2, "fail": 3}.get(str(status or ""), 1)


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
    wall_embedment_preflight: dict[str, Any] | None = None,
    governing_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    topology_preflight = dict(topology_preflight or {})
    support_case_sync = dict(support_case_sync or {})
    wall_embedment_preflight = dict(wall_embedment_preflight or {})
    by_face_level, face_totals = _support_counts(project)
    missing_faces = unrestrained_concave_face_codes(project.excavation, project.retaining_system.supports) if project.excavation and project.retaining_system else []
    envelopes = _wall_envelopes(project, stage_results)
    for row in envelopes:
        display_face = str(row.get("faceCode") or "")
        support_face = str(row.get("segmentId") or display_face)
        row["supportFaceCode"] = support_face
        row["directSupportCount"] = face_totals.get(support_face, face_totals.get(display_face, 0))
        row["directSupportCountByLevel"] = by_face_level.get(support_face, by_face_level.get(display_face, {}))
        if support_face in missing_faces:
            row["supportCoverageStatus"] = "fail"
            row["supportCoverageMethod"] = "missing_direct_or_valid_corner_transfer_path"
        elif support_face and int(row["directSupportCount"]) == 0:
            # Short return/step walls can transfer into the closed perimeter wale
            # and adjacent supported faces.  Keep this visible as an indirect
            # structural path instead of presenting it as an unexplained warning.
            row["supportCoverageStatus"] = "indirect_corner_transfer"
            row["supportCoverageMethod"] = "closed_perimeter_wale_and_adjacent_supported_faces"
        else:
            row["supportCoverageStatus"] = "pass"
            row["supportCoverageMethod"] = "direct_support_endpoint"

    roots: list[dict[str, Any]] = []
    concave_repair = dict(topology_preflight.get("concaveReturnRepair") or {})
    wale_repair = dict(topology_preflight.get("waleSupportBayRepair") or {})
    if concave_repair.get("changed"):
        faces = list(concave_repair.get("missingFacesBefore") or concave_repair.get("missingFaces") or [])
        roots.append(_root_cause(
            "UNRESTRAINED_CONCAVE_RETURN_WALL_REPAIRED",
            "凹角回墙传力路径已自动补强",
            f"计算前发现回墙 {', '.join(faces) or '局部墙面'} 缺少法向直接传力路径，已增补 {int(concave_repair.get('addedSupportCount') or 0)} 根局部次对撑并重新组装工况。",
            severity="warning",
            objects=faces,
            action="复核新增支撑的交叉节点、临时立柱、净空和施工顺序；通过后保留当前拓扑。",
        ))
    if wale_repair.get("changed"):
        faces = list(wale_repair.get("failingFaces") or [])
        audit_before = dict(wale_repair.get("auditBefore") or {})
        audit_after = dict(wale_repair.get("auditAfter") or {})
        roots.append(_root_cause(
            "WALE_SUPPORT_BAY_REPAIRED",
            "围檩支点间距已按强度前置规则修复",
            (
                f"墙面 {', '.join(faces) or '局部墙面'} 的围檩有效支点间距超过硬上限，"
                f"已增补 {int(wale_repair.get('addedSupportCount') or 0)} 根两端落墙的平行角撑、端部长斜撑或直接对撑；"
                f"最大间距由 {float(audit_before.get('maxBayM') or 0.0):.2f} m 调整为 "
                f"{float(audit_after.get('maxBayM') or 0.0):.2f} m。"
            ),
            severity="warning",
            objects=faces,
            action="复核新增墙—墙支撑的端部节点、斜向分力、临时立柱服务范围和出土通道后，将修复后的围檩支点作为设计基准。",
        ))
    if not concave_repair and topology_preflight.get("changed") and not wale_repair:
        faces = list(topology_preflight.get("missingFacesBefore") or topology_preflight.get("missingFaces") or [])
        roots.append(_root_cause(
            "SUPPORT_TOPOLOGY_REPAIRED",
            "支撑拓扑已自动修复",
            f"计算前已增补 {int(topology_preflight.get('addedSupportCount') or 0)} 根支撑并同步施工工况。",
            severity="warning",
            objects=faces,
            action="复核新增构件、节点、立柱和施工净空后保留当前拓扑。",
        ))
    elif missing_faces and not concave_repair.get("changed"):
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
            "施工阶段支撑引用已自动同步",
            "支撑方案更新后原工况引用旧构件 ID；本次计算已按支撑层、标高和当前拓扑重建激活关系，旧引用未参与计算。",
            severity="warning",
            action="核对同步后的支撑安装、换撑和拆撑时序；确认后将新工况保存为项目基准。",
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
    if any("EMBEDMENT-STABILITY" in rule for rule in fail_rules):
        before = wall_embedment_preflight.get("beforeMinimumFactor")
        after = wall_embedment_preflight.get("afterMinimumFactor")
        before_bottom = wall_embedment_preflight.get("beforeBottomElevationM")
        after_bottom = wall_embedment_preflight.get("afterBottomElevationM")
        locked = int(wall_embedment_preflight.get("lockedFailureCount") or 0)
        roots.append(_root_cause(
            "WALL_EMBEDMENT_STABILITY",
            "围护墙墙趾嵌固稳定未闭合",
            (
                f"全部/多幅墙出现同一嵌固规则失败，属于共用墙趾标高控制问题；"
                f"最小筛查系数 {before if before is not None else '-'} → {after if after is not None else '-'}，"
                f"共用墙趾标高 {before_bottom if before_bottom is not None else '-'}m → {after_bottom if after_bottom is not None else '-'}m。"
                + (f"其中 {locked} 幅墙趾已锁定，系统未自动覆盖。" if locked else "")
            ),
            severity="fail",
            objects=[str(row.get("wallCode") or row.get("wallId")) for row in wall_embedment_preflight.get("rowsAfter", []) if row.get("status") == "fail"],
            action="恢复勘察/源模型墙趾控制值，或运行共用墙趾嵌固设计；同时复核被动区土参数、地下水和施工阶段。",
        ))
    if any("WALE" in rule and ("FLEXURE" in rule or "SHEAR" in rule or "DEFLECTION" in rule) for rule in fail_rules):
        roots.append(_root_cause(
            "WALE_MEMBER_CAPACITY",
            "围檩强度或刚度仍未闭环",
            "围檩多工况包络超过当前截面、配筋或挠度控制值。",
            severity="fail",
            action="先复核支点间距和拆换撑传力路径，再执行截面与配筋自动迭代；达到工程上限仍不满足时更换体系。",
        ))
    if any("QUALITY-SUPPORT_CROSSING" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "NON_RING_SUPPORT_CROSSING",
            "普通水平支撑存在平面穿越",
            "同层非环形支撑在跨中相互穿越。即使交点附近存在立柱，连续杆件穿越仍会造成节点构造、施工顺序和内力模型不一致。",
            severity="fail",
            action="重新生成两端落在围护墙、围檩或闭合环梁上的直撑/长斜撑；普通轴压支撑禁止终止于另一根支撑跨中。",
        ))
    if any("QUALITY-SUPPORT_OUTSIDE_EXCAVATION" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "SUPPORT_OUTSIDE_EXCAVATION",
            "支撑中心线穿出基坑轮廓",
            "支撑线在凹角、阶梯段或回折边处离开实际开挖域。",
            severity="fail",
            action="按局部主轴和真实多边形求交重新生成，禁止使用包围盒端点代替墙面交点。",
        ))
    if any("QUALITY-WALE_SUPPORT_BAY" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "WALE_SUPPORT_BAY_HARD_GATE",
            "围檩直接支点间距超过硬上限",
            "局部墙面缺少可追溯的直接支点，截面放大不能替代清晰传力路径。",
            severity="fail",
            action="采用非交叉直对撑、平行角撑族、端部长斜撑或调整主对撑站位，直至每层每面墙的直接支点间距满足上限。",
        ))
    if any("QUALITY-SUPPORT_STATION_CLUSTER" in rule or "SUPPORT_STATION_CLUSTER" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "SUPPORT_STATION_CLUSTER",
            "支撑站位在局部变宽或折点附近过度聚集",
            "多个支撑站位被重复插入到同一宽度突变区，导致围檩节点拥挤、工程量增加，且不能有效降低端墙围檩控制跨。",
            severity="fail",
            action="按局部短跨和等效分担面积重新生成自适应站位；折点附近只移动最近站位，端部围檩由平行角撑族或墙—墙长斜撑闭合。",
        ))
    if any("QUALITY-SUPPORT_WALL_CLEARANCE" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "SUPPORT_WALL_CLEARANCE",
            "支撑中心线与围护墙净距不足",
            "支撑截面或中心线侵入墙体/围檩构造区。",
            severity="fail",
            action="保留墙面连接点，通过围檩刚臂将支撑中心线向坑内退让，并重新检查短段可施工长度。",
        ))
    if any("QUALITY-TEMPORARY_COLUMN" in rule for rule in fail_rules):
        roots.append(_root_cause(
            "TEMPORARY_COLUMN_LOAD_PATH",
            "长跨或支撑节点缺少临时立柱",
            "支撑有效无侧向支承长度超过控制值，或长跨构件缺少明确的竖向承托与平面外稳定体系。",
            severity="fail",
            action="在长跨控制点生成临时立柱/立柱桩并记录服务构件；临时立柱不得用于掩盖普通支撑跨中承受平面内横向集中力的问题。",
        ))

    # Never leave a hard failure without an engineering diagnosis.  Unknown
    # rule IDs are grouped and surfaced with their original identifiers so the
    # frontend does not fall back to an opaque 'unclassified' card.
    if fail_rules and not any(str(item.get("severity")) == "fail" for item in roots):
        top = ", ".join(f"{rule}×{count}" for rule, count in fail_rules.most_common(5))
        roots.append(_root_cause(
            "OTHER_HARD_CHECK_FAILURES",
            "存在尚未闭环的硬性校核",
            f"控制规则：{top}。",
            severity="fail",
            action="打开校核清单定位对象、工况、计算值和限值；修复后重新计算，旧结果不得用于出图。",
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
        actions.append({"code": "REVIEW_ADDED_SUPPORTS", "label": "复核强度前置拓扑修复", "targetStep": "retaining", "primary": True})
    if support_case_sync.get("synchronized"):
        actions.append({"code": "REVIEW_STAGE_SEQUENCE", "label": "复核更新后的施工工况", "targetStep": "calculation", "primary": not actions})
    if counts.get("fail", 0):
        actions.append({"code": "LOCATE_GOVERNING_FAILURE", "label": "定位控制墙段和条文", "targetStep": "calculation", "primary": not actions})
    else:
        actions.append({"code": "CONTINUE_REBAR_REVIEW", "label": "进入配筋与出图复核", "targetStep": "deliverables", "primary": not actions})

    wale_before = dict((wale_repair.get("auditBefore") or {}))
    wale_after = dict((wale_repair.get("auditAfter") or {}))
    strength_design_loop = {
        "enabled": bool(getattr(project.design_settings, "auto_strength_design_enabled", True)),
        "iterationLimit": int(getattr(project.design_settings, "max_design_iterations", 3) or 3),
        "topologyAdjusted": bool(topology_preflight.get("changed")),
        "addedSupportCount": int(topology_preflight.get("addedSupportCount") or 0),
        "waleBayBeforeM": wale_before.get("maxBayM"),
        "waleBayAfterM": wale_after.get("maxBayM"),
        "strengthStatus": "fail" if any(("FLEXURE" in rule or "SHEAR" in rule or "AXIAL" in rule) for rule in fail_rules) else "pass",
        "stiffnessStatus": "fail" if any(("DEFORMATION" in rule or "DEFLECTION" in rule) for rule in fail_rules) else "pass",
        "topologyStatus": "fail" if any(rule.startswith("QUALITY-SUPPORT_") or "WALE_SUPPORT_BAY" in rule for rule in fail_rules) else "pass",
        "loadPathPolicy": "replacement slab/waler elevations remain in vertical tributary-band partition during support removal stages",
        "waleBoundaryPolicy": "closed-perimeter rigid corner joints restrain face-wale end bays; direct strut reactions retain conservative qL equilibrium",
        "wallEmbedment": {
            "enabled": bool(wall_embedment_preflight.get("enabled", True)),
            "status": wall_embedment_preflight.get("status"),
            "changed": bool(wall_embedment_preflight.get("changed")),
            "beforeBottomElevationM": wall_embedment_preflight.get("beforeBottomElevationM"),
            "afterBottomElevationM": wall_embedment_preflight.get("afterBottomElevationM"),
            "beforeMinimumFactor": wall_embedment_preflight.get("beforeMinimumFactor"),
            "afterMinimumFactor": wall_embedment_preflight.get("afterMinimumFactor"),
            "targetFactor": wall_embedment_preflight.get("designTarget"),
            "message": wall_embedment_preflight.get("message"),
        },
    }

    return {
        "status": status,
        "caseId": case.id,
        "topologyPreflight": topology_preflight,
        "supportTopologySynchronization": support_case_sync,
        "wallEmbedmentPreflight": wall_embedment_preflight,
        "rootCauses": roots,
        "wallCoverage": sorted(envelopes, key=lambda row: (-_status_rank(str(row.get("supportCoverageStatus"))), -float(row.get("maxDisplacementMm") or 0.0))),
        "strengthDesignLoop": strength_design_loop,
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
