from __future__ import annotations

from typing import Any

from app.rules.gb50010.detailing_rules import check_rebar_anchorage_and_lap
from app.schemas.domain import Project


def _status_rank(value: str) -> int:
    return {"pass": 0, "preliminary": 1, "manual_review": 2, "warning": 3, "fail": 4}.get(value, 2)


def build_rebar_constructability(project: Project, scheme: dict[str, Any]) -> dict[str, Any]:
    settings = project.design_settings
    checks: list[dict[str, Any]] = []
    coupler_threshold = float(settings.rebar_mechanical_coupler_diameter_mm)
    congestion_limit = float(settings.rebar_congestion_limit)
    cover = float(settings.default_cover_mm)

    wall_by_id = {wall.id: wall for wall in (project.retaining_system.diaphragm_walls if project.retaining_system else [])}
    for zone in scheme.get("wallZones", []):
        wall = wall_by_id.get(str(zone.get("hostId")))
        thickness_mm = float(getattr(wall, "thickness", 0.8) or 0.8) * 1000.0
        zone_height_mm = max(float(zone.get("heightM") or 0.0) * 1000.0, 1.0)
        available_depth = max(thickness_mm - 2.0 * cover, 1.0)
        for face in zone.get("faces", []):
            dia = float(face.get("barDiameterMm") or 0.0)
            spacing = float(face.get("barSpacingMm") or 200.0)
            layers = int(face.get("layerCount") or 1)
            clear = float(face.get("clearSpacingMm") or max(spacing - dia, 0.0))
            occupancy = min(9.0, layers * dia / available_depth + dia / max(clear + dia, 1.0))
            congestion_status = "fail" if occupancy > congestion_limit else "warning" if occupancy > 0.85 * congestion_limit else "pass"
            checks.append({
                "checkId": f"RB-CONGEST-{zone.get('zoneId')}-{face.get('face')}",
                "category": "rebar_congestion",
                "hostId": zone.get("hostId"),
                "hostCode": zone.get("hostCode"),
                "zoneId": zone.get("zoneId"),
                "face": face.get("face"),
                "status": congestion_status,
                "calculatedValue": round(occupancy, 4),
                "limitValue": round(congestion_limit, 4),
                "unit": "index",
                "failureReasonCode": "REBAR_CONGESTION" if congestion_status == "fail" else None,
                "message": f"钢筋占用/净距筛查指数 {occupancy:.3f}，控制值 {congestion_limit:.3f}。",
                "recommendedAction": "调整钢筋层数、直径或截面厚度，并复核钢筋笼吊装净距。" if congestion_status != "pass" else None,
                "evidenceLevel": "constructability_screening",
            })
            coupler_required = dia >= coupler_threshold or layers > 1 or bool(face.get("mechanicalCouplerRequired"))
            checks.append({
                "checkId": f"RB-COUPLER-{zone.get('zoneId')}-{face.get('face')}",
                "category": "mechanical_coupler",
                "hostId": zone.get("hostId"),
                "hostCode": zone.get("hostCode"),
                "status": "manual_review" if coupler_required else "pass",
                "calculatedValue": dia,
                "limitValue": coupler_threshold,
                "unit": "mm",
                "message": "需要机械连接并核定接头等级、错开比例和检验批。" if coupler_required else "钢筋直径未触发项目机械连接阈值。",
                "recommendedAction": "在施工图和钢筋表中明确套筒类型、等级、错开位置和抽检要求。" if coupler_required else None,
                "evidenceLevel": "detailing_requirement",
            })
            anchor_checks = check_rebar_anchorage_and_lap(
                object_id=str(zone.get("hostId") or "wall"),
                bar_diameter_mm=max(dia, 12.0),
                rebar_grade=str(getattr(wall, "rebar_grade", settings.default_rebar_grade) if wall else settings.default_rebar_grade),
                available_anchor_length_mm=max(min(zone_height_mm * 0.45, 1800.0), 300.0),
                available_lap_length_mm=max(min(zone_height_mm * 0.35, 2200.0), 400.0),
                seismic=settings.seismic_grade not in {"non_seismic_temporary", "none"},
            )
            for raw in anchor_checks:
                item = raw.model_dump(mode="json", by_alias=True)
                item.update({
                    "hostCode": zone.get("hostCode"),
                    "zoneId": zone.get("zoneId"),
                    "category": "anchorage" if str(item.get("ruleId") or "").endswith("-ANCHOR") else "lap_splice",
                    "evidenceLevel": "parameterized_screening",
                })
                checks.append(item)

    support_by_id = {support.id: support for support in (project.retaining_system.supports if project.retaining_system else [])}
    for row in scheme.get("supportSchemes", []):
        support = support_by_id.get(str(row.get("hostId")))
        if not support or row.get("sectionType") not in (None, "rc_rectangular"):
            continue
        longitudinal = row.get("longitudinal") or {}
        dia = float(longitudinal.get("diameterMm") or 0.0)
        count = int(longitudinal.get("count") or 0)
        section = row.get("section") or {}
        width_mm = float(section.get("widthM") or 0.8) * 1000.0
        height_mm = float(section.get("heightM") or 0.8) * 1000.0
        available_perimeter = max(2.0 * (width_mm + height_mm - 4.0 * cover), 1.0)
        occupancy = count * dia / available_perimeter
        congestion_status = "fail" if occupancy > congestion_limit else "warning" if occupancy > 0.85 * congestion_limit else "pass"
        checks.append({
            "checkId": f"RB-SUPPORT-CONGEST-{support.id}",
            "category": "support_rebar_congestion",
            "hostId": support.id,
            "hostCode": support.code,
            "status": congestion_status,
            "calculatedValue": round(occupancy, 4),
            "limitValue": round(congestion_limit, 4),
            "unit": "index",
            "message": f"支撑纵筋周边占用指数 {occupancy:.3f}。",
            "recommendedAction": "增大截面或调整纵筋直径/根数，并复核箍筋和节点加密区。" if congestion_status != "pass" else None,
            "evidenceLevel": "constructability_screening",
        })
        coupler_required = dia >= coupler_threshold or count >= 16
        checks.append({
            "checkId": f"RB-SUPPORT-COUPLER-{support.id}",
            "category": "support_mechanical_coupler",
            "hostId": support.id,
            "hostCode": support.code,
            "status": "manual_review" if coupler_required else "pass",
            "calculatedValue": dia,
            "limitValue": coupler_threshold,
            "unit": "mm",
            "message": "水平支撑纵筋建议采用机械连接并避开节点刚域。" if coupler_required else "可采用项目批准的搭接或机械连接方案。",
            "recommendedAction": "在跨中低应力区错开接头，节点刚域内不得集中设置接头。" if coupler_required else None,
            "evidenceLevel": "detailing_requirement",
        })

    fail = sum(1 for row in checks if row.get("status") == "fail")
    warning = sum(1 for row in checks if row.get("status") in {"warning", "manual_review"})
    governing = max(checks, key=lambda row: _status_rank(str(row.get("status") or "manual_review")), default=None)
    return {
        "method": "V3.46 anchorage, lap, coupler and reinforcement-congestion constructability screening",
        "checks": checks,
        "summary": {
            "checkCount": len(checks),
            "failCount": fail,
            "warningCount": warning,
            "passCount": sum(1 for row in checks if row.get("status") == "pass"),
            "governingStatus": governing.get("status") if governing else "manual_review",
            "governingObject": governing.get("hostCode") if governing else None,
        },
        "boundary": "筛查不能替代钢筋排布详图、套筒产品认证、吊装验算和现场样板确认。",
    }
