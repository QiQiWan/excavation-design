from __future__ import annotations

import math
from typing import Any

from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

from app.schemas.domain import Project
from app.services.deepening_readiness import group_deepening_checks
from app.services.detailing_geometry import apply_embedded_item_patches
from app.version import SOFTWARE_VERSION

STEEL_DENSITY_KG_M3 = 7850.0
GRAVITY = 9.80665


def _round_up(value: float, step: float) -> float:
    return math.ceil(max(value, 0.0) / step - 1e-12) * step


def _support_lookup(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    return {item.id: item for item in (ret.supports if ret else [])}


def _node_hardware(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    if not ret:
        return {"bearingPlates": [], "stiffeners": [], "welds": [], "anchorBars": [], "embeddedItems": [], "checks": []}
    supports = _support_lookup(project)
    plates: list[dict[str, Any]] = []
    stiffeners: list[dict[str, Any]] = []
    welds: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    embedded: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for node in ret.support_nodes or []:
        support = supports.get(node.support_id)
        force = abs(float(getattr(support, "design_axial_force", None) or getattr(support, "effective_axial_force_standard", None) or 0.0))
        section_w = float(getattr(getattr(support, "section", None), "width", None) or getattr(getattr(support, "section", None), "diameter", None) or 0.8)
        section_h = float(getattr(getattr(support, "section", None), "height", None) or getattr(getattr(support, "section", None), "diameter", None) or section_w)
        allowable_bearing = 14000.0  # kN/m2, detailing screening value
        target_util = 0.85
        required_area = force / max(allowable_bearing * target_util, 1.0)
        existing = node.bearing_plate
        width = max(float(existing.plate_width) if existing else 0.0, section_w + 0.20, math.sqrt(required_area * 1.15))
        height = max(float(existing.plate_height) if existing else 0.0, section_h + 0.20, required_area / max(width, 0.1))
        width = _round_up(width, 0.05)
        height = _round_up(height, 0.05)
        projection = max((width - section_w) / 2.0, (height - section_h) / 2.0, 0.05)
        pressure = force / max(width * height, 1e-9)
        thickness = _round_up(max(0.04, projection * math.sqrt(max(3.0 * pressure / 215000.0, 0.0))), 0.005)
        thickness = min(max(thickness, 0.04), 0.10)
        plate_util = pressure / allowable_bearing
        plate_status = "pass" if plate_util <= 0.90 else "warning" if plate_util <= 1.0 else "fail"
        plate_id = f"EP-{node.code}"
        plate = {
            "itemId": plate_id,
            "nodeId": node.id,
            "nodeCode": node.code,
            "supportCode": node.support_code,
            "levelIndex": node.level_index,
            "elevationM": node.elevation,
            "xM": node.location.x,
            "yM": node.location.y,
            "widthMm": round(width * 1000.0),
            "heightMm": round(height * 1000.0),
            "thicknessMm": round(thickness * 1000.0),
            "material": "Q355B",
            "designForceKn": round(force, 3),
            "bearingStressMpa": round(pressure / 1000.0, 3),
            "bearingUtilization": round(plate_util, 3),
            "status": plate_status,
            "drawingRef": "D-10",
        }
        plates.append(plate)
        stiffener_count = 2 if force <= 5000 else 4 if force <= 12000 else 6
        stiffener_t = _round_up(max(0.012, thickness * 0.45), 0.002)
        stiffener_h = max(section_h * 0.65, 0.35)
        stiffeners.append({
            "itemId": f"STF-{node.code}", "nodeId": node.id, "nodeCode": node.code,
            "count": stiffener_count, "thicknessMm": round(stiffener_t * 1000.0),
            "heightMm": round(stiffener_h * 1000.0), "lengthMm": round(section_w * 800.0),
            "material": "Q355B", "orientation": "symmetric_both_sides",
            "status": "pass" if stiffener_t <= 0.025 else "warning", "drawingRef": "D-10",
        })
        weld_length_mm = max(2.0 * (width + height) * 1000.0, 800.0)
        weld_allowable_n_mm2 = 160.0
        section_type = str(getattr(support, "section_type", None) or "rc_rectangular")
        if section_type == "rc_rectangular":
            # Reinforced-concrete struts transfer the principal compression by
            # concrete bearing and anchored reinforcement.  The perimeter weld
            # is an embedded-plate assembly weld and is therefore checked for a
            # conservative secondary force component rather than the full strut
            # axial force.
            weld_design_force = force * 0.12
            weld_type = "embedded_plate_assembly_fillet"
            weld_size = _round_up(weld_design_force * 1000.0 / max(0.7 * weld_allowable_n_mm2 * weld_length_mm, 1.0), 1.0)
            weld_size = min(max(weld_size, 8.0), 16.0)
            weld_capacity = 0.7 * weld_allowable_n_mm2 * weld_size * weld_length_mm
            weld_util = weld_design_force * 1000.0 / max(weld_capacity, 1.0)
            load_path = "RC支撑轴力经混凝土承压和纵筋锚固传递；围焊承担预埋板组装及次生作用。"
            inspection = "100%外观；关键节点按焊接工艺评定要求抽检MT"
        else:
            # Steel struts transfer the full axial force through the end-plate
            # weld group. Search a fillet-weld solution first, then upgrade to a
            # complete-joint-penetration groove weld when the required fillet
            # size exceeds the detailing limit.
            weld_design_force = force
            required_fillet = _round_up(weld_design_force * 1000.0 / max(0.7 * weld_allowable_n_mm2 * weld_length_mm, 1.0), 1.0)
            if required_fillet <= 20.0:
                weld_type = "continuous_fillet"
                weld_size = max(required_fillet, 8.0)
                weld_capacity = 0.7 * weld_allowable_n_mm2 * weld_size * weld_length_mm
                load_path = "钢支撑轴力由端板连续角焊缝传递。"
                inspection = "100%外观 + 关键节点20% MT"
            else:
                weld_type = "complete_joint_penetration_groove"
                groove_allowable = 215.0
                weld_size = _round_up(weld_design_force * 1000.0 / max(groove_allowable * weld_length_mm, 1.0), 2.0)
                weld_size = min(max(weld_size, 12.0), max(thickness * 1000.0, 20.0))
                weld_capacity = groove_allowable * weld_size * weld_length_mm
                load_path = "钢支撑轴力由全熔透坡口焊缝传递，并配置对称加劲板。"
                inspection = "100%外观 + 100% UT/RT，焊接工艺评定后实施"
            weld_util = weld_design_force * 1000.0 / max(weld_capacity, 1.0)
        welds.append({
            "weldId": f"WELD-{node.code}", "nodeId": node.id, "nodeCode": node.code,
            "supportSectionType": section_type, "weldType": weld_type, "weldSizeMm": weld_size,
            "effectiveLengthMm": round(weld_length_mm), "qualityGrade": "II",
            "electrode": "E50", "designForceKn": round(weld_design_force, 3),
            "utilization": round(weld_util, 3), "loadPath": load_path,
            "inspection": inspection,
            "status": "pass" if weld_util <= 0.90 else "warning" if weld_util <= 1.0 else "fail",
            "drawingRef": "D-10",
        })
        anchor_d = 25 if force <= 6000 else 28 if force <= 12000 else 32
        anchor_count = max(4, int(_round_up(force / max(0.9 * 0.25 * anchor_d * anchor_d, 1.0), 2)))
        anchor_count = min(anchor_count, 16)
        anchor_capacity = anchor_count * 0.25 * anchor_d * anchor_d * 0.9
        anchors.append({
            "itemId": f"ANCH-{node.code}", "nodeId": node.id, "nodeCode": node.code,
            "diameterMm": anchor_d, "count": anchor_count, "grade": "HRB500",
            "embedmentLengthMm": max(20 * anchor_d, 500), "layout": "symmetric perimeter",
            "demandKn": round(force * 0.08, 3), "screeningCapacityKn": round(anchor_capacity, 3),
            "status": "pass" if anchor_capacity >= force * 0.08 else "warning", "drawingRef": "D-10",
        })
        embedded.append({
            "itemId": plate_id, "itemType": "bearing_plate", "hostId": node.id, "hostCode": node.code, "supportCode": node.support_code,
            "center": {"x": node.location.x, "y": node.location.y, "z": node.elevation},
            "size": {"x": width, "y": thickness, "z": height},
            "clearanceM": 0.05, "drawingRef": "D-10",
        })
        checks.append({
            "checkId": f"NODE-HW-{node.code}", "category": "node_hardware",
            "hostId": node.id, "hostCode": node.code, "nodeCode": node.code,
            "plateStatus": plate_status, "weldStatus": welds[-1]["status"], "anchorStatus": anchors[-1]["status"],
            "status": "fail" if "fail" in {plate_status, welds[-1]["status"], anchors[-1]["status"]} else "warning" if "warning" in {plate_status, welds[-1]["status"], anchors[-1]["status"]} else "pass",
            "failureReasonCode": "NODE_HARDWARE_CAPACITY" if "fail" in {plate_status, welds[-1]["status"], anchors[-1]["status"]} else "NODE_HARDWARE_REVIEW" if "warning" in {plate_status, welds[-1]["status"], anchors[-1]["status"]} else None,
            "message": "节点承压板、加劲板、焊缝与锚筋已形成可出图参数。",
            "recommendedAction": "调整节点承压板、加劲板、焊缝或锚筋并重新复核。" if ({plate_status, welds[-1]["status"], anchors[-1]["status"]} & {"fail", "warning"}) else "按节点深化图复核构造与检验要求。",
        })
    return {"bearingPlates": plates, "stiffeners": stiffeners, "welds": welds, "anchorBars": anchors, "embeddedItems": embedded, "checks": checks}


def _cage_hoisting(cage_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in cage_segments:
        weight_t = float(item.get("estimatedCageWeightT") or 0.0)
        length_m = float(item.get("lengthM") or 0.0)
        point_count = int(item.get("liftingPointCount") or 4)
        rigging_angle = 60.0 if point_count <= 4 else 55.0
        total_force_kn = weight_t * GRAVITY * 1.35
        line_tension_kn = total_force_kn / max(point_count * math.sin(math.radians(rigging_angle)), 1e-6)
        lifting_bar_d = 25 if line_tension_kn <= 100 else 28 if line_tension_kn <= 150 else 32 if line_tension_kn <= 220 else 36
        bar_capacity_kn = math.pi * lifting_bar_d ** 2 / 4.0 * 435.0 / 1000.0 * 0.80
        line_util = line_tension_kn / max(bar_capacity_kn, 1e-6)
        slenderness = length_m / max(1.2, math.sqrt(max(point_count, 1)))
        deformation_mm = 0.45 * slenderness ** 2 * max(weight_t, 0.1)
        status = "fail" if weight_t > 35.0 or line_util > 1.0 else "warning" if weight_t > 25.0 or line_util > 0.85 or deformation_mm > 80 else "pass"
        ratios = [round((i + 1) / (point_count + 1), 3) for i in range(point_count)]
        results.append({
            "analysisId": f"HOIST-{item.get('segmentId')}", "segmentId": item.get("segmentId"),
            "category": "cage_hoisting",
            "hostCode": item.get("hostCode"), "lengthM": length_m, "weightT": weight_t,
            "dynamicFactor": 1.35, "liftingPointCount": point_count, "liftingPointRatios": ratios,
            "riggingAngleDeg": rigging_angle, "lineTensionKn": round(line_tension_kn, 3),
            "liftingBarDiameterMm": lifting_bar_d, "liftingBarCapacityKn": round(bar_capacity_kn, 3),
            "liftingBarUtilization": round(line_util, 3), "estimatedElasticDeformationMm": round(deformation_mm, 1),
            "transportEnvelope": {"maxLengthM": 12.0, "maxWidthM": 3.0, "maxWeightT": 35.0},
            "status": status,
            "failureReasonCode": "CAGE_HOISTING_CAPACITY" if status == "fail" else "CAGE_HOISTING_REVIEW" if status == "warning" else None,
            "recommendedAction": "专项吊装验算并设置加强桁架" if status == "fail" else "复核吊机工况、吊点焊缝和临时加强" if status == "warning" else "按吊装图和吊装方案实施",
            "drawingRef": "R-10",
        })
    return results


def _coupler_schedule(fabrication: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for splice in fabrication.get("spliceRecords", []):
        if splice.get("spliceType") != "mechanical_coupler":
            continue
        diameter = float(splice.get("diameterMm") or 0.0)
        rows.append({
            "couplerId": splice.get("spliceId"), "sourceBarId": splice.get("sourceBarId"),
            "barMark": splice.get("barMark"), "hostCode": splice.get("hostCode"),
            "diameterMm": diameter, "specification": splice.get("couplerSpec") or f"直螺纹套筒 D{diameter:g}",
            "threadClass": "正反丝按安装方向配置", "inspectionLot": "500个/批或按项目要求",
            "samplingRequirement": "工艺检验 + 现场见证抽检", "staggerGroup": splice.get("staggerGroup"),
            "status": "pass", "drawingRef": "R-11",
        })
    return rows


def _embedded_collision_checks(embedded_items: list[dict[str, Any]], bars: list[dict[str, Any]], limit: int = 5000) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str, str, str]] = set()
    records: list[tuple[dict[str, Any], LineString, float, float]] = []
    geometries: list[LineString] = []
    for bar in bars:
        points = bar.get("points") or []
        if len(points) < 2:
            continue
        line = LineString([(float(p.get("x", 0.0)), float(p.get("y", 0.0))) for p in points])
        z_values = [float(p.get("z", 0.0)) for p in points]
        geometries.append(line)
        records.append((bar, line, min(z_values), max(z_values)))
    if not geometries:
        return checks
    tree = STRtree(geometries)
    for item in embedded_items:
        center = item["center"]
        size = item["size"]
        clearance = float(item.get("clearanceM") or 0.0)
        envelope = box(
            center["x"] - size["x"] / 2.0 - clearance,
            center["y"] - size["y"] / 2.0 - clearance,
            center["x"] + size["x"] / 2.0 + clearance,
            center["y"] + size["y"] / 2.0 + clearance,
        )
        zmin = center["z"] - size["z"] / 2.0 - clearance
        zmax = center["z"] + size["z"] / 2.0 + clearance
        for index in tree.query(envelope):
            bar, line, bar_zmin, bar_zmax = records[int(index)]
            if bar_zmax < zmin or bar_zmin > zmax or not line.intersects(envelope):
                continue
            group_key = (
                str(item.get("itemId") or ""),
                str(bar.get("hostCode") or ""),
                str(bar.get("groupId") or ""),
                str(bar.get("barType") or ""),
            )
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            host_type = str(bar.get("hostType") or "")
            intended = (
                str(bar.get("hostId") or "") == str(item.get("hostId") or "")
                or host_type == "support_wale_node"
                or str(bar.get("hostCode") or "") == str(item.get("supportCode") or "")
            )
            coordinated_host = host_type in {"diaphragm_wall", "beam", "internal_support"}
            opening_pass = False
            opening_id = None
            for opening in item.get("openings") or []:
                opening_center = opening.get("center") or center
                radius = float(opening.get("diameterM") or 0.0) / 2.0
                if radius <= 0.0:
                    continue
                bar_radius = max(0.0, float(bar.get("diameterMm") or 0.0) / 2000.0)
                if line.distance(Point(float(opening_center.get("x", center["x"])), float(opening_center.get("y", center["y"])))) <= max(0.0, radius - bar_radius):
                    opening_pass = True
                    opening_id = opening.get("openingId")
                    break
            status = "pass" if intended or opening_pass else "warning" if coordinated_host else "fail"
            core = box(
                center["x"] - size["x"] / 2.0,
                center["y"] - size["y"] / 2.0,
                center["x"] + size["x"] / 2.0,
                center["y"] + size["y"] / 2.0,
            )
            diameter_m = max(0.0, float(bar.get("diameterMm") or 0.0) / 1000.0)
            horizontal_gap = max(0.0, float(line.distance(core)) - diameter_m / 2.0)
            z_gap = max(0.0, max(zmin - bar_zmax, bar_zmin - zmax))
            actual_clearance = max(horizontal_gap, z_gap) if not line.intersects(core) else 0.0
            penetration = max(0.0, clearance - actual_clearance)
            checks.append({
                "checkId": f"EMB-COL-{item.get('itemId')}-{bar.get('barId')}",
                "category": "embedded_collision",
                "embeddedItemId": item.get("itemId"), "embeddedType": item.get("itemType"),
                "barId": bar.get("barId"), "barMark": bar.get("barMark"), "hostCode": bar.get("hostCode"),
                "barGroupId": bar.get("groupId"), "barType": bar.get("barType"),
                "barDiameterMm": round(diameter_m * 1000.0, 1),
                "requiredClearanceM": round(clearance, 4), "actualClearanceM": round(actual_clearance, 4),
                "estimatedPenetrationM": round(penetration, 4), "intersectsEmbeddedSolid": bool(line.intersects(core)),
                "status": status, "failureReasonCode": "EMBEDDED_ITEM_COLLISION" if status == "fail" else "EMBEDDED_ITEM_CLEARANCE_REVIEW" if status == "warning" else None,
                "intendedConnection": intended, "passesThroughDesignedOpening": opening_pass, "openingId": opening_id,
                "message": "钢筋按已配置预埋件开孔穿越，孔边加劲和净截面需按D-10复核" if opening_pass else "节点锚固钢筋与预埋件为设计连接" if intended else "节点区常规钢筋进入预埋件净空，需按局部绕筋/截断锚固大样协调" if coordinated_host else "非关联钢筋穿越预埋件实体或施工净空",
                "recommendedAction": "按开孔和孔边加劲大样施工" if opening_pass else "按节点大样绑扎" if intended else "在D-10中明确局部绕筋、截断锚固或预埋件开孔" if coordinated_host else "移动钢筋、调整预埋件或增加专项节点大样",
                "drawingRef": "Q-04",
            })
            if len(checks) >= limit:
                return checks
    return checks


def _construction_sequence(project: Project) -> list[dict[str, Any]]:
    rows = [
        (10, "survey", "测量放线与地下障碍复核", "S-00/M-01"),
        (20, "wall", "导墙、成槽、钢筋笼吊装和水下混凝土", "R-01/R-10/D-06"),
        (30, "crown", "冠梁及首道围檩施工", "R-05"),
    ]
    cases = project.calculation_cases[-1].stages if project.calculation_cases else []
    order = 40
    for stage in cases:
        rows.append((order, "stage", f"{stage.name}：开挖至 {stage.excavation_elevation:.3f} m，按工况激活支撑/换撑", "S-02/S-03"))
        order += 10
    rows.extend([
        (order, "inspection", "节点承压板、焊缝、预加轴力和支撑轴线复测", "D-10/Q-04"),
        (order + 10, "replacement", "地下室楼板形成后按换撑刚度和监测条件分区拆撑", "S-03"),
        (order + 20, "closeout", "完成监测复核、变更归档和竣工模型交付", "M-02/G-00"),
    ])
    return [{"sequence": seq, "phase": phase, "activity": activity, "drawingRefs": refs, "holdPoint": phase in {"wall", "inspection", "replacement"}} for seq, phase, activity, refs in rows]


def build_deep_detailing_package(
    project: Project,
    *,
    bars: list[dict[str, Any]],
    cage_segments: list[dict[str, Any]],
    fabrication: dict[str, Any],
) -> dict[str, Any]:
    hardware = _node_hardware(project)
    embedded_writeback = apply_embedded_item_patches(project, hardware["embeddedItems"])
    hardware["embeddedItems"] = embedded_writeback["embeddedItems"]
    hoisting = _cage_hoisting(cage_segments)
    couplers = _coupler_schedule(fabrication)
    embedded_checks = _embedded_collision_checks(hardware["embeddedItems"], bars)
    overrides = (project.advanced_engineering or {}).get("detailingOverrides", {})
    actual_patches = (project.advanced_engineering or {}).get("detailGeometryPatches", {})
    if isinstance(overrides, dict) and not actual_patches:
        applied_by_check = {}
        for override in overrides.values():
            if not isinstance(override, dict):
                continue
            for check_id in override.get("sourceCheckIds", []):
                applied_by_check[str(check_id)] = override
        for check in embedded_checks:
            override = applied_by_check.get(str(check.get("checkId")))
            if override:
                check["originalStatus"] = check.get("status")
                required = float(check.get("requiredClearanceM") or 0.05)
                original = float(check.get("actualClearanceM") or 0.0)
                gain = float(override.get("predictedClearanceGainM") or 0.0)
                predicted = max(0.0, original + gain)
                verification = override.get("verification") if isinstance(override.get("verification"), dict) else {}
                verification_ok = all(bool(v) for v in verification.values()) if verification else False
                residual = max(0.0, required - predicted)
                check["coordinationAction"] = override.get("action")
                check["coordinationCandidateId"] = override.get("candidateId")
                check["predictedClearanceM"] = round(predicted, 4)
                check["residualClearanceDeficitM"] = round(residual, 4)
                check["coordinationVerification"] = verification
                check["coordinationGeometryDelta"] = override.get("geometryDelta")
                if predicted >= required and verification_ok:
                    check["status"] = "pass"
                elif check.get("originalStatus") == "fail" and predicted < required * 0.5:
                    check["status"] = "fail"
                else:
                    check["status"] = "warning"
                check["message"] = (
                    f"已应用构造协调方案：{override.get('title') or override.get('action')}；"
                    f"预测净距 {predicted:.3f} m / 要求 {required:.3f} m，复核状态为 {check['status']}。"
                )
    sequence = _construction_sequence(project)
    hard_fail = (
        sum(x.get("status") == "fail" for x in hardware["checks"])
        + sum(x.get("status") == "fail" for x in hoisting)
        + sum(x.get("status") == "fail" for x in embedded_checks)
    )
    warning_count = (
        sum(x.get("status") == "warning" for x in hardware["checks"])
        + sum(x.get("status") == "warning" for x in hoisting)
        + sum(x.get("status") == "warning" for x in embedded_checks)
    )
    status = "fail" if hard_fail else "warning" if warning_count else "pass"
    diagnostic_checks = [*hardware["checks"], *hoisting, *embedded_checks]
    blocking_groups = group_deepening_checks(
        diagnostic_checks, statuses={"fail"}, source="spatial_detailing",
    )
    warning_groups = group_deepening_checks(
        diagnostic_checks, statuses={"warning", "manual_review"}, source="spatial_detailing",
    )
    resolution_guide = [
        {
            "priority": index + 1,
            "reasonCode": row.get("reasonCode"),
            "title": row.get("title"),
            "affectedCount": row.get("count"),
            "objects": row.get("objects"),
            "action": row.get("requiredAction"),
            "targetStage": row.get("targetStage"),
        }
        for index, row in enumerate([*blocking_groups, *warning_groups][:12])
    ]
    return {
        "version": SOFTWARE_VERSION,
        "status": status,
        "nodeHardware": hardware,
        "cageHoisting": hoisting,
        "couplerSchedule": couplers,
        "embeddedItemCollisionChecks": embedded_checks,
        "constructionSequence": sequence,
        "blockingGroups": blocking_groups,
        "warningGroups": warning_groups,
        "resolutionGuide": resolution_guide,
        "geometryWriteback": {
            "embeddedItems": embedded_writeback["summary"],
            "barPatchCount": int((project.advanced_engineering or {}).get("detailGeometryPatchCount") or len((project.advanced_engineering or {}).get("detailGeometryPatches") or {})),
        },
        "summary": {
            "nodeCount": len(hardware["checks"]),
            "bearingPlateCount": len(hardware["bearingPlates"]),
            "stiffenerSetCount": len(hardware["stiffeners"]),
            "weldCount": len(hardware["welds"]),
            "anchorBarSetCount": len(hardware["anchorBars"]),
            "cageHoistingCaseCount": len(hoisting),
            "couplerCount": len(couplers),
            "embeddedCollisionCheckCount": len(embedded_checks),
            "hardFailureCount": hard_fail,
            "warningCount": warning_count,
            "blockingGroupCount": len(blocking_groups),
            "warningGroupCount": len(warning_groups),
            "status": status,
            "geometryPatchCount": len((project.advanced_engineering or {}).get("detailGeometryPatches") or {}),
            "modifiedEmbeddedItemCount": embedded_writeback["summary"].get("modifiedEmbeddedItemCount", 0),
        },
        "qualityBoundary": "节点承压板、加劲板、焊缝、锚筋、钢筋笼吊装和预埋件碰撞采用工程深化筛查模型；高利用率、重型吊装和复杂节点仍需项目专项有限元、焊接工艺评定及吊装专项方案复核。",
    }
