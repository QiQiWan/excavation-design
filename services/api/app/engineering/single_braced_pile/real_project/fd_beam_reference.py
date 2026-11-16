"""Independent 1-D finite-difference Euler-Bernoulli beam verification.

This is a verification of the beam DIFFERENTIAL OPERATOR on explicitly linear
benchmark problems. It is deliberately not a replacement for the history-
carrying, one-sided/plastic staged earth-reaction solver.

Unknowns: horizontal deflection y at nodes. Curvature is represented by
nonuniform three-point divided differences. The discrete bending work is
sum(EI * curvature**2 * integration_width)/2. No beam-element rotations,
commercial FE software, or imported beam stiffness matrices are used.
"""
from __future__ import annotations
import math
import numpy as np


def solve_linear_fd(nodes_m: list[float], ei_kn_m2: float,
                    nodal_forces_kn: list[float], *,
                    elastic_springs_kn_m: dict[int, float] | None = None,
                    prescribed_displacements_m: dict[int, float] | None = None) -> dict:
    nodes = np.asarray(nodes_m, dtype=float)
    forces = np.asarray(nodal_forces_kn, dtype=float)
    n = len(nodes)
    if n < 5 or len(forces) != n or not np.all(np.isfinite(nodes)) or not np.all(np.diff(nodes) > 0):
        raise ValueError('Finite difference needs at least five increasing nodes and matching finite loads')
    if not np.all(np.isfinite(forces)) or not math.isfinite(ei_kn_m2) or ei_kn_m2 <= 0:
        raise ValueError('EI and nodal loads must be finite and physically valid')
    d2 = np.zeros((n - 2, n), dtype=float)
    weights = np.zeros(n - 2, dtype=float)
    for i in range(1, n - 1):
        a, b = nodes[i] - nodes[i-1], nodes[i+1] - nodes[i]
        d2[i-1, i-1] = 2 / (a * (a + b))
        d2[i-1, i] = -2 / (a * b)
        d2[i-1, i+1] = 2 / (b * (a + b))
        weights[i-1] = (a + b) / 2
    matrix = ei_kn_m2 * (d2.T * weights) @ d2
    for ix, k in (elastic_springs_kn_m or {}).items():
        if not isinstance(ix, int) or not 0 <= ix < n or not math.isfinite(k) or k < 0:
            raise ValueError('Invalid spring node or stiffness')
        matrix[ix, ix] += k
    prescribed = prescribed_displacements_m or {}
    for ix, value in prescribed.items():
        if not isinstance(ix, int) or not 0 <= ix < n or not math.isfinite(value):
            raise ValueError('Invalid prescribed node/displacement')
    free = np.array([i for i in range(n) if i not in prescribed], dtype=int)
    fixed = np.array(list(prescribed), dtype=int)
    answer = np.zeros(n, dtype=float)
    if len(fixed):
        answer[fixed] = np.array([prescribed[i] for i in fixed], dtype=float)
    if len(free):
        sub = matrix[np.ix_(free, free)]
        if np.linalg.matrix_rank(sub, tol=max(1., np.linalg.norm(sub, ord=2)) * 1e-12) != len(free):
            raise ValueError('Finite-difference benchmark has insufficient physical constraints')
        answer[free] = np.linalg.solve(sub, forces[free] - matrix[np.ix_(free, fixed)] @ answer[fixed])
    residual = matrix @ answer - forces
    curvature = d2 @ answer
    return {'method':'independent_nonuniform_finite_difference_beam_operator',
            'nodesM':nodes.tolist(), 'displacementsM':answer.tolist(),
            'momentAtInteriorKnM':(ei_kn_m2 * curvature).tolist(),
            'maxFreeResidualKn':float(max(abs(residual[free]), default=0)),
            'maxAbsDisplacementMm':float(1000 * max(abs(answer))),
            'isNonlinearStageVerification':False,
            'isCompleteEngineeringCheck':False}


def verify_cantilever_uniform_load(*, length_m: float = 4.,
                                   ei_kn_m2: float = 25000.,
                                   distributed_load_kn_m: float = 20., segments: int = 80) -> dict:
    """Closed-form cantilever (two adjacent toe nodes fixed) vs independent FD."""
    if segments < 20 or segments > 140:
        raise ValueError('Cantilever verification needs 20..140 segments')
    dx = length_m / segments
    nodes = [i * dx for i in range(segments+1)]
    forces = [distributed_load_kn_m * dx] * (segments+1)
    forces[0] /= 2
    forces[-1] /= 2
    result = solve_linear_fd(nodes,ei_kn_m2,forces,
                             prescribed_displacements_m={segments:0.,segments-1:0.})
    analytic_tip = distributed_load_kn_m * length_m**4/(8*ei_kn_m2)
    computed_tip = abs(result['displacementsM'][0])
    err = abs(computed_tip-analytic_tip)/analytic_tip
    return {'comparison':'uniform_transverse_load_fixed_toe_free_head',
            'analyticTipDisplacementMm':1000*analytic_tip,
            'fdTipDisplacementMm':1000*computed_tip,
            'relativeDifference':err,
            'methodIndependence':'nonuniform_fd_curvature_energy_vs_closed_form_EB_beam',
            'linearOperatorOnly':True,'numericalBenchmarkPassed':bool(err < .025),
            'engineeringQualified':False}
