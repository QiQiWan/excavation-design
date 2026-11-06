"""Explicit, shared physical input for two independent structural solvers.

Only the mesh, external water actions and constitutive parameters are shared.
No structural matrix, iteration state or solution is shared with a comparator.
"""
from __future__ import annotations

from bisect import bisect_right
from math import ceil, pi, radians, sqrt, tan

from app.section.models import FrozenSectionSnapshot, SectionInput
from app.section.geotech_actions import (vertical_stress, strip_vertical_increment,
                                         rectangle_vertical_increment, rankine_pressure_state)


def effective_vertical_stress(model: SectionInput, depth_m: float, surface_m: float, water_m: float) -> float:
    """Integral of total unit weight minus hydrostatic gradient below water.

    H and z are measured downwards from the same model ground datum. The
    inside integral starts at the actual excavation surface; surcharge is
    intentionally excluded and handled explicitly by the caller.
    """
    stress = 0.0
    for layer in model.soil_layers:
        lo = max(surface_m, layer.top_m)
        hi = min(depth_m, layer.bottom_m)
        if hi <= lo:
            continue
        dry = max(0.0, min(hi, water_m) - lo)
        wet = max(0.0, hi - max(lo, water_m))
        stress += dry * layer.gamma_unsat_kn_m3 + wet * (layer.gamma_sat_kn_m3 - model.analysis.water_unit_weight_kn_m3)
    return stress


def build_nodes(model: SectionInput) -> list[float]:
    length = model.pile.length_m
    critical = {0.0, length}
    for layer in model.soil_layers:
        critical.update((layer.top_m, layer.bottom_m))
    for stage in model.stages:
        critical.update((stage.excavation_depth_m, stage.water_inside_depth_m, stage.water_outside_depth_m))
    critical.update(support.depth_m for support in model.supports)
    bounds = sorted({round(z, 10) for z in critical if 0 <= z <= length})
    nodes = [0.0]
    for lo, hi in zip(bounds, bounds[1:]):
        pieces = max(1, ceil((hi - lo) / model.analysis.max_element_size_m - 1e-10))
        nodes.extend(round(lo + (hi - lo) * j / pieces, 10) for j in range(1, pieces + 1))
    if len(nodes) > 401 or any(b - a < 1e-5 for a, b in zip(nodes, nodes[1:])):
        raise ValueError('网格超过401节点或包含间距小于0.01毫米的相邻关键深度；请检查输入精度和网格')
    return nodes


def build_solver_model(snapshot: FrozenSectionSnapshot) -> dict:
    model = snapshot.validated_input()
    extensions = model.method_extensions
    modes = {r.layer_id: r.mode for r in extensions.layer_stress_modes} if extensions else {}
    stage_options = {r.stage_id: r for r in extensions.stage_options} if extensions else {}
    surface_loads = {r.id: r for r in extensions.surface_loads} if extensions else {}
    layer_data = [layer.model_dump() for layer in model.soil_layers]
    nodes = build_nodes(model)
    layer_tops = [layer.top_m for layer in model.soil_layers]
    # Positive linear shape weights preserve force and first moment for each
    # material station. Soil states remain at fixed material stations and are
    # lumped to the two adjacent beam nodes, retaining a stable ID across stages.
    gauss = ((-sqrt(3 / 5), 5 / 9), (0.0, 8 / 9), (sqrt(3 / 5), 5 / 9))
    stations = []
    for element, (lo, hi) in enumerate(zip(nodes, nodes[1:])):
        for gauss_id, (xi, weight) in enumerate(gauss):
            z = (lo + hi) / 2 + xi * (hi - lo) / 2
            integration_length = weight * (hi - lo) / 2
            for end, shape in ((0, (1 - xi) / 2), (1, (1 + xi) / 2)):
                stations.append((f'E{element}:G{gauss_id}:N{end}', element + end,
                                 z, integration_length * shape))
    support_map = {support.id: support for support in model.supports}
    stages = []
    for stage in model.stages:
        options = stage_options.get(stage.id)
        forces = [0.0] * len(nodes)
        springs = []
        for station_id, node, z, length_weight in stations:
            layer = model.soil_layers[bisect_right(layer_tops, z) - 1]
            ka = tan(pi / 4 - radians(layer.phi_deg) / 2) ** 2
            kp = 1 / ka
            pressure_area = model.pile.spacing_m * length_weight
            water_out = model.analysis.water_unit_weight_kn_m3 * max(0, z - stage.water_outside_depth_m)
            water_in = model.analysis.water_unit_weight_kn_m3 * max(0, z - stage.water_inside_depth_m)
            if not extensions:
                forces[node] += (water_out - water_in) * pressure_area
            for side, sign, surface, water, surcharge, m_value in (
                ('outside', -1, 0.0, stage.water_outside_depth_m, stage.surcharge_kpa, layer.m_outside_kn_m4),
                ('inside', 1, stage.excavation_depth_m, stage.water_inside_depth_m, 0.0, layer.m_inside_kn_m4),
            ):
                if z <= surface:
                    continue
                metadata = {}
                if extensions:
                    for load_id in options.active_load_ids if side == 'outside' else ():
                        load = surface_loads[load_id]
                        surcharge += (strip_vertical_increment(load.q_kpa, load.near_m, load.far_m, z)
                                      if load.kind == 'strip' else rectangle_vertical_increment(
                                          load.q_kpa, load.near_m, load.far_m,
                                          load.along_start_m, load.along_end_m, z))
                    vertical = vertical_stress(layer_data, z, surface, water, model.analysis.water_unit_weight_kn_m3)
                    state = rankine_pressure_state(vertical['total_vertical_kpa'], vertical['pore_pressure_kpa'],
                                                   surcharge, layer.phi_deg, layer.cohesion_kpa, layer.k0, modes[layer.id])
                    sigma = state['effective_vertical_kpa']
                    p0, pa, pp = state['p0_kpa'], state['active_kpa'], state['passive_kpa']
                    forces[node] -= sign * state['external_water_kpa'] * pressure_area
                    metadata = {'totalVerticalKpa': state['total_vertical_kpa'],
                                'surchargeVerticalKpa': surcharge,
                                'waterPressureKpa': vertical['pore_pressure_kpa'],
                                'externalWaterKpa': state['external_water_kpa'],
                                'pressureBasis': state['pressure_basis'],
                                'stressMethod': state['method_id'],
                                'verticalStressFieldsExcludeSurcharge': True}
                else:
                    sigma = effective_vertical_stress(model, z, surface, water) + surcharge
                    p0 = layer.k0 * sigma
                    pa = max(0.0, ka * sigma - 2 * layer.cohesion_kpa * sqrt(ka))
                    pp = kp * sigma + 2 * layer.cohesion_kpa * sqrt(kp)
                if p0 < pa - 1e-8 or p0 > pp + 1e-8:
                    raise ValueError(f'阶段{stage.id} 土层{layer.id} 的K0初始压力不在主动被动界限内；不得自动截断初态')
                springs.append({
                    'id': f'{side}:{station_id}', 'node': node, 'sign': sign, 'side': side,
                    'depthM': z, 'layerId': layer.id, 'weightM': length_weight,
                    'p0Kn': p0 * pressure_area,
                    'kKnPerM': m_value * (z - surface) * model.soil_effective_width_m * length_weight,
                    'lowerKn': pa * pressure_area, 'upperKn': pp * pressure_area,
                    'p0Kpa': p0, 'activeKpa': pa, 'passiveKpa': pp,
                    'effectiveVerticalKpa': sigma,
                    **metadata,
                })
        installed = []
        for support_id in stage.install_support_ids:
            support = support_map[support_id]
            installed.append({'id': support.id, 'node': nodes.index(round(support.depth_m, 10)),
                              'kind': support.kind, 'stiffnessKnPerM': support.stiffness_kn_per_m,
                              'compressionOnly': support.compression_only, 'preloadKn': support.preload_kn})
        stages.append({'id': stage.id, 'name': stage.name, 'event': stage.event,
                       'externalForcesKn': forces, 'soilSprings': springs,
                       'installSupports': installed, 'removeSupports': list(stage.remove_support_ids),
                       'excavationDepthM': stage.excavation_depth_m,
                       'waterInsideDepthM': stage.water_inside_depth_m,
                       'waterOutsideDepthM': stage.water_outside_depth_m,
                       'surchargeKpa': stage.surcharge_kpa})
        if extensions:
            stages[-1].update(soilHistoryAction=options.soil_history_action,
                              soilHistoryBasis=options.history_basis,
                              activeSurfaceLoadIds=list(options.active_load_ids))
    inertia = pi * model.pile.diameter_m ** 4 / 64
    physical = {'schemaVersion': 'pitguard.section-discrete/2' if extensions else 'pitguard.section-discrete/1', 'snapshotHash': snapshot.snapshot_hash,
            'analysisHash': snapshot.analysis_hash, 'nodesM': nodes,
            'eiKnM2': model.pile.elastic_modulus_kpa * inertia * model.pile.inertia_factor,
            'toeBoundary': model.pile.toe_boundary, 'stages': stages,
            'discretisation': 'three-point-Gauss prescribed-pressure integration preserves force and moment; displacement-dependent soil uses independent nodal lumping and requires mesh comparison',
            'forceBasis': 'single_pile', 'lengthUnit': 'm', 'forceUnit': 'kN'}
    if extensions:
        physical['methodExtensions'] = extensions.model_dump(mode='json')
    return physical
