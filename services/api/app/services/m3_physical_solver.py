"""M3 depth-distributed spatial frame and unilateral Winkler research benchmark.

NOT a qualified geotechnical or nonlinear soil-structure model. Soil stiffness,
net pressure, pile toe, constitutive law, node stiffness, and stage sequencing
require independent engineering evidence and review before any design use.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from math import ceil, hypot, sqrt
from time import perf_counter
import json
import numpy as np

from .m3_spatial_frame import (build_structural_graph, element_matrix,
    _add, _solver, _dominated)
from .m3_plan_topology import _polygon, plan_topology_candidates
from .m3_physical_evidence import M3PhysicalStudy, PhysicalStage, WallPressureProfile


_GAUSS_X = (0.1127016653792583, 0.5, 0.8872983346207417)
_GAUSS_W = (5./18, 8./18, 5./18)


def hermite_line_load_local(length: float, segments: list[tuple[float, float, float, float]],
                            qlocal_direction: np.ndarray) -> np.ndarray:
    """Consistent 12-DOF nodal loads for piecewise-linear transverse q(s).

    segments: [(s_start, s_end, q_start, q_end)], dimensionless s in [0,1].
    The 3-point Gauss rule is exact for cubic shape functions times linear q.
    """
    f = np.zeros(12)
    for sa, sb, qa, qb in segments:
        if not (0 <= sa < sb <= 1 + 1e-10):
            raise ValueError('Invalid distributed pressure integration interval')
        for gauss, weight in zip(_GAUSS_X, _GAUSS_W):
            s = sa + (sb-sa)*gauss
            q = qa+(qb-qa)*gauss
            n1 = 1 - 3*s*s + 2*s*s*s
            n2 = length*(s - 2*s*s + s*s*s)
            n3 = 3*s*s - 2*s*s*s
            n4 = length*(-s*s + s*s*s)
            factor = length*(sb-sa)*weight*q
            f[[1,5,7,11]] += factor*qlocal_direction[1]*np.array([n1,n2,n3,n4])
            f[[2,4,8,10]] += factor*qlocal_direction[2]*np.array([n1,-n2,n3,-n4])
            # Axial load is not used in this horizontal wall-pressure benchmark.
    return f


def _interp(profile: WallPressureProfile, z: float) -> float:
    a = profile.samples
    if z >= a[0].z_m:
        return a[0].pressure_kpa
    if z <= a[-1].z_m:
        return a[-1].pressure_kpa
    for u,v in zip(a,a[1:]):
        if u.z_m >= z >= v.z_m:
            t = (u.z_m-z)/(u.z_m-v.z_m)
            return u.pressure_kpa*(1-t)+v.pressure_kpa*t
    raise ValueError('Pressure interpolation grid inconsistent')


def _common_vertical_levels(study:M3PhysicalStudy):
    frame = study.frame
    zz = {frame.crown_z_m,frame.strut_z_m,frame.toe_z_m}
    for stage in frame.stages:
        zz.add(frame.crown_z_m-stage.excavation_depth_m)
    for stage in study.boundary.stages:
        for profile in stage.profiles:
            zz.update(p.z_m for p in profile.samples)
        for contact in stage.contacts:
            zz.add(contact.z_top_m);zz.add(contact.z_bottom_m)
    source = sorted(zz,reverse=True)
    levels = []
    for z1,z2 in zip(source,source[1:]):
        levels.append(z1)
        n=max(1,ceil((z1-z2)/study.boundary.max_vertical_mesh_m))
        levels.extend(z1+(z2-z1)*i/n for i in range(1,n))
    levels.append(source[-1])
    levels = sorted({round(z,8) for z in levels}, reverse=True)
    if len(levels)>32:
        raise ValueError('Physical wall vertical mesh exceeds 32 levels')
    return levels


def _augment_wall_graph(study:M3PhysicalStudy,candidates:list[dict],candidate:dict):
    graph,face_xy=build_structural_graph(study.frame,candidates,candidate)
    levels=_common_vertical_levels(study)
    original_wall=[m for m in graph.members if m['group']=='wall']
    graph.members=[m for m in graph.members if m['group']!='wall']
    wall_node_ids={}
    wall_segments=defaultdict(list)
    for face in face_xy:
        for x,y in face:
            key=(x,y)
            if key not in wall_node_ids:
                wall_node_ids[key]={z:graph.node(x,y,z,'pile_depth') for z in levels}
    for m in original_wall:
        a,b=graph.nodes[m['a']],graph.nodes[m['b']]
        z_hi=max(a[2],b[2]);z_lo=min(a[2],b[2]);xy=(float(a[0]),float(a[1]))
        member_levels=[z for z in levels if z_lo-1e-7<=z<=z_hi+1e-7]
        for j,(z1,z2) in enumerate(zip(member_levels,member_levels[1:])):
            if z1-z2 < 1e-6:
                continue
            ni=wall_node_ids[xy][z1];nj=wall_node_ids[xy][z2]
            # Preserve top-to-bottom local beam orientation regardless of old members.
            mid=m['id']+':'+str(j)
            graph.member(mid,'beam',ni,nj,m['section'],'wall')
    if len(graph.nodes)>320 or len(graph.members)>650:
        raise ValueError('Physical frame exceeds verified 320-node / 650-member budget')
    for m in graph.members:
        if m['group']=='wall':
            wall_segments[tuple(graph.nodes[m['a']][:2])].append(m)
    for segs in wall_segments.values():
        segs.sort(key=lambda m:-graph.nodes[m['a']][2])
    return graph,face_xy,levels,wall_segments,wall_node_ids


def _face_geometry(frame, face_xy):
    poly,coordinates = _polygon(frame.topology.excavation.vertices)
    orientation=1. if poly.exterior.is_ccw else -1.
    details={}
    for face,points in enumerate(face_xy):
        a,b=coordinates[face],coordinates[(face+1)%len(coordinates)]
        L=hypot(b[0]-a[0],b[1]-a[1])
        inward=np.array([orientation*-(b[1]-a[1])/L,
                         orientation*(b[0]-a[0])/L,0.])
        tributary=defaultdict(float)
        for pa,pb in zip(points,points[1:]):
            h=hypot(pb[0]-pa[0],pb[1]-pa[1]);tributary[tuple(pa)]+=h/2;tributary[tuple(pb)]+=h/2
        details[face]={'normalInward':inward,'widthByPosition':tributary,'length':L,
                       'midpointXY':((a[0]+b[0])/2,(a[1]+b[1])/2)}
    return details


def _integrate_profile_on_element(profile, z1,z2,trib_width,unit_normal,T):
    length=z1-z2
    knots=[z1]+[p.z_m for p in profile.samples if z2+1e-9 < p.z_m < z1-1e-9]+[z2]
    intervals=[]
    for za,zb in zip(knots,knots[1:]):
        intervals.append(((z1-za)/length,(z1-zb)/length,
                          _interp(profile,za),_interp(profile,zb)))
    f_local=hermite_line_load_local(length,intervals,T[:3,:3]@unit_normal*trib_width)
    return T.T@f_local


def _load_wall_pressures(frame,physical_stage,graph,face_geom,wall_segments):
    nd=6*len(graph.nodes)
    F=np.zeros(nd);source_refs=[]
    expected_force=np.zeros(3);expected_moment=np.zeros(3)
    for profile in physical_stage.profiles:
        f=face_geom[profile.face_index]
        face_force_scalar=0.;first_z_moment=0.
        for p0,p1 in zip(profile.samples,profile.samples[1:]):
            dz=p0.z_m-p1.z_m
            face_force_scalar+=dz*(p0.pressure_kpa+p1.pressure_kpa)/2
            first_z_moment+=dz*(2*p0.pressure_kpa*p0.z_m + p0.pressure_kpa*p1.z_m +
                                  p1.pressure_kpa*p0.z_m + 2*p1.pressure_kpa*p1.z_m)/6
        force=f['length']*face_force_scalar*f['normalInward']
        z_first=f['length']*first_z_moment
        expected_force+=force
        cx,cy=f['midpointXY']
        expected_moment+=np.cross(np.array([cx,cy,0.]),force)+np.cross(
            np.array([0.,0.,z_first]),f['normalInward'])
        for xy,width in f['widthByPosition'].items():
            for m in wall_segments[xy]:
                n1,n2=m['a'],m['b']
                z1=float(graph.nodes[n1][2]);z2=float(graph.nodes[n2][2])
                _,T,_,_=element_matrix('beam',graph.nodes[n1],graph.nodes[n2],m['section'])
                element_load=_integrate_profile_on_element(profile,z1,z2,width,f['normalInward'],T)
                dofs=np.r_[np.arange(6*n1,6*n1+6),np.arange(6*n2,6*n2+6)]
                F[dofs]+=element_load
        source_refs.append({'faceIndex':profile.face_index,'sourceSha256':profile.source_sha256,
                            'pointCount':len(profile.samples),'integration':'exact_piecewise_linear_x_cubic_hermite_3pt_gauss'})
    assembled_force=np.zeros(3);assembled_moment=np.zeros(3)
    for index,point in enumerate(graph.nodes):
        force=F[6*index:6*index+3];couple=F[6*index+3:6*index+6]
        assembled_force+=force
        assembled_moment+=np.cross(point,force)+couple
    force_diff=float(np.linalg.norm(assembled_force-expected_force))
    moment_diff=float(np.linalg.norm(assembled_moment-expected_moment))
    tolerance=max(1e-5,1e-8*max(np.linalg.norm(expected_force),np.linalg.norm(expected_moment)))
    if max(force_diff,moment_diff)>tolerance:
        raise ValueError('Distributed wall pressure force/moment conservation failed')
    return F,source_refs,{'status':'analytical_resultant_equilibrium_checked',
       'forceResidualKn':round(force_diff,10),
       'momentResidualKnM':round(moment_diff,10),
       'note':'Checks load numerical integration only; not earth-pressure model qualification'}


def _contact_springs(physical_stage,graph,face_geom,wall_node_ids,levels):
    springs=[]
    for soil in physical_stage.contacts:
        f=face_geom[soil.face_index]
        direction=f['normalInward'][:2] * (1 if soil.side=='inside' else -1)
        for xy,width in f['widthByPosition'].items():
            for za,zb in zip(levels,levels[1:]):
                if za<=soil.z_top_m+1e-7 and zb>=soil.z_bottom_m-1e-7:
                    k=.5*soil.stiffness_kn_m3*width*(za-zb)
                    for z in (za,zb):
                        node=wall_node_ids[xy][z]
                        springs.append((node,k,direction,soil.gap_mm/1000,soil.source_sha256,soil.side))
    return springs


def solve_contact_newton(K,rhs,free,springs,*,warmstart=None,max_iterations=35):
    """Active-contact semismooth Newton solve of convex unilateral spring energy.

    Spring force: k * max(n.dot(u)-gap,0) * n. Positive relative wall
    displacement presses the declared soil half-space. Boundary DOFs fixed.
    """
    nd=len(rhs)
    u=np.zeros(nd) if warmstart is None else np.array(warmstart,dtype=float).copy()
    free=np.asarray(free,dtype=int)
    fixed=np.setdiff1d(np.arange(nd),free)
    u[fixed]=0.
    contacts=defaultdict(lambda: {'k':0.,'n':None,'gap':0.,'source':None})
    # Aggregate equal node/side/source contributions; no contact topology changes.
    for node,k,direction,gap,source,side in springs:
        key=(node,tuple(float(x) for x in direction),gap,source,side)
        contacts[key]['k']+=k
        contacts[key]['n']=np.asarray(direction)
        contacts[key]['gap']=gap
        contacts[key]['source']=source
    prepared=[]
    for key,v in contacts.items():
        node=key[0]
        ix=2 if False else 6*node
        prepared.append((ix,v['k'],v['n'],v['gap'],v['source'],key[-1]))

    def forces_and_tangent(x,build_tangent=True):
        f=np.zeros(nd)
        tangent=np.zeros_like(K) if build_tangent else None
        count=0
        for ix,k,n,gap,source,side in prepared:
            depth=float(n@x[ix:ix+2]-gap)
            if depth > 0:
                f[ix:ix+2]+=k*depth*n
                count+=1
                if build_tangent:
                    tangent[ix:ix+2,ix:ix+2]+=k*np.outer(n,n)
        return f,tangent,count

    scale=max(1.,float(np.linalg.norm(rhs[free])))
    for iteration in range(1,max_iterations+1):
        cf,kt,active=forces_and_tangent(u)
        residual=K@u+cf-rhs
        err=float(np.linalg.norm(residual[free]))
        if err <= max(1e-6,1e-8*scale):
            return u,{'converged':True,'iterations':iteration,'activeContacts':active,
                      'freeResidualKn':err,'contactInternalForces':cf,'contactTangent':kt,
                      'contactModel':'compression_only_winkler_reference'}
        du,_=_solver(K+kt,-residual,free)
        trial=np.zeros(nd);trial[free]=du
        # Backtracking on the convex nonlinear spring potential.
        def energy(x):
            base=.5*float(x@(K@x))-float(rhs@x)
            return base+sum(.5*k*max(0.,float(n@x[ix:ix+2])-gap)**2
                        for ix,k,n,gap,_,_ in prepared)
        e0=energy(u);step=1.
        for _ in range(18):
            tentative=u+step*trial
            if energy(tentative)<=e0+1e-10:
                u=tentative;break
            step*=.5
        else:
            raise ValueError('Unilateral soil contact Newton line search did not converge')
    raise ValueError('Unilateral soil contact failed to converge within iteration budget')


def solve_physical_candidate(study:M3PhysicalStudy,all_candidates:list[dict],candidate:dict):
    frame=study.frame
    graph,face_xy,levels,wall_segments,wall_node_ids=_augment_wall_graph(study,all_candidates,candidate)
    graph.nodes=np.asarray(graph.nodes)
    face_geom=_face_geometry(frame,face_xy)
    nd=len(graph.nodes)*6
    Kbase=np.zeros((nd,nd));base=[];struts=[]
    for m in graph.members:
        i,j=m['a'],m['b'];dofs=np.r_[np.arange(6*i,6*i+6),np.arange(6*j,6*j+6)]
        Kg,T,kl,L=element_matrix(m['kind'],graph.nodes[i],graph.nodes[j],m['section'])
        item={**m,'dofs':dofs,'Kg':Kg,'T':T,'kl':kl,'length':L}
        (struts if m['active_with_struts'] else base).append(item)
        if not m['active_with_struts']:
            _add(Kbase,Kg,dofs)
    free=np.setdiff1d(np.arange(nd),np.array(sorted(graph.fixed),dtype=int))
    previous=np.zeros(nd);ref_by_member={};stages=[];max_ind_diff=0.
    physical_stages={s.stage_id:s for s in study.boundary.stages}
    for stage in frame.stages:
        physical=physical_stages[stage.id]
        K=Kbase.copy();offset=np.zeros(nd)
        members=struts if stage.struts_installed else []
        for member in members:
            dofs=member['dofs'];_add(K,member['Kg'],dofs)
            if member['id'] not in ref_by_member:
                ref_by_member[member['id']]=previous[dofs].copy()
            offset[dofs]+=member['Kg']@ref_by_member[member['id']]
        load,pressure_refs,load_audit=_load_wall_pressures(frame,physical,graph,face_geom,wall_segments)
        rhs=load+offset
        springs=_contact_springs(physical,graph,face_geom,wall_node_ids,levels)
        displacement,solve_info=solve_contact_newton(K,rhs,free,springs,warmstart=previous)
        soil_force=solve_info['contactInternalForces']
        residual=K@displacement+soil_force-rhs
        free_residual=float(np.linalg.norm(residual[free]))
        reactions=residual.copy();reactions[free]=0.
        # Force balance with unilateral soil action treated as an external boundary reaction.
        global_force=np.zeros(3);global_moment=np.zeros(3)
        for i,p in enumerate(graph.nodes):
            applied=load[6*i:6*i+3]+reactions[6*i:6*i+3]-soil_force[6*i:6*i+3]
            couple=load[6*i+3:6*i+6]+reactions[6*i+3:6*i+6]-soil_force[6*i+3:6*i+6]
            global_force+=applied;global_moment+=np.cross(p,applied)+couple
        limit=max(1e-3,1e-6*float(np.linalg.norm(load)))
        if np.linalg.norm(global_force)>limit or np.linalg.norm(global_moment)>limit:
            raise ValueError('Physical model force or moment balance failed')
        activeK=K+solve_info['contactTangent']
        effective_rhs=rhs+solve_info['contactTangent']@displacement-soil_force
        ref_solution=np.linalg.solve(activeK[np.ix_(free,free)],effective_rhs[free])
        cross_error=float(np.linalg.norm(ref_solution-displacement[free])/max(1.,float(np.linalg.norm(displacement[free]))))
        max_ind_diff=max(max_ind_diff,cross_error)
        if cross_error>1e-7:
            raise ValueError('Independent dense algebra solver disagrees with sparse solution')
        member_rows=[];brace_node_forces=[]
        for member in base+members:
            dofs=member['dofs'];local=member['kl']@(member['T']@(displacement[dofs]-ref_by_member.get(member['id'],np.zeros(12))))
            member_rows.append({'memberId':member['id'],'group':member['group'],'lengthM':round(member['length'],6),
                                'axialKn':round(max(abs(local[0]),abs(local[6])),6),
                                'shearYKn':round(max(abs(local[1]),abs(local[7])),6),
                                'shearZKn':round(max(abs(local[2]),abs(local[8])),6),
                                'momentYKnM':round(max(abs(local[4]),abs(local[10])),6),
                                'momentZKnM':round(max(abs(local[5]),abs(local[11])),6),
                                'torsionKnM':round(max(abs(local[3]),abs(local[9])),6)})
            if member['group']=='strut':
                global_forces=member['T'].T@local
                for end,node in enumerate((member['a'],member['b'])):
                    v=global_forces[6*end:6*end+6]
                    brace_node_forces.append({'stageId':stage.id,'memberId':member['id'],
                        'nodeId':node,'pointM':[float(x) for x in graph.nodes[node]],
                        'forceKn':[round(float(x),7) for x in v[:3]],
                        'momentKnM':[round(float(x),7) for x in v[3:]],
                        'actionOnNodeForceKn':[round(float(-x),7) for x in v[:3]],
                        'actionOnNodeMomentKnM':[round(float(-x),7) for x in v[3:]],
                        'signBasis':'element_end_resisting_force_global_xyz; action_on_node_is_negative',
                        'status':'computed_model_joint_action_only',
                        'capacityVerified':False})
        disp=[hypot(displacement[6*n],displacement[6*n+1])*1000 for n in graph.wale_nodes]
        maxbrace=max((row['axialKn'] for row in member_rows if row['group']=='strut'),default=0.)
        stages.append({'stageId':stage.id,'maximumWallHorizontalDisplacementMm':round(max(disp,default=0),6),
            'maxBraceAxialKn':round(maxbrace,6),'soilContactActiveCount':solve_info['activeContacts'],
            'soilContactIterations':solve_info['iterations'], 'soilContactForceKn':round(float(np.linalg.norm(soil_force)),7),
            'freeDofResidualKn':round(free_residual,9),
            'globalForceBalanceKn':round(float(np.linalg.norm(global_force)),9),
            'globalMomentBalanceKnM':round(float(np.linalg.norm(global_moment)),9),
            'solverCrosscheckRelativeError':round(cross_error,12),
            'pressureSources':pressure_refs,'loadIntegrationAudit':load_audit,
            'soilSources':sorted({x.source_sha256 for x in physical.contacts}),
            'memberResults':member_rows,'braceNodeActions':brace_node_forces,
            'nodeDisplacements':[{'nodeId':n,'xM':float(graph.nodes[n][0]),'yM':float(graph.nodes[n][1]),
                'zM':float(graph.nodes[n][2]),'uxMm':round(float(displacement[6*n])*1000,8),
                'uyMm':round(float(displacement[6*n+1])*1000,8),
                'uzMm':round(float(displacement[6*n+2])*1000,8)} for n in sorted(graph.wale_nodes)],
            'status':'nonlinear_contact_reference_solved_only'})
        previous=displacement.copy()
        if not stage.struts_installed:
            ref_by_member.clear()
    out={'candidateId':candidate['candidateId'],'status':'nonlinear_contact_reference_solved_only',
        'graph':{'nodes':len(graph.nodes),'members':len(graph.members),
            'wallMembers':sum(m['group']=='wall' for m in graph.members),
            'braceMembers':sum(m['group']=='strut' for m in graph.members),
            'verticalLevelsM':levels,'fixedToeModel':'ideal_fixed_toe_reference'},
        'structuralGraph':{'coordinateSystem':'local_xyz_m',
            'nodes':[{'id':i,'x':float(p[0]),'y':float(p[1]),'z':float(p[2]),
                       'tags':sorted(graph.node_tags[i])} for i,p in enumerate(graph.nodes)],
            'members':[{'id':m['id'],'startNode':m['a'],'endNode':m['b'],
                        'kind':m['kind'],'group':m['group'],
                        'activation':'struts_installed' if m['active_with_struts'] else 'all_stages'} for m in graph.members]},
        'stages':stages,'totalBraceLengthM':candidate['totalLengthM'],
        'estimatedMemberMassKg':round(sum(m['length']*m['section'].mass_kg_per_m for m in base+struts),5),
        'numericalCrosscheck':{'methodA':'sparse_or_numpy_assembled_equilibrium',
           'methodB':'dense_numpy_independent_linear_algebra_on_active_tangent',
           'maxRelativeDifference':round(max_ind_diff,12),
           'independentSoilConstitutiveCalibration':False},
        'unresolvedEngineeringChecks':sorted(set(candidate['requiredEngineeringChecks']+[
            'SOIL_PARAMETER_AND_PRESSURE_METHOD_UNQUALIFIED','IDEAL_TOE_SUPPORT_UNQUALIFIED',
            'SOIL_CONSTITUTIVE_HYSTERESIS_AND_CONSTRUCTION_HISTORY_NOT_MODELED',
            'GROUNDWATER_AND_GLOBAL_STABILITY_NOT_CHECKED',
            'M7_NODE_CAPACITY_ANCHORAGE_AND_DETAILING_NOT_CHECKED',
            'INDEPENDENT_ENGINEER_REVIEW_REQUIRED'])),
        'engineeringQualified':False,'formalConstructionIssue':False}
    return out


def physical_candidates(study:M3PhysicalStudy,optimizer:bool):
    inp=study.frame;base=plan_topology_candidates(inp.topology)
    if not base.get('candidates'):
        return []
    candidates=list(base['candidates'])
    if optimizer:
        from copy import deepcopy
        for factor in (.85,1.15,1.35):
            if len(candidates)>=inp.max_evaluations:break
            proposal=inp.topology.model_copy(update={'target_spacing_m':min(35.,max(inp.topology.min_support_spacing_m,inp.topology.target_spacing_m*factor))})
            plan=plan_topology_candidates(proposal)
            if not plan.get('candidates'):continue
            c=deepcopy(plan['candidates'][0]);c['candidateId']='M6-P'+str(len(candidates)+1)
            c['strategy']='immutable_geometry_spacing_trial_'+str(factor)
            sig=lambda value:sorted((round(m['start']['x'],5),round(m['start']['y'],5),round(m['end']['x'],5),round(m['end']['y'],5)) for m in value['members'])
            if not any(sig(c)==sig(p) for p in candidates):candidates.append(c)
    return candidates[:inp.max_evaluations]


def objective_metrics(row,frame):
    disp=max(stage['maximumWallHorizontalDisplacementMm'] for stage in row['stages'])
    brace=max(stage['maxBraceAxialKn'] for stage in row['stages'])
    row['objectives']={'maxDisplacementMm':disp,'maxBraceAxialKn':brace,
                       'totalBraceLengthM':row['totalBraceLengthM']}
    controls=frame.numeric_controls
    checks={}
    if controls is not None:
        for field,value in [('maximum_wall_displacement_mm',disp),('maximum_brace_axial_kn',brace),
                            ('maximum_member_mass_kg',row['estimatedMemberMassKg'])]:
            limit=getattr(controls,field)
            if limit is not None:
                checks[field]={'demand':value,'limit':limit,'status':'numeric_screen_pass' if value<=limit else 'numeric_screen_fail'}
    row['numericConstraintChecks']=checks
    row['numericalFeasibleOnly']=all(c['status']=='numeric_screen_pass' for c in checks.values()) if checks else None
    return row


def finalize_physical_results(study:M3PhysicalStudy,results:list[dict],optimizer:bool,elapsed:float,stop_reason:str,source_audit:dict):
    solved=[r for r in results if r['status']=='nonlinear_contact_reference_solved_only']
    for candidate in solved:
        obj=candidate['objectives'];a=tuple(obj[k] for k in ('maxDisplacementMm','maxBraceAxialKn','totalBraceLengthM'))
        candidate['paretoNonDominated']=not any(candidate is not b and
            _dominated(a,tuple(b['objectives'][k] for k in ('maxDisplacementMm','maxBraceAxialKn','totalBraceLengthM'))) for b in solved)
    return {'schemaVersion':'pitguard.m3-physical-study-results/3.89.2',
        'inputSha256':source_audit['studySha256'],'sourceArchiveSha256':source_audit['archiveSha256'],
        'evidenceAudit':source_audit,'status':'nonlinear_contact_reference_only' if solved else 'all_candidates_blocked',
        'optimizerMode':'durable_agent_candidate_search' if optimizer else 'independent_a_b_c_reference',
        'evaluatedCount':len(results),'budget':{'maxEvaluations':study.frame.max_evaluations,
            'maxRuntimeSeconds':study.frame.max_runtime_seconds,'elapsedSeconds':round(elapsed,4),
            'stopReason':stop_reason,'inFlightSolverNotForceCancelled':True},
        'candidates':results,'paretoCandidateIds':[x['candidateId'] for x in solved if x['paretoNonDominated']],
        'modelScope':'distributed_depth_linear_spatial_frame_and_unilateral_winkler_reference',
        'automaticDesignAdoptionAllowed':False,'engineeringQualified':False,
        'formalConstructionIssue':False}


def evaluate_m3_physical(study:M3PhysicalStudy,source_audit:dict,*,optimizer=False):
    cands=physical_candidates(study,optimizer)
    if not cands:return {'status':'unsupported_geometry','engineeringQualified':False,'formalConstructionIssue':False}
    begun=perf_counter();results=[];reason='candidate_budget_reached'
    for c in cands:
        if perf_counter()-begun>=study.frame.max_runtime_seconds:
            reason='time_budget_before_next_candidate';break
        try:results.append(objective_metrics(solve_physical_candidate(study,cands,c),study.frame))
        except (ValueError,np.linalg.LinAlgError) as exc:
            results.append({'candidateId':c['candidateId'],'status':'blocked','reason':str(exc),
                            'engineeringQualified':False,'formalConstructionIssue':False})
    return finalize_physical_results(study,results,optimizer,perf_counter()-begun,reason,source_audit)
