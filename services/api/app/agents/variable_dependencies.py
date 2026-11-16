"""V3.88.3 dependency registry for constrained, reversible design trials.

Dependencies label required rechecks; a physical candidate still runs the
original solver and project-specific source reconfirmation. They do not imply
that changed material data or an unsupported code formula was evaluated.
"""
from __future__ import annotations
from .search_rules import ALLOWED_FIELDS

REGISTRY={
 'strut.stiffness_kn_m':{
    'dependsOn':['strut.id','strut.spacing_along_wall_m','stages.install_strut',
                 'opposite_wall_stiffness','support_EA_over_L','support_connections'],
    'recheck':['stage_displacement','brace_force','brace_member_capacity',
               'support_node_connections','opposite_wall_response'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'strut.depth_m':{
    'dependsOn':['stages.install_strut','stages.excavation_depth_m','pile.length_m',
                 'construction_sequence','support_connections'],
    'recheck':['stage_displacement','brace_force','pile_bending_shear',
               'support_node_connections','installation_reference_displacement'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'pile.length_m':{
    'dependsOn':['layers.bottom_m','pile.toe_boundary','groundwater_boundary'],
    'recheck':['embedment','passive_resultant','global_stability',
               'hydraulic_heave','pile_bending_shear'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'pile.diameter_m':{
    'dependsOn':['pile.spacing_m','pile.length_m','cage.bar_axis_cover_mm',
                 'foundation_drilling_method','support_connections'],
    'recheck':['section_capacity','all_stage_displacement','soil_reaction','pile_shear',
               'cage_fabrication_and_joint_stagger','minimum_longitudinal_area',
               'crack_control','drilling_clearance','drawing_and_bim_geometry'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'pile.inertia_factor':{
    'dependsOn':['pile.elastic_modulus_kpa','pile.diameter_m','cracking_state'],
    'recheck':['stage_displacement','pile_bending_shear','serviceability'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'cage.bar_count':{
    'dependsOn':['cage.bar_diameter_mm','cage.bar_axis_cover_mm','pile.diameter_m'],
    'recheck':['minimum_longitudinal_area','cage_clear_spacing','matched_NMV',
               'crack_control','anchorage_and_connections','cage_fabrication_and_joint_stagger'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'cage.bar_diameter_mm':{
    'dependsOn':['cage.bar_count','cage.bar_axis_cover_mm','cage.stirrup_diameter_mm'],
    'recheck':['minimum_longitudinal_area','cage_clear_spacing','matched_NMV',
               'crack_control','anchorage_and_connections','cage_fabrication_and_joint_stagger'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
 'cage.stirrup_spacing_mm':{
    'dependsOn':['cage.stirrup_diameter_mm','pile.diameter_m','shear_combination'],
    'recheck':['matched_NMV','stirrup_constructability','cage_fabrication_and_joint_stagger','anchorage_and_connections'],
    'engineeringSourceRequired':True,'sameModelMayNotValidateAllDependencies':True},
}
assert set(REGISTRY)==ALLOWED_FIELDS


def dependency_report(actions:list[dict])->dict:
    fields=[item['field'] for item in actions]
    if len(fields)!=len(set(fields)) or any(x not in REGISTRY for x in fields):
        raise ValueError('候选工程变量存在重复或未授权字段')
    checks=sorted({x for field in fields for x in REGISTRY[field]['recheck']})
    dependencies=sorted({x for field in fields for x in REGISTRY[field]['dependsOn']})
    return {'schemaVersion':'pitguard.a6-dependency-graph/3.88.6',
            'changedFields':fields,'requiredDependencyChecks':dependencies,
            'requiredRechecks':checks,
            'engineeringSourcesReconfirmationRequired':fields,
            'unqualifiedExternalDependencies':[x for x in dependencies if x in {
                'opposite_wall_stiffness','support_EA_over_L','support_connections',
                'cracking_state','groundwater_boundary','shear_combination',
                'foundation_drilling_method'}],
            'independentQualificationRequired':True,'formalConstructionIssue':False}
