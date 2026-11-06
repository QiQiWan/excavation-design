"""Evidence-based assessment of numerical groundwater models.

JGJ 120 Appendix C permits numerical consideration of heterogeneous aquifers,
but it does not make arbitrary finite-volume boundary conditions a site proof.
Passing this screening gate never turns an unknown field hydrogeology into a
formal design PASS. Failure or solver instability blocks design review.
"""
from __future__ import annotations
from math import inf, isfinite
from .seepage_fd import solve_seepage

GRADE_MIN_FACTOR = {1: 1.6, 2: 1.5, 3: 1.4}


def hydraulic_assurance(domain: dict, *, safety_grade: int | None,
                        boundary_verified: bool = False,
                        convergence_limit: float = .10) -> dict:
    """Check mesh sensitivity and hydraulic evidence using explicit grids.

    Thresholds are conservative *screening criteria* derived from the grade
    sequence for the homogeneous Appendix C C.0.2 case. They do NOT claim that
    the Appendix C homogeneous formula applies to the heterogeneous domain.
    """
    if not 0 < convergence_limit <= .5:
        raise ValueError('convergence_limit must be in (0,0.5]')
    runs=[]
    for nx,nz in ((24,16),(48,32)):
        r=solve_seepage(domain,dx_cells=nx,dz_cells=nz)
        runs.append(r)
        if r.get('status') != 'numerical_screening_only':
            return {'status':'solver_failure','formalDesignReady':False,
                    'meshRuns':runs,'reason':'渗流方程未达到数值残差条件，禁止继续工程判定'}
    a,b=runs
    ga=a['governingExit']['upwardGradient'];gb=b['governingExit']['upwardGradient']
    relative=abs(gb-ga)/max(abs(gb),abs(ga),1e-12)
    max_balance=max(r['waterBalance']['relativeImbalance'] for r in runs)
    factor=b.get('exitGradientSafetyFactor')
    threshold=GRADE_MIN_FACTOR.get(safety_grade)
    # Water-head difference of zero is a legitimate no-flow condition, not a
    # missing safety factor. Preserve this distinctly from a failed solver.
    no_flow=(abs(domain['outsideHeadM']-domain['insideHeadM'])<1e-12
             and gb < 1e-9)
    estimated=inf if no_flow else factor
    required=[]
    if threshold is None:required.append('safety_grade')
    if not boundary_verified:required.append('site_hydraulic_boundary_verification')
    if relative>convergence_limit:required.append('exit_gradient_mesh_convergence')
    if max_balance>1e-5:required.append('flow_mass_balance')
    if estimated is None:required.append('positive_exit_gradient_or_zero_head')
    # If the investigation detects an unacceptable gradient under even the
    # conservative *screening* criterion, never report that as acceptable.
    too_high=estimated is not None and threshold is not None and estimated<threshold
    status=('fail_screening' if too_high else
            'needs_boundary_verification' if required else 'screening_satisfactory')
    return {'schemaVersion':'pitguard.hydraulic-assurance/1',
        'status':status,'formalDesignReady':False,
        'numericalCriterionOnly':True,
        'criterionBasis':'conservative_grade_screen_from_Appendix_C_C.0.2_not_regulatory_heterogeneous_pass',
        'requiredGradeScreeningFactor':threshold,'fineGridExitGradient':gb,
        'fineGridCriticalGradient':b['criticalExitGradient'],
        'screeningFactor':None if estimated==inf else estimated,
        'noHeadDifference':no_flow,
        'meshRelativeDifference':relative,'allowedMeshRelativeDifference':convergence_limit,
        'maxWaterBalanceError':max_balance,'requiredEvidence':required,
        'fieldBoundaryVerified':boundary_verified,
        'meshRuns':[{'mesh':r['mesh'],'exitGradient':r['governingExit']['upwardGradient'],
           'factor':r['exitGradientSafetyFactor'],'balance':r['waterBalance']['relativeImbalance'],
           'residual':r['residualRelative']} for r in runs],
        'limitations':['两档网格检查只证明当前数值模型的稳定性；须校核顶部、远场、底部边界和帷幕实际连续性',
          '局部出逸梯度仅为流土筛查；非均质管涌、内部侵蚀、承压突涌和施工降水风险需分别审查',
          '均质层C.0.2的安全系数序列仅作为保守筛查门槛，未直接扩展为非均质规范判定']}
