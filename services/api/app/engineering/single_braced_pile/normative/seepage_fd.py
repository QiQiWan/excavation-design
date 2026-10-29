"""Auditable 2D finite-volume seepage investigation, not a regulatory shortcut.

Horizontal ground, impermeable curtain (zero horizontal flux above toe),
Dirichlet heads at pit/outside top and far sides, zero flow at bottom.
Heterogeneous hydraulic conductivities are assigned by soil depth below pit.
The side and top heads are explicit modeling assumptions. Grid, balance, exit
gradient and residual are returned; this does NOT certify JGJ120 Appendix C for
a complex site without independent groundwater boundary/mesh validation.
"""
from __future__ import annotations
from math import isfinite


def _harmonic(a, b):
    return 2*a*b/(a+b)


def solve_seepage(section, *, dx_cells=32, dz_cells=24, atol=1e-9, iterations=16000):
    width_inside = section['widthInsideM']
    width_outside = section['widthOutsideM']
    depth = section['depthBelowPitM']
    toe = section['curtainEmbedmentBelowPitM']
    left_h = section['insideHeadM']
    right_h = section['outsideHeadM']
    if dx_cells % 2 or not (12<=dx_cells<=100 and 12<=dz_cells<=100):
        raise ValueError('The mesh needs even horizontal cells and at least 12 cells in each direction')
    if not (0 < toe < depth) or abs(width_inside-width_outside)>1e-10:
        raise ValueError('The supported domain uses equal side widths and a curtain shorter than depth')
    if not all(isfinite(x) for x in (left_h,right_h)) or left_h>right_h:
        raise ValueError('Outside head must be greater or equal to inside head for upward seepage check')
    layers=section['layers'];last=0.
    for layer in layers:
        if abs(layer['topM']-last)>1e-10 or layer['bottomM']<=last:
            raise ValueError('Hydraulic layers must be continuous from the pit base')
        if min(layer['khMPerS'],layer['kvMPerS'],layer['gammaSubKnM3'])<=0:
            raise ValueError('Positive conductivity and submerged unit weight required')
        last=layer['bottomM']
    if last<depth-1e-10:raise ValueError('Hydraulic layers do not cover the flow domain')
    nx=dx_cells;nz=dz_cells;dx=(width_inside+width_outside)/nx;dz=depth/nz
    x0=nx//2
    def layer_of(z):
        return next((v for v in layers if v['topM']<=z<v['bottomM']),layers[-1])
    kh=[layer_of((j+.5)*dz)['khMPerS'] for j in range(nz)]
    kv=[layer_of((j+.5)*dz)['kvMPerS'] for j in range(nz)]
    h=[[left_h+(right_h-left_h)*i/(nx-1) for i in range(nx)] for _ in range(nz)]
    # Coefficients as finite volume conductances (units m3/s per unit wall length).
    stencil=[]
    for j in range(nz):
        row=[]
        for i in range(nx):
            weights=[];constant=0.
            if i==0:constant+=2*kh[j]*dz/dx*left_h;diag=2*kh[j]*dz/dx
            else:
                conduct=kh[j]*dz/dx if not (i==x0 and (j+.5)*dz<toe) else 0.
                if conduct:weights.append((j,i-1,conduct))
                diag=conduct
            if i==nx-1:diag+=2*kh[j]*dz/dx;constant+=2*kh[j]*dz/dx*right_h
            else:
                conduct=kh[j]*dz/dx if not (i==x0-1 and (j+.5)*dz<toe) else 0.
                if conduct:weights.append((j,i+1,conduct))
                diag+=conduct
            if j==0:
                conduct=2*kv[j]*dx/dz;diag+=conduct
                constant+=conduct*(left_h if i<x0 else right_h)
            else:
                conduct=_harmonic(kv[j],kv[j-1])*dx/dz
                diag+=conduct;weights.append((j-1,i,conduct))
            if j<nz-1:
                conduct=_harmonic(kv[j],kv[j+1])*dx/dz
                diag+=conduct;weights.append((j+1,i,conduct))
            row.append((diag,constant,weights))
        stencil.append(row)
    # SOR; stopping uses explicit equation residual, not displacement increment.
    omega=1.55
    residual=1e12
    count=0
    for k in range(iterations):
        for j in range(nz):
            for i in range(nx):
                diag,constant,neighbors=stencil[j][i]
                nxt=(constant+sum(w*h[jj][ii] for jj,ii,w in neighbors))/diag
                h[j][i]+=omega*(nxt-h[j][i])
        if k%20==0 or k==iterations-1:
            worst=0.;scale=max(1.,abs(right_h-left_h))
            for j in range(nz):
                for i in range(nx):
                    diag,constant,neighbors=stencil[j][i]
                    resid=abs(diag*h[j][i]-constant-sum(w*h[jj][ii] for jj,ii,w in neighbors))
                    worst=max(worst,resid/max(diag,1e-30))
            residual=worst/scale
            if residual<atol:
                count=k+1;break
    if not count:
        return {'status':'solver_failure','residualRelative':residual,
                'reason':'Finite-volume seepage did not meet the stated residual tolerance',
                'mesh':{'nx':nx,'nz':nz}}
    gradient=[]
    for i in range(x0):
        upward=(h[0][i]-left_h)/(.5*dz)
        gradient.append({'xM':-width_inside+(i+.5)*dx,'upwardGradient':max(0,upward)})
    governing=max(gradient,key=lambda row:row['upwardGradient'])
    # Conservation of water volume is checked independently of the SOR
    # residual; sum the boundary fluxes on exactly the same finite volumes.
    q_left=sum(2*kh[j]*dz/dx*(left_h-h[j][0]) for j in range(nz))
    q_right=sum(2*kh[j]*dz/dx*(right_h-h[j][-1]) for j in range(nz))
    q_top=sum(2*kv[0]*dx/dz*((left_h if i<x0 else right_h)-h[0][i]) for i in range(nx))
    qnet=q_left+q_right+q_top
    flow_scale=max(abs(q_left)+abs(q_right)+abs(q_top),1e-16)
    balance_ratio=abs(qnet)/flow_scale
    gamma_sub=layers[0]['gammaSubKnM3'];water_gamma=section.get('waterGammaKnM3',9.81)
    critical=gamma_sub/water_gamma
    exit_grad=governing['upwardGradient']
    factor=critical/exit_grad if exit_grad>1e-12 else None
    return {'status':'numerical_screening_only','method':'two_dimensional_steady_finite_volume_curtain_v1',
            'mesh':{'nx':nx,'nz':nz,'dxM':dx,'dzM':dz},'iterations':count,
            'residualRelative':residual,
            'waterBalance':{'leftBoundaryInflowM2PerS':q_left,'rightBoundaryInflowM2PerS':q_right,
                'topBoundaryInflowM2PerS':q_top,'netBoundaryInflowM2PerS':qnet,
                'relativeImbalance':balance_ratio},
            'boundaryAssumptions':{
              'insideHeadM':left_h,'outsideHeadM':right_h,'top':'Dirichlet each side',
              'sides':'Dirichlet far field','bottom':'impermeable',
              'curtain':'impermeable above toe'},
            'governingExit':governing,'criticalExitGradient':critical,
            'exitGradientSafetyFactor':factor,'exitGradientSamples':gradient,
            'limitations':['基于显式边界条件的二维稳态渗流数值调查；未独立校核场地的远场及底边界',
               '局部流速、涌水量及边缘梯度需要网格加密、临界区不确定性检查',
               '不代替JGJ 120附录C适用方法和专业设计审查']}
