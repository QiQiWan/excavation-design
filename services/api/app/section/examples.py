"""Fully explicit verification definitions. No input is an inferred FRWS setting."""
from math import pi

from .models import verification_example


def verification_example_v2() -> dict:
    data = verification_example()
    data.update(schema_version='pitguard.frozen-section/2', section_id='VERIFY-METHODS-02',
                name='有限邻载、阶段历史与工程配筋检查验证例', reference_contract_hash=None)
    data['analysis']['pressure_method'] = 'explicit_rankine_stress_modes_v2'
    data['method_extensions'] = {
        'schema_version': 'pitguard.method-extensions/1', 'geometry_basis': 'flat_wall_top_surface',
        'm_law': 'k_h=m*z', 'm_depth_reference': 'current_side_surface',
        'm_basis': '合成验证例：从各侧现地表起算k_h=m*z；每层独立m值，不推定FRWS原设置。',
        'surface_load_method': 'boussinesq_vertical_rankine_k_mapping_v1',
        'surface_loads': [{'id': 'Q-RECT', 'kind': 'rectangle', 'q_kpa': 30.0,
            'near_m': 4.3, 'far_m': 16.3, 'along_start_m': -32.5, 'along_end_m': 32.5,
            'basis': '合成验证例：邻载尺寸参考原文；假定剖面位于65m长度中点且距离从墙轴线起算，原项目这两项未确认。'}],
        'layer_stress_modes': [{'layer_id': layer['id'], 'mode': 'effective_separated',
            'parameter_basis': 'effective', 'basis': '合成砂土验证层明确采用有效应力c、phi和K0。'} for layer in data['soil_layers']],
        'stage_options': [{'stage_id': stage['id'], 'active_load_ids': [] if i == 0 else ['Q-RECT'],
            'soil_history_action': 'carry_plastic_history_v1',
            'history_basis': '合成验证例明确继承此前收敛塑性位移；FRWS9.0原项目规则待补。'} for i, stage in enumerate(data['stages'])],
    }
    data['engineering_design'] = {
        'nm_cases': [{'stage_id': stage['id'], 'axial_force_design_kn': 0.0,
            'axial_force_basis': '合成二维横向受弯验证例明确指定各阶段N=0，不代替原项目竖向作用分析。',
            'second_order_assessment': 'not_required_by_project_assessment',
            'second_order_basis': '合成算例N=0且不设置轴向受压构件，不存在本算例轴向二阶放大。'} for stage in data['stages']],
        'shear': {'concrete_tensile_design_strength_mpa': 1.43, 'concrete_strength_coefficient_beta_c': 1.0,
            'coefficient_basis': '合成C30材料检查取beta_c=1.0。', 'stirrup_design_strength_mpa': 270.0,
            'effective_hoop_area_mm2': 2*pi*8**2/4,
            'hoop_area_basis': '验证例显式按Phi8闭合圆箍在剪力方向的两有效截面积100.530965mm2计；工程需核对原方法口径。',
            'loading_case': 'general_bending_nonprestressed', 'axial_force_design_kn': 0.0,
            'seismic_case': 'nonseismic', 'applicability_basis': '合成普通受弯圆截面，无预应力、扭矩和地震设计作用，逐阶段N=0。'},
        'detailing': {'hoop_arrangement': 'external_tangent_closed_circular', 'minimum_clear_cover_mm': 50.0,
            'minimum_clear_spacing_mm': 75.0, 'maximum_stirrup_spacing_mm': 150.0,
            'minimum_stirrup_diameter_mm': 6.0,
            'criterion_basis': '合成项目给定的50mm外箍净保护层、75mm净距、150mm最大箍距和6mm最小箍径，仅检验几何判断。'},
        'anchorage': {'bar_surface': 'deformed', 'seismic_case': 'nonseismic',
            'concrete_tensile_design_strength_mpa_capped_c60': 1.43,
            'material_basis': '合成锚固区C30混凝土抗拉设计强度，未达到C60上限。',
            'epoxy_coated': False, 'construction_disturbed': False,
            'top_available_length_mm': 600.0, 'bottom_available_length_mm': 650.0,
            'transverse_diameter_mm': 8.0, 'transverse_spacing_mm': 125.0,
            'applicability_basis': '合成非抗震梁柱类直锚区两个端部，非梁柱节点；仅用于规则验证，不认定排桩连接细节。'},
        'lap': {'arrangement': 'continuous_no_lap', 'arrangement_basis': '合成给定钢筋全长连续无接头，实际桩笼接头条件未确认。',
            'seismic_case': 'nonseismic'},
        'service': {'cases': [{'stage_id': stage['id'], 'axial_force_service_kn': 0.0,
                'characteristic_effect_scale': 1.0, 'combination_basis': '合成使用阶段按已算特征效应1.0倍及N=0，非原项目准永久组合恢复。'} for stage in data['stages']],
            'concrete_elastic_modulus_mpa': 30000.0,
            'long_term_basis': '本验证仅算短期裂后弹性截面应力；尚无已核实圆截面长期裂缝宽度公式。',
            'crack_width_limit_mm': .2, 'limit_basis': '合成项目显式指定0.2mm限值，缺圆截面适用方法故不判定裂缝通过。'},
    }
    data['limitations'] = [
        '完整合成验证例；原A3-3地形、标高、材料、边界、软件选项仍须按原资料另行固定。',
        '邻载按水平弹性半空间竖向应力影响再映射土压力，适用性需另行判断，未宣称等同FRWS9.0。',
        '坡台主动楔体仅有独立作用核验；未建立原项目分层含水坡台的完整阶段模型。',
        '保护层与锚固输入故意保留可识别的不满足项，供结果验证；不据此认定原项目配筋不合格。',
    ]
    return data
