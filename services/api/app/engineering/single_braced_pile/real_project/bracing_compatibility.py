"""Two-end brace compatibility with explicit opposite-wall compliance.

This checks an axial member connecting two laterally elastic boundary nodes.
It is not a three-dimensional support-frame analysis and cannot infer the
opposite-wall properties from the one-side FRWS PDF reaction plot.
"""
from __future__ import annotations


def coupled_two_end_brace(*,member_axial_stiffness_kn_m:float,
        left_wall_stiffness_kn_m:float,right_wall_stiffness_kn_m:float,
        left_free_displacement_m:float,right_free_displacement_m:float,
        installed_relative_displacement_m:float=0.)->dict:
    for x in (member_axial_stiffness_kn_m,left_wall_stiffness_kn_m,right_wall_stiffness_kn_m):
        if x<=0:raise ValueError('Both wall compliances and brace EA/L must be positive')
    k=member_axial_stiffness_kn_m
    free_gap=left_free_displacement_m-right_free_displacement_m-installed_relative_displacement_m
    n=k*free_gap/(1+k*(1/left_wall_stiffness_kn_m+1/right_wall_stiffness_kn_m))
    left=left_free_displacement_m-n/left_wall_stiffness_kn_m
    right=right_free_displacement_m+n/right_wall_stiffness_kn_m
    equilibrium_residual=max(abs(left_wall_stiffness_kn_m*(left-left_free_displacement_m)+n),
                             abs(right_wall_stiffness_kn_m*(right-right_free_displacement_m)-n),
                             abs(n-k*(left-right-installed_relative_displacement_m)))
    equivalent=k*right_wall_stiffness_kn_m/(k+right_wall_stiffness_kn_m)
    return {'status':'calculated_scope_limited','model':'two_wall_nodes_one_axial_member_linear/1',
       'leftWallDisplacementM':left,'rightWallDisplacementM':right,
       'memberAxialKn':n,'leftEndForceKn':-n,'rightEndForceKn':n,
       'endForceBalanceKn':0.,'equationResidualKn':equilibrium_residual,
       'effectiveLeftWallSupportStiffnessKnM':equivalent,
       'limitations':['仅两端线性等效墙弹簧和单根轴向构件，缺少平面支撑体系和多阶段土墙历史',
                      '真实工程须提供两端墙体自由位移、等效刚度、安装参考与EA/L的来源']}
