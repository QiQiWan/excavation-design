# Mixed water / history method matrix

All three unchanged synthetic fixtures pass all seven stages against real OpenSees 3.8.0, including same-snapshot hand checks. This is numerical verification, not original A3-3 or FRWS equivalence.

## Definitions and scope

Each fixture derives from verification_example_v2. L1 uses effective separated water; L2 explicitly uses total combined undrained parameters phi_u=0, cu=10 kPa, K0=1. A fixed toe is imposed in all three fixtures as a defined physical boundary, avoiding rigid modes when undrained soil yields. No weak spring or peak fitting is used. The 30 kPa finite rectangle retains x=4.3..16.3 m and the explicitly synthetic centered y=-32.5..32.5 m location. HISTORY follows S3 and changes only the selected soil history policy; installed support references persist. Carry uses a no-change water_or_load event because the schema reserves soil_reset for non-carry policies.

Every case compares S0, S1, S2, S3, HISTORY, S4, S5: 581 nodal displacements, 581 rotations, 1148 element-end shears, 1148 element-end moments, 5700 pressures, 5700 plastic offsets, six support reactions and six installation references.

## Maximum absolute discrepancies

| Metric | Carry | Reset zero | Rebase |
|---|---:|---:|---:|
| nodalDisplacementM | 2.34968295e-15 | 3.01147995e-15 | 2.34968295e-15 |
| nodalRotation | 1.18145511e-15 | 1.18145511e-15 | 1.18145511e-15 |
| elementShearKn | 1.60262159e-08 | 1.53502455e-08 | 1.03249882e-08 |
| elementMomentKnM | 8.51846593e-10 | 8.16313239e-10 | 6.46267706e-10 |
| pressureKn | 1.96465066e-12 | 1.96465066e-12 | 1.96465066e-12 |
| plasticDisplacementM | 2.35055031e-15 | 3.01147995e-15 | 2.35055031e-15 |
| supportReactionKn | 3.72409659e-09 | 3.01255721e-09 | 1.73853323e-08 |
| supportReferenceM | 1.81885756e-15 | 1.81885756e-15 | 1.81885756e-15 |

Units follow metric names: m, kN and kN·m; rotation is dimensionless. All pointwise acceptance checks pass; maxNormalizedError is normalized to the combined acceptance allowance, not a raw relative percentage.

## Failure retained and corrected

The initial real-backend run failed all three cases at S2 with “OpenSees final tangent disagrees with an interior elastic soil branch”. It is preserved in method_matrix_before_yield_band_fix.json. Fixture definitions before and after were compared exactly and are identical. The failure occurred before the history checkpoint, so it was common to all policies.

At inside:E33:G0:N1, p0=0.12944014443502652 kN, k=99.58160130077921 kN/m, lower=0.06181914520747147 kN, upper=0.1970611436625816 kN, prior plastic displacement=0.001926784922561345 m and local displacement=0.002605836055945841 m. Reconstructed trial=0.19706114366256366 kN, while native pressure=upper and tangent=0. The interior distance is 1.7930101847696278e-14 kN, exceeding the previous 64eps pressure-arithmetic allowance 3.687621972096533e-15 kN, but below k*DBL_EPSILON=2.2111557318633504e-14 kN. All 42 flagged stations lie within 0.844 times the native yield band. Full station details are retained in mixed_failure_diagnostic.json; mixed_failure_physical.json is the first three-stage physical input.

Official source: https://github.com/OpenSees/OpenSees/blob/master/SRC/material/uniaxial/ElasticPPMaterial.cpp . ElasticPP uses fYieldSurface=-E*DBL_EPSILON, with elastic tangent only when f<=fYieldSurface. The strain-change early-return code is commented out; this is a yield-surface band, not a cached-update explanation. validate-opensees-yield-band.py confirms the installed 3.8.0 behavior independently at both yield bounds: distances 0, 0.25eps and 0.5eps have zero tangent; distances 1, 2, 4 and 8eps have tangent E. All 14 points match source.

Root implemented a restricted one-sided-corner recognition: native zero tangent, native pressure at the corresponding bound, and interior distance within k*eps plus arithmetic roundoff. Strictly exterior plastic states remain unpromoted; the existing stability cone check remains active. The final matrix records nativeYieldBandCornerCount=42 at S2 in every case, and zero at other stages. All stages retain strictlyPlasticBranchesPromoted=false.

## Reproduction

Run from the repository with the primary environment:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python scripts/validate-section-method-matrix.py --opensees-path /workspace/scratch/fd5d8a28c64e/work/section_opensees
PYTHONPATH=/workspace/scratch/fd5d8a28c64e/work/section_opensees /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python scripts/validate-opensees-yield-band.py
```

Native MPI initialization requires the authorized unrestricted local process environment. No downloads, full-suite tests, mesh campaigns or plot generation are part of these scripts.

## Source identities

Primary implementation hash: `7dabf98ee435f958ea6315fbaeffed3d969d91292a2fb11b4107f9124ab695db`

Independent source hash: `5aa9b1ef582a843a02b7a3527df8400353233422394ac53b25d6adee50645831`

Script SHA256: `59ddf2830d730f11a636c675585365c3d7bbbb74bbd90c5da2f32df8bbb398a4`

The JSON includes every fixture definition, snapshot/analysis/result/discrete identity, stage comparison metric and hand-check result.
