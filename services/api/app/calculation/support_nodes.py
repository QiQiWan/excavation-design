from __future__ import annotations

from app.schemas.domain import ReinforcementGroup, SupportElement, SupportWaleNode

CONCRETE_BEARING_CAPACITY_KPA = {
    "C30": 0.60 * 14300,
    "C35": 0.60 * 16700,
    "C40": 0.60 * 19100,
    "C45": 0.60 * 21100,
    "C50": 0.60 * 23100,
}


def _node_reinforcement_groups(axial_force: float) -> list[ReinforcementGroup]:
    if axial_force >= 3000:
        return [
            ReinforcementGroup(name="节点核心区附加竖向筋", bar_type="additional", diameter=25, count=8, grade="HRB400", location_description="high-force support-to-wale node vertical bars", check_status="preliminary"),
            ReinforcementGroup(name="节点核心区加密箍筋", bar_type="stirrup", diameter=14, spacing=100, grade="HRB400", location_description="dense confinement stirrups in node core", check_status="preliminary"),
            ReinforcementGroup(name="端部抗裂分布筋", bar_type="distribution", diameter=16, spacing=150, grade="HRB400", location_description="anti-splitting distribution bars near bearing plate", check_status="preliminary"),
        ]
    return [
        ReinforcementGroup(name="节点附加竖向筋", bar_type="additional", diameter=20, count=4, grade="HRB400", location_description="support-to-wale node vertical bars", check_status="preliminary"),
        ReinforcementGroup(name="节点加密箍筋", bar_type="stirrup", diameter=12, spacing=100, grade="HRB400", location_description="dense stirrups in node core", check_status="preliminary"),
    ]


def update_support_node_design(nodes: list[SupportWaleNode], supports: list[SupportElement]) -> list[dict]:
    supports_by_id = {s.id: s for s in supports}
    checks: list[dict] = []
    for node in nodes:
        support = supports_by_id.get(node.support_id)
        if not support or not node.bearing_plate:
            node.check_status = "manual_review"
            checks.append({
                "ruleId": "GB50010-NODE-BEARING-SUBSET",
                "objectId": node.id,
                "objectType": "SupportWaleNode",
                "status": "manual_review",
                "message": "缺少支撑轴力或承压板参数，支撑-围檩节点需人工复核。",
                "clauseReference": "GB/T 50010 local compression and detailing subset; final clause applicability to verify",
            })
            continue
        axial = float(support.design_axial_force or 0.0)
        capacity = CONCRETE_BEARING_CAPACITY_KPA.get(support.material.grade, CONCRETE_BEARING_CAPACITY_KPA["C35"])
        # Automatically enlarge the local bearing plate within the support section envelope.
        max_plate_w = max(node.bearing_plate.plate_width, float(support.section.width or node.bearing_plate.plate_width))
        max_plate_h = max(node.bearing_plate.plate_height, float(support.section.height or node.bearing_plate.plate_height))
        required_area = axial / max(capacity, 1e-9) if axial > 0 else node.bearing_plate.bearing_area
        side = max(node.bearing_plate.plate_width, node.bearing_plate.plate_height, required_area ** 0.5)
        side = min(max(side, 0.6), max(max_plate_w, max_plate_h))
        node.bearing_plate.plate_width = round(side, 3)
        node.bearing_plate.plate_height = round(side, 3)
        node.bearing_plate.bearing_area = round(side * side, 3)
        area = max(float(node.bearing_plate.bearing_area), 0.01)
        stress = axial / area
        status = "pass" if stress <= capacity else "fail"
        node.bearing_plate.bearing_stress = round(stress, 3)
        node.bearing_plate.bearing_capacity = round(capacity, 3)
        node.bearing_plate.check_status = status
        node.reinforcement = _node_reinforcement_groups(axial)
        node.check_status = status
        node.design_note = "按支撑轴力包络进行端部局部承压和节点附加配筋子集筛查；正式节点详图需复核锚固、抗裂、局压扩散和施工连接。"
        checks.append({
            "ruleId": "GB50010-NODE-BEARING-SUBSET",
            "objectId": node.id,
            "objectType": "SupportWaleNode",
            "status": status,
            "calculatedValue": round(stress, 3),
            "limitValue": round(capacity, 3),
            "unit": "kPa",
            "message": "支撑端部-围檩节点局部承压子集筛查，并生成节点附加筋/加密箍筋建议。",
            "clauseReference": "GB/T 50010 local compression/detailing subset; final clause applicability to verify",
            "formula": "auto-enlarge bearing plate; sigma_bearing = N_design / A_plate <= 0.60*f_c",
            "supportCode": support.code,
            "nodeCode": node.code,
            "bearingArea": area,
            "bearingPlateWidth": node.bearing_plate.plate_width,
            "bearingPlateHeight": node.bearing_plate.plate_height,
            "reinforcementSummary": "; ".join(f"{r.name} D{r.diameter}" + (f"x{r.count}" if r.count else f"@{r.spacing}" if r.spacing else "") for r in node.reinforcement),
        })
    return checks
