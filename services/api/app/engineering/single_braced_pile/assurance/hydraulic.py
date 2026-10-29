"""Traceable hydraulic investigation and numerical-verification dossier.

Site hydrogeological acceptance remains independent of a converged PDE solve.
No part of this module grants formal code compliance for heterogeneous aquifers.
"""
from __future__ import annotations
from math import hypot, isclose
from copy import deepcopy
from pydantic import Field, model_validator, ConfigDict
from .model import Strict, EvidenceBundle, audit_required
from ..normative.hydraulic_assurance import hydraulic_assurance
from ..normative.seepage_fd import solve_seepage


class HydraulicLayer(Strict):
    top_m:float=Field(alias='topM',ge=0)
    bottom_m:float=Field(alias='bottomM',gt=0)
    kh_m_s:float=Field(alias='khMPerS',gt=0,le=.1)
    kv_m_s:float=Field(alias='kvMPerS',gt=0,le=.1)
    gamma_sub_kn_m3:float=Field(alias='gammaSubKnM3',gt=0,le=25)


class HydraulicDomain(Strict):
    width_inside_m:float=Field(alias='widthInsideM',gt=0,le=200)
    width_outside_m:float=Field(alias='widthOutsideM',gt=0,le=200)
    depth_below_pit_m:float=Field(alias='depthBelowPitM',gt=0,le=100)
    curtain_embedment_below_pit_m:float=Field(alias='curtainEmbedmentBelowPitM',gt=0,le=100)
    inside_head_m:float=Field(alias='insideHeadM')
    outside_head_m:float=Field(alias='outsideHeadM')
    water_gamma_kn_m3:float=Field(alias='waterGammaKnM3',ge=9.5,le=10.5)
    layers:tuple[HydraulicLayer,...]=Field(min_length=1,max_length=30)
    @model_validator(mode='after')
    def geometry(self):
        if abs(self.width_inside_m-self.width_outside_m)>1e-8:
            raise ValueError('The existing FVM algorithm requires equal inside/outside far-field width')
        if not 0<self.curtain_embedment_below_pit_m<self.depth_below_pit_m:
            raise ValueError('Curtain toe must be inside numerical domain')
        if self.outside_head_m<self.inside_head_m:
            raise ValueError('Only the defined outside-to-inside hydraulic gradient is supported')
        z=0.
        for layer in self.layers:
            if abs(layer.top_m-z)>1e-8 or layer.bottom_m<=z:
                raise ValueError('Hydraulic strata must be contiguous and strictly increasing')
            z=layer.bottom_m
        if z<self.depth_below_pit_m-1e-8:
            raise ValueError('Hydraulic strata must cover the full domain depth')
        return self


class PathPoint(Strict):
    x_m:float
    z_below_pit_m:float=Field(ge=0)


class HeadScenario(Strict):
    """Explicit adverse construction water-head scenario, not a presumed default."""
    scenario_id:str=Field(alias='scenarioId',min_length=2,max_length=100)
    inside_head_m:float=Field(alias='insideHeadM')
    outside_head_m:float=Field(alias='outsideHeadM')
    rationale:str=Field(min_length=8,max_length=500)
    @model_validator(mode='after')
    def hydraulic_direction(self):
        if self.outside_head_m < self.inside_head_m:
            raise ValueError('Reverse flow requires a separately qualified hydraulic model')
        return self


class HydraulicAuditInput(Strict):
    project_id:str=Field(min_length=1)
    section_id:str=Field(min_length=1)
    safety_grade:int=Field(ge=1,le=3)
    domain:HydraulicDomain
    evidence:EvidenceBundle
    conceptual_flow_path:tuple[PathPoint,...]=Field(default=(),max_length=500)
    failure_modes:tuple[str,...]=('boiling','piping','confined_uplift')
    adverse_head_scenarios:tuple[HeadScenario,...]=Field(default=(),alias='adverseHeadScenarios',max_length=12)
    evaluate_boundary_sensitivity:bool=Field(default=False,alias='evaluateBoundarySensitivity')
    @model_validator(mode='after')
    def project_match(self):
        if (self.project_id,self.section_id)!=(self.evidence.project_id,self.evidence.section_id):
            raise ValueError('Hydraulic evidence must belong to the current project and section')
        valid={'boiling','piping','confined_uplift','internal_erosion','construction_drawdown'}
        if len({s.scenario_id for s in self.adverse_head_scenarios})!=len(self.adverse_head_scenarios):
            raise ValueError('Adverse head scenario IDs must be unique')
        if not self.failure_modes or not set(self.failure_modes)<=valid:
            raise ValueError('Hydraulic failure modes must be explicitly enumerated')
        return self


def _independent_direct_exit(domain:dict,nx:int,nz:int)->float:
    """Independent matrix assembly and sparse-direct solve of the *same BVP*.

    This checks the numerical implementation; common boundary/model errors are
    not detected by independent algebraic solutions to a shared BVP.
    Requires optional scipy, intentionally not part of the runtime lock.
    """
    from scipy.sparse import lil_matrix
    from scipy.sparse.linalg import spsolve
    W=domain['widthInsideM'];depth=domain['depthBelowPitM']
    assert abs(domain['widthInsideM']-domain['widthOutsideM'])<1e-8
    dx=2*W/nx;dz=depth/nz;layers=domain['layers']
    hi=domain['insideHeadM'];ho=domain['outsideHeadM'];toe=domain['curtainEmbedmentBelowPitM']
    def layer(j,key):
        zz=(j+.5)*dz
        return next((v[key] for v in layers if v['topM']<=zz<v['bottomM']),layers[-1][key])
    A=lil_matrix((nx*nz,nx*nz));b=[0.]*(nx*nz)
    def edge(p,q,c):
        A[p,p]+=c;A[q,q]+=c;A[p,q]-=c;A[q,p]-=c
    for j in range(nz):
        kh=layer(j,'khMPerS');kv=layer(j,'kvMPerS')
        for i in range(nx):
            p=j*nx+i
            if i==0:
                c=2*kh*dz/dx;A[p,p]+=c;b[p]+=c*hi
            if i==nx-1:
                c=2*kh*dz/dx;A[p,p]+=c;b[p]+=c*ho
            if j==0:
                c=2*kv*dx/dz;A[p,p]+=c;b[p]+=c*(hi if i<nx//2 else ho)
            if i<nx-1 and not (i==nx//2-1 and (j+.5)*dz<toe):
                edge(p,p+1,kh*dz/dx)
            if j<nz-1:
                kv2=layer(j+1,'kvMPerS')
                edge(p,p+nx,(2*kv*kv2/(kv+kv2))*dx/dz)
    h=spsolve(A.tocsr(),b)
    if not all(float('-inf')<float(v)<float('inf') for v in h):
        raise ValueError('Direct hydraulic solver returned nonfinite heads')
    return max(max(0.,(float(h[i])-hi)/(.5*dz)) for i in range(nx//2))


def audit_hydraulic(request:HydraulicAuditInput,*, independent:bool=True)->dict:
    domain=request.domain.model_dump(mode='json',by_alias=True)
    required={
        'hydraulic.outside_head_m':(domain['outsideHeadM'],'m'),
        'hydraulic.inside_head_m':(domain['insideHeadM'],'m'),
        'hydraulic.curtain_embedment_m':(domain['curtainEmbedmentBelowPitM'],'m'),
        'hydraulic.domain_depth_m':(domain['depthBelowPitM'],'m'),
        'hydraulic.far_field_boundary':('verified_site_interpretation','-'),
        'hydraulic.bottom_boundary':('verified_site_interpretation','-'),
        'hydraulic.flow_path_basis':('site_section','-'),
        'hydraulic.failure_mode_review':('reviewed','-'),
    }
    for i,layer in enumerate(domain['layers']):
        for field,unit in [('topM','m'),('bottomM','m'),('khMPerS','m/s'),
                           ('kvMPerS','m/s'),('gammaSubKnM3','kN/m3')]:
            required[f'hydraulic.layers.{i}.{field}']=(layer[field],unit)
    for case in request.adverse_head_scenarios:
        required[f'hydraulic.scenarios.{case.scenario_id}.inside_head_m']=(case.inside_head_m,'m')
        required[f'hydraulic.scenarios.{case.scenario_id}.outside_head_m']=(case.outside_head_m,'m')
    evidence=audit_required(request.evidence,required)
    flow=request.conceptual_flow_path
    length=sum(hypot(b.x_m-a.x_m,b.z_below_pit_m-a.z_below_pit_m)
               for a,b in zip(flow,flow[1:]))
    path_valid=(len(flow)>=2 and length>0 and
        all(-domain['widthInsideM']<=p.x_m<=domain['widthOutsideM'] and
            p.z_below_pit_m<=domain['depthBelowPitM'] for p in flow))
    numeric=hydraulic_assurance(domain,safety_grade=request.safety_grade,
          boundary_verified=evidence['status']=='metadata_confirmed' and path_valid)
    independent_check={'status':'not_run','method':'independent_sparse_direct_same_boundary_value_problem',
                       'note':'This is an algebraic cross-check, not an independent field hydrogeology model.'}
    if independent and numeric['status']!='solver_failure':
        try:
            values=[]
            for run in numeric['meshRuns']:
                mesh=run['mesh'];gradient=_independent_direct_exit(domain,mesh['nx'],mesh['nz'])
                old=run['exitGradient']
                values.append({'mesh':mesh,'directGradient':gradient,'primaryGradient':old,
                    'absoluteDifference':abs(gradient-old),
                    'consistent':isclose(gradient,old,rel_tol=2e-5,abs_tol=1e-7)})
            independent_check.update(status='passed' if all(v['consistent'] for v in values)
               else 'failed',meshChecks=values)
        except ImportError:
            independent_check['status']='dependency_unavailable'
        except Exception as exc:
            independent_check.update(status='failed',reason=str(exc))
    # A single nominal water level is not enough for staged excavation. Adverse
    # head conditions are separate BVPs, never algebraic multipliers on output.
    scenario_runs=[]
    for scenario in request.adverse_head_scenarios:
        scenario_domain=deepcopy(domain)
        scenario_domain['insideHeadM']=scenario.inside_head_m
        scenario_domain['outsideHeadM']=scenario.outside_head_m
        trial=hydraulic_assurance(scenario_domain,safety_grade=request.safety_grade,
                                  boundary_verified=False)
        scenario_runs.append({'scenarioId':scenario.scenario_id,
            'insideHeadM':scenario.inside_head_m,'outsideHeadM':scenario.outside_head_m,
            'rationale':scenario.rationale,'numericalStatus':trial['status'],
            'exitGradient':trial.get('fineGridExitGradient'),
            'screeningFactor':trial.get('screeningFactor'),
            'meshRelativeDifference':trial.get('meshRelativeDifference'),
            'waterBalanceError':trial.get('maxWaterBalanceError'),
            'regulatoryApproval':False})
    boundary_check={'status':'not_requested','basis':'far_field_distance_sensitivity_not_field_validation'}
    if request.evaluate_boundary_sensitivity:
        # Vary the numerical side-boundary positions while keeping all physical
        # conductivity, head and toe inputs fixed. This is a model sensitivity
        # check, not evidence of correct far-field groundwater conditions.
        larger=deepcopy(domain)
        larger['widthInsideM']*=1.5
        larger['widthOutsideM']*=1.5
        try:
            # Keep dx and dz identical: original 48 x 32 on 2W and 72 x 32
            # on 3W. Different dx would confound boundary and mesh effects.
            alternative=solve_seepage(larger,dx_cells=72,dz_cells=32)
            a=numeric.get('fineGridExitGradient')
            b=(alternative['governingExit']['upwardGradient'] if
                alternative.get('status')=='numerical_screening_only' else None)
            relative=(abs(a-b)/max(abs(a),abs(b),1e-12) if a is not None and b is not None else None)
            boundary_check={'status':('calculated_sensitivity_only' if relative is not None else 'solver_failure'),
                'originalSideWidthM':domain['widthInsideM'],
                'expandedSideWidthM':larger['widthInsideM'],
                'exitGradientOriginal':a,'exitGradientExpanded':b,
                'meshResolutionHeldConstant':True,'numericalMeshExpanded':{'nx':72,'nz':32},
                'relativeDifference':relative,'regulatoryApproval':False}
        except Exception as exc:
            boundary_check={'status':'solver_failure','reason':str(exc),'regulatoryApproval':False}
    scenarios_failed=any(x['numericalStatus'] in ('fail_screening','solver_failure') for x in scenario_runs)
    sensitivity_failed=boundary_check['status']=='solver_failure'
    if numeric['status'] in ('fail_screening','solver_failure') or independent_check['status']=='failed' or scenarios_failed or sensitivity_failed:
        state='fail_or_numerical_invalid'
    elif evidence['status']!='metadata_confirmed' or not path_valid:
        state='incomplete_site_evidence'
    elif independent_check['status']!='passed':
        state='independent_numerical_check_missing'
    else:
        state='site_engineering_review_required'
    return {'schemaVersion':'pitguard.hydraulic-standard-audit/1',
        'projectId':request.project_id,'sectionId':request.section_id,
        'assessmentStatus':state,'formalDesignReady':False,
        'sourceEvidence':evidence,
        'flowPath':{'status':'documented_conceptual_only' if path_valid else 'missing_or_invalid',
                    'lengthM':length if path_valid else None,
                    'pointCount':len(flow),
                    'disclaimer':'A user-drawn path is not a computed streamline.'},
        'numericalInvestigation':numeric,'independentNumericalCheck':independent_check,
        'adverseHeadScenarioChecks':scenario_runs,
        'farFieldBoundarySensitivity':boundary_check,
        'failureModeChecks':{mode:{'status':'separate_criterion_required',
            'siteSpecificCriterionVerified':False} for mode in request.failure_modes},
        'normativeBasis':['JGJ 120-2012 4.2.6, 7.1.4, Appendix C',
                         'GB 55003-2021 Chapter 7: applicable mandatory provisions'],
        'requiredNextActions':['确认原始水文资料的文件字节/来源和现场边界解释',
            '按渗透破坏、承压突涌、内蚀分别确定适用判据并独立审核',
            '由有权限的工程师审核网格、域边界及水位不利组合']}
