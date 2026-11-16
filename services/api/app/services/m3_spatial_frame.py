"""Auditable 6-DOF-per-node spatial beam/truss benchmark for M3 layouts.

Engineering scope: small-displacement linear elastic staged frame with explicit
face-resultant loads, wall/wale/crown/pile beams and optional column supports.
This is a conservative *model qualification gate*: soil nonlinearity, lateral
unilateral contact, preload, foundation settlement, member capacities and node
bearing/anchorage must be independently supplied/checked. No design adoption.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from math import ceil, hypot, isfinite, sqrt
from time import perf_counter
import json
from typing import Literal
import numpy as np
from pydantic import Field, model_validator
from app.engineering.single_braced_pile.assurance.model import Strict
from .m3_plan_topology import M3TopologyInput, plan_topology_candidates, _polygon

HEX = r'^[0-9a-f]{64}$'


class Section3D(Strict):
    E_kn_m2: float = Field(gt=1e3, le=1e12)
    G_kn_m2: float = Field(gt=1e3, le=1e12)
    area_m2: float = Field(gt=1e-6, le=1e3)
    Iy_m4: float = Field(gt=1e-10, le=1e4)
    Iz_m4: float = Field(gt=1e-10, le=1e4)
    J_m4: float = Field(gt=1e-10, le=1e4)
    mass_kg_per_m: float = Field(ge=0,le=1e6)


class FaceTraction(Strict):
    face_index: int = Field(ge=0,le=127)
    net_pressure_kpa: float = Field(ge=-5000,le=5000)
    source_sha256: str = Field(pattern=HEX)
    method_reference: str = Field(min_length=8,max_length=300)


class Stage3D(Strict):
    id: str = Field(min_length=2,max_length=80)
    excavation_depth_m: float = Field(gt=0,le=100)
    struts_installed: bool
    lateral_soil_spring_kn_per_m2: float = Field(default=0.0,ge=0,le=1e9)
    face_tractions: tuple[FaceTraction,...] = Field(min_length=4,max_length=128)
    @model_validator(mode='after')
    def unique_faces(self):
        ids=[x.face_index for x in self.face_tractions]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate face traction')
        return self


class ColumnSite(Strict):
    id: str = Field(min_length=2,max_length=80)
    x: float
    y: float
    # A column on an unconnected brace intersection must explicitly connect
    # the intersecting struts. Geometric crossing alone is NOT a joint.
    connect_intersecting_struts: bool = True


class NumericControls(Strict):
    maximum_wall_displacement_mm: float | None = Field(default=None,gt=0,le=10000)
    maximum_brace_axial_kn: float | None = Field(default=None,gt=0,le=1e9)
    maximum_member_mass_kg: float | None = Field(default=None,gt=0,le=1e10)


class M3FrameInput(Strict):
    schema_version: Literal['pitguard.m3-spatial-frame/1'] = Field(default='pitguard.m3-spatial-frame/1',alias='schemaVersion')
    topology: M3TopologyInput
    crown_z_m: float = Field(default=0.0,ge=-100,le=100)
    strut_z_m: float = Field(default=-3.0,ge=-100,le=100)
    toe_z_m: float = Field(default=-13.0,ge=-200,le=100)
    max_wall_mesh_m: float = Field(default=8.0,ge=1,le=20)
    wall_pile_spacing_m: float = Field(gt=0.3,le=8)
    wall_section: Section3D
    wale_section: Section3D
    crown_section: Section3D
    strut_section: Section3D
    column_section: Section3D | None = None
    column_sites: tuple[ColumnSite,...] = Field(default=(),max_length=30)
    toe_condition: Literal['ideal_fixed_toe_reference']
    stages: tuple[Stage3D,...] = Field(min_length=1,max_length=12)
    evidence_level: Literal['synthetic_benchmark','project_supplied_unreviewed']
    numeric_controls: NumericControls | None = None
    max_evaluations: int = Field(default=6,ge=1,le=12)
    max_runtime_seconds: float = Field(default=60.0,ge=1,le=180)
    @model_validator(mode='after')
    def stages_valid(self):
        if not (self.crown_z_m>self.strut_z_m>self.toe_z_m):
            raise ValueError('Expected crown_z > strut_z > toe_z')
        if self.column_sites and self.column_section is None:
            raise ValueError('Explicit columns require verified section properties')
        if len({x.id for x in self.stages})!=len(self.stages):
            raise ValueError('Stage IDs must be unique')
        n=len(self.topology.excavation.vertices)
        if tuple(x.face_index for x in sorted(self.stages[0].face_tractions,key=lambda x:x.face_index))!=tuple(range(n)):
            raise ValueError('Every original wall face needs explicit stage pressure (including zeros)')
        for stage in self.stages:
            if set(x.face_index for x in stage.face_tractions)!=set(range(n)):
                raise ValueError('All stages must cover each original wall face')
            if stage.excavation_depth_m>self.crown_z_m-self.toe_z_m:
                raise ValueError('Excavation exceeds declared pile toe')
        return self


def _local_axes(p,q):
    a=np.asarray(p,dtype=float); b=np.asarray(q,dtype=float); delta=b-a
    length=float(np.linalg.norm(delta))
    if length<1e-7:raise ValueError('Zero-length spatial member')
    ex=delta/length
    ref=np.array([0.,0.,1.]) if abs(ex[2])<.85 else np.array([0.,1.,0.])
    ey=np.cross(ref,ex);ey/=np.linalg.norm(ey)
    ez=np.cross(ex,ey)
    return length, np.vstack([ex,ey,ez])


def beam_local_k(section:Section3D,length:float)->np.ndarray:
    """Euler-Bernoulli 12-DOF local stiffness; units kN, m, radians."""
    e,g,a,iy,iz,j=(section.E_kn_m2,section.G_kn_m2,section.area_m2,
                    section.Iy_m4,section.Iz_m4,section.J_m4)
    k=np.zeros((12,12))
    def block(ids,mat):
        for i,ii in enumerate(ids):
            for n,jj in enumerate(ids):k[ii,jj]+=mat[i][n]
    s=e*a/length
    block([0,6],[[s,-s],[-s,s]])
    tor=g*j/length
    block([3,9],[[tor,-tor],[-tor,tor]])
    # v / rotation-about-z flexure (Iz)
    a12=12*e*iz/length**3;b6=6*e*iz/length**2;c4=4*e*iz/length;d2=2*e*iz/length
    block([1,5,7,11],[[a12,b6,-a12,b6],[b6,c4,-b6,d2],[-a12,-b6,a12,-b6],[b6,d2,-b6,c4]])
    # w / rotation-about-y flexure (Iy); signs are opposite in local beam basis
    a12=12*e*iy/length**3;b6=6*e*iy/length**2;c4=4*e*iy/length;d2=2*e*iy/length
    block([2,4,8,10],[[a12,-b6,-a12,-b6],[-b6,c4,b6,d2],[-a12,b6,a12,b6],[-b6,d2,b6,c4]])
    return k


def element_matrix(kind:str,p,q,section:Section3D):
    length,R=_local_axes(p,q)
    T=np.zeros((12,12))
    for offset in (0,3,6,9):T[offset:offset+3,offset:offset+3]=R
    if kind=='truss':
        k=np.zeros((12,12));s=section.E_kn_m2*section.area_m2/length
        for a,b,sign in ((0,0,1),(6,6,1),(0,6,-1),(6,0,-1)):k[a,b]=sign*s
    else:k=beam_local_k(section,length)
    return T.T@k@T,T,k,length


class SpatialGraph:
    def __init__(self):
        self.nodes=[];self.node_tags=defaultdict(set);self.node_lookup={};self.members=[];self.fixed=set()
        self.wale_nodes=set();self.wall_boundary=[];self.column_ids=set()
    def node(self,x,y,z,tag):
        key=(round(float(x),7),round(float(y),7),round(float(z),7))
        if key not in self.node_lookup:
            self.node_lookup[key]=len(self.nodes);self.nodes.append(np.asarray(key,dtype=float))
        k=self.node_lookup[key];self.node_tags[k].add(tag)
        return k
    def member(self,id,kind,a,b,section,group,active_with_struts=False):
        if a==b:raise ValueError('Degenerate graph member '+id)
        self.members.append({'id':id,'kind':kind,'a':a,'b':b,'section':section,
                             'group':group,'active_with_struts':active_with_struts})


def _point_segment_t(p,a,b):
    ap=np.asarray(p)-np.asarray(a);ab=np.asarray(b)-np.asarray(a)
    denom=float(ab@ab)
    if denom<1e-10:return None
    t=float(ap@ab/denom)
    if -1e-7<=t<=1+1e-7 and np.linalg.norm(ap-t*ab)<1e-5:
        return min(1.,max(0.,t))
    return None


def _perimeter_common_mesh(spec:M3FrameInput,candidates:list[dict]):
    coords=[(p.x,p.y) for p in spec.topology.excavation.vertices]
    group=[]
    for i,(a,b) in enumerate(zip(coords,coords[1:]+coords[:1])):
        ts={0.,1.}
        length=hypot(b[0]-a[0],b[1]-a[1])
        ts.update(k/max(1,ceil(length/spec.max_wall_mesh_m)) for k in range(max(1,ceil(length/spec.max_wall_mesh_m))+1))
        for cand in candidates:
            for m in cand['members']:
                for end in ('start','end'):
                    p=(m[end]['x'],m[end]['y']);t=_point_segment_t(p,a,b)
                    if t is not None:ts.add(round(t,9))
        group.append([(round(a[0]+t*(b[0]-a[0]),7),round(a[1]+t*(b[1]-a[1]),7)) for t in sorted(ts)])
    return group


def build_structural_graph(spec:M3FrameInput,candidates:list[dict],candidate:dict):
    groups=_perimeter_common_mesh(spec,candidates)
    graph=SpatialGraph();face_nodes=[];positions=[]
    tributary_by_position=defaultdict(float)
    for points in groups:
        for i in range(len(points)-1):
            L=hypot(points[i+1][0]-points[i][0],points[i+1][1]-points[i][1])
            tributary_by_position[tuple(points[i])]+=L/2
            tributary_by_position[tuple(points[i+1])]+=L/2
    # Common wall + wale + crown grid across A/B/C eliminates mesh-ranking artifacts.
    generated_seats=set()
    for group in groups:
        row=[]
        for x,y in group:
            crown=graph.node(x,y,spec.crown_z_m,'crown')
            seat=graph.node(x,y,spec.strut_z_m,'wale')
            toe=graph.node(x,y,spec.toe_z_m,'toe')
            graph.wale_nodes.add(seat);graph.fixed.update(range(toe*6,toe*6+6))
            if seat not in generated_seats:
                generated_seats.add(seat)
                # Tributary-width equivalent pile stiffness prevents a finer
                # boundary mesh from inventing more piles and added stiffness.
                ratio=tributary_by_position[(x,y)]/spec.wall_pile_spacing_m
                wall=spec.wall_section.model_copy(update={
                    k:getattr(spec.wall_section,k)*ratio for k in (
                    'area_m2','Iy_m4','Iz_m4','J_m4','mass_kg_per_m')})
                graph.member('pile-upper:'+str(seat),'beam',crown,seat,wall,'wall')
                graph.member('pile-lower:'+str(seat),'beam',seat,toe,wall,'wall')
            row.append(seat)
        face_nodes.append(row)
        positions.append(group)
    for face_i,row in enumerate(face_nodes):
        for j in range(len(row)-1):
            a,b=row[j],row[j+1]
            pa=np.asarray(graph.nodes[a]);pb=np.asarray(graph.nodes[b]);
            ua=graph.node(pa[0],pa[1],spec.crown_z_m,'crown')
            ub=graph.node(pb[0],pb[1],spec.crown_z_m,'crown')
            graph.member(f'wale:{face_i}:{j}','beam',a,b,spec.wale_section,'wale')
            graph.member(f'crown:{face_i}:{j}','beam',ua,ub,spec.crown_section,'crown')
    graph.wall_boundary=face_nodes
    # Explicit crossing sites supply a column AND declare a connection.
    sites=[(c.id,c.x,c.y) for c in spec.column_sites if c.connect_intersecting_struts]
    site_members=defaultdict(list)
    for m in candidate['members']:
        a=(m['start']['x'],m['start']['y']);b=(m['end']['x'],m['end']['y'])
        cuts=[(0.,a,'start'),(1.,b,'end')]
        for cid,x,y in sites:
            t=_point_segment_t((x,y),a,b)
            if t is not None and 1e-6<t<1-1e-6:
                cuts.append((t,(x,y),cid));site_members[cid].append(m['id'])
        cuts.sort()
        for j in range(len(cuts)-1):
            p=cuts[j][1];q=cuts[j+1][1]
            n1=graph.node(*p,spec.strut_z_m,'brace_node')
            n2=graph.node(*q,spec.strut_z_m,'brace_node')
            if n1 not in graph.wale_nodes and cuts[j][2] in ('start','end'):
                raise ValueError('Brace seat is not a wall-grid node')
            if n2 not in graph.wale_nodes and cuts[j+1][2] in ('start','end'):
                raise ValueError('Brace seat is not a wall-grid node')
            graph.member(f'{m["id"]}:{j}','truss',n1,n2,spec.strut_section,'strut',True)
    for cid,x,y in sites:
        if len(set(site_members.get(cid,[])))<2:
            raise ValueError('Declared column '+cid+' must connect at least two crossing braces')
        foot=graph.node(x,y,spec.toe_z_m,'column_foundation')
        node=graph.node(x,y,spec.strut_z_m,'column_connection')
        graph.fixed.update(range(foot*6,foot*6+6))
        graph.member('column:'+cid,'beam',node,foot,spec.column_section,'column',True)
        graph.column_ids.add(cid)
    if len(graph.nodes)>320 or len(graph.members)>650:
        raise ValueError('Spatial model exceeds bounded benchmark size (320 nodes / 650 members)')
    return graph,positions


def _add(K,element,dofs):
    K[np.ix_(dofs,dofs)]+=element


def _solver(K,F,free):
    if len(free)>1500:raise ValueError('Solve exceeds supported free-DOF budget')
    A=K[np.ix_(free,free)];b=F[free]
    if not np.all(np.isfinite(A)) or not np.all(np.isfinite(b)):
        raise ValueError('Nonfinite load or stiffness')
    # Optional accelerated sparse solve, with no mandatory third-party scipy dependency.
    try:
        from scipy.sparse import csc_matrix
        from scipy.sparse.linalg import spsolve
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            u=spsolve(csc_matrix(A),b)
    except ImportError:
        u=np.linalg.solve(A,b)
    except Exception as exc:
        raise ValueError('Spatial structural stiffness is singular or ill-conditioned: '+str(exc)[:140]) from exc
    if not np.all(np.isfinite(u)):
        raise ValueError('Structural system is unstable or solve returned nonfinite displacement')
    residual=float(np.linalg.norm(A@u-b))
    if residual>max(1e-5,1e-7*float(np.linalg.norm(b))):
        raise ValueError('Structural equilibrium residual exceeds numerical tolerance')
    return u,residual


def solve_spatial_candidate(spec:M3FrameInput,all_candidates:list[dict],candidate:dict)->dict:
    """One physical study. Each stage replaces absolute externally supplied loads;
    installed braces are stress free in the immediately preceding deformed state.
    """
    g,face_coords=build_structural_graph(spec,all_candidates,candidate)
    nd=6*len(g.nodes);Kfixed=np.zeros((nd,nd))
    g.nodes=np.asarray(g.nodes)
    base_members=[];strut_members=[]
    for m in g.members:
        i,j=m['a'],m['b'];dofs=np.r_[np.arange(6*i,6*i+6),np.arange(6*j,6*j+6)]
        Kg,T,kl,length=element_matrix(m['kind'],g.nodes[i],g.nodes[j],m['section'])
        record={**m,'dofs':dofs,'Kg':Kg,'T':T,'kl':kl,'length':length}
        (strut_members if m['active_with_struts'] else base_members).append(record)
        if not m['active_with_struts']:_add(Kfixed,Kg,dofs)
    fixed=np.array(sorted(g.fixed),dtype=int)
    free=np.setdiff1d(np.arange(nd),fixed)
    prev=np.zeros(nd);ref_by_member={};stage_rows=[]
    for stage in spec.stages:
        K=Kfixed.copy();loads=np.zeros(nd);rhs_offset=np.zeros(nd)
        active=strut_members if stage.struts_installed else []
        for m in active:
            d=m['dofs']
            _add(K,m['Kg'],d)
            if m['id'] not in ref_by_member:
                ref_by_member[m['id']]=prev[d].copy()
            rhs_offset[d]+=m['Kg']@ref_by_member[m['id']]
        # Explicit face traction mapped as a line resultant, *not* integrated
        # nonlinear soil pressure. For a uniform face resultant p*h, the nodal
        # tributary is exact for its total force and first moment on a face.
        traction_by_face={p.face_index:p for p in stage.face_tractions}
        poly,coords=_polygon(spec.topology.excavation.vertices)
        orientation=1. if poly.exterior.is_ccw else -1.
        loaded_faces=[]
        for face,points in enumerate(face_coords):
            p=traction_by_face[face].net_pressure_kpa;h=stage.excavation_depth_m
            a,b=coords[face],coords[(face+1)%len(coords)]
            L=hypot(b[0]-a[0],b[1]-a[1]);nx=orientation*-(b[1]-a[1])/L;ny=orientation*(b[0]-a[0])/L
            row=g.wall_boundary[face]
            for t in range(len(row)-1):
                length=hypot(points[t+1][0]-points[t][0],points[t+1][1]-points[t][1])
                for node in (row[t],row[t+1]):
                    loads[6*node]+=p*h*length*nx/2
                    loads[6*node+1]+=p*h*length*ny/2
            loaded_faces.append({'faceIndex':face,'pressureKpa':p,'loadedHeightM':h,
                                 'resultantKn':round(p*h*L,7),'sourceSha256':traction_by_face[face].source_sha256})
        if stage.lateral_soil_spring_kn_per_m2:
            # One tributary spring per wall station, assembled once regardless
            # of duplicated corner nodes shared by neighbouring segments.
            tributary=defaultdict(float)
            for face,points in enumerate(face_coords):
                row=g.wall_boundary[face]
                for i in range(len(row)-1):
                    L=hypot(points[i+1][0]-points[i][0],points[i+1][1]-points[i][1])
                    tributary[row[i]]+=L/2;tributary[row[i+1]]+=L/2
            for node,length in tributary.items():
                spring=stage.lateral_soil_spring_kn_per_m2*length
                K[6*node,6*node]+=spring
                K[6*node+1,6*node+1]+=spring
        rhs=loads+rhs_offset
        u=np.zeros(nd);u[free],rel=_solver(K,rhs,free)
        all_internal=K@u-rhs_offset
        reactions=all_internal-loads
        free_reaction=float(np.linalg.norm(reactions[free]))
        if free_reaction>max(1e-4,1e-7*float(np.linalg.norm(loads))):
            raise ValueError('Stage residual exceeds validated tolerance')
        # Global force and moment equilibrium, including restrained nodal couples.
        global_force=np.zeros(3);global_moment=np.zeros(3)
        for n,p in enumerate(g.nodes):
            force=loads[6*n:6*n+3]+reactions[6*n:6*n+3]
            couple=loads[6*n+3:6*n+6]+reactions[6*n+3:6*n+6]
            global_force+=force;global_moment+=np.cross(p,force)+couple
        if max(np.linalg.norm(global_force),np.linalg.norm(global_moment))>max(1e-3,1e-6*np.linalg.norm(loads)):
            raise ValueError('Global force/moment equilibrium did not close')
        member_rows=[]
        for m in base_members+active:
            d=m['dofs'];du=u[d]-ref_by_member.get(m['id'],np.zeros(12));local=m['kl']@(m['T']@du)
            member_rows.append({'memberId':m['id'],'group':m['group'],'lengthM':round(m['length'],6),
                'axialKn':round(max(abs(local[0]),abs(local[6])),6),
                'shearYKn':round(max(abs(local[1]),abs(local[7])),6),
                'shearZKn':round(max(abs(local[2]),abs(local[8])),6),
                'momentYKnM':round(max(abs(local[4]),abs(local[10])),6),
                'momentZKnM':round(max(abs(local[5]),abs(local[11])),6),
                'torsionKnM':round(max(abs(local[3]),abs(local[9])),6)})
        horizontal=[hypot(u[6*n],u[6*n+1])*1000 for n in g.wale_nodes]
        maxbrace=max((x['axialKn'] for x in member_rows if x['group']=='strut'),default=0.)
        stage_rows.append({'stageId':stage.id,'strutsInstalled':stage.struts_installed,
            'maximumWallHorizontalDisplacementMm':round(max(horizontal,default=0.),6),
            'maxBraceAxialKn':round(maxbrace,6),'freeDofResidualKn':round(free_reaction,9),
            'globalForceBalanceKn':round(float(np.linalg.norm(global_force)),9),
            'globalMomentBalanceKnM':round(float(np.linalg.norm(global_moment)),9),
            'loadedFaceResultants':loaded_faces,'memberResults':member_rows,
            'nodeDisplacements':[{ 'nodeId':int(n),'xM':float(g.nodes[n][0]),'yM':float(g.nodes[n][1]),
                 'zM':float(g.nodes[n][2]),'uxMm':round(float(u[6*n])*1000,7),
                 'uyMm':round(float(u[6*n+1])*1000,7),'uzMm':round(float(u[6*n+2])*1000,7)}
                  for n in sorted(g.wale_nodes)],
            'status':'linear_reference_solved_only'})
        prev=u
        # A removed support loses its installation reference; reinstallation
        # starts stress-free at that later stage's current configuration.
        if not stage.struts_installed:ref_by_member.clear()
    return {'candidateId':candidate['candidateId'],'status':'linear_reference_solved_only',
       'graph':{'nodes':len(g.nodes),'members':len(g.members),'wallMembers':sum(x['group']=='wall' for x in g.members),
                'waleMembers':sum(x['group']=='wale' for x in g.members),
                'crownMembers':sum(x['group']=='crown' for x in g.members),
                'braceMembers':sum(x['group']=='strut' for x in g.members),
                'columnMembers':sum(x['group']=='column' for x in g.members),
                'fixedToeModel':'ideal_fixed_toe_reference','connectedColumnIds':sorted(g.column_ids),
                'wallReferencePileSpacingM':spec.wall_pile_spacing_m},
       'stages':stage_rows,'totalBraceLengthM':candidate['totalLengthM'],
       'estimatedMemberMassKg':round(sum(m['length']*m['section'].mass_kg_per_m for m in base_members+strut_members),5),
       'structuralGraph':{'coordinateSystem':'local_xyz_m','nodes':[{'id':i,'x':float(v[0]),'y':float(v[1]),'z':float(v[2]),
           'tags':sorted(g.node_tags[i])} for i,v in enumerate(g.nodes)],
           'members':[{'id':m['id'],'startNode':m['a'],'endNode':m['b'],
             'group':m['group'],'kind':m['kind'],
             'activation':'struts_installed' if m['active_with_struts'] else 'all_stages'} for m in g.members]},
       'unresolvedCrossings':candidate['unconnectedCrossings'] if not g.column_ids else [x for x in candidate['unconnectedCrossings'] if not any(hypot(x['x']-c.x,x['y']-c.y)<1e-4 for c in spec.column_sites if c.connect_intersecting_struts)],
       'normativeCompliance':'not_assessed','engineeringQualified':False,'formalConstructionIssue':False}


def _dominated(a,b):
    return all(a[i]>=b[i]-1e-8 for i in range(3)) and any(a[i]>b[i]+1e-8 for i in range(3))


def evaluate_m3_spatial(spec:M3FrameInput|dict,*,optimizer:bool=False)->dict:
    inp=M3FrameInput.model_validate(spec)
    base=plan_topology_candidates(inp.topology)
    if not base['candidates']:
        return {'status':'unsupported_geometry','engineeringQualified':False,'formalConstructionIssue':False,
                'requiredEngineeringChecks':['GEOMETRY_UNSUPPORTED_OR_UNRESOLVED']}
    candidates=list(base['candidates'])
    # M6 bounded parameter proposals; same original loads/wall mesh, no mutation
    # of geotechnical parameters or limit values. Changes are trial-only.
    if optimizer:
        from copy import deepcopy
        for factor in (.85,1.15,1.35):
            if len(candidates)>=inp.max_evaluations:break
            proposal=inp.topology.model_copy(update={'target_spacing_m':min(35.,max(inp.topology.min_support_spacing_m,inp.topology.target_spacing_m*factor))})
            trial=plan_topology_candidates(proposal)
            if trial.get('candidates'):
                c=deepcopy(trial['candidates'][0]);c['candidateId']='M6-T'+str(len(candidates)+1)
                c['strategy']='m6_bounded_spacing_trial_'+str(factor)
                if not any(sorted((round(x['start']['x'],5),round(x['start']['y'],5),round(x['end']['x'],5),round(x['end']['y'],5)) for x in c['members'])==
                           sorted((round(x['start']['x'],5),round(x['start']['y'],5),round(x['end']['x'],5),round(x['end']['y'],5)) for x in p['members']) for p in candidates):
                    candidates.append(c)
    candidates=candidates[:inp.max_evaluations]
    results=[]
    started=perf_counter()
    stop_reason='candidate_budget_reached'
    for cand in candidates:
        if perf_counter()-started>=inp.max_runtime_seconds:
            stop_reason='time_budget_reached_before_next_candidate'
            break
        try:
            r=solve_spatial_candidate(inp,candidates,cand)
            maxdisp=max(x['maximumWallHorizontalDisplacementMm'] for x in r['stages'])
            maxbrace=max(x['maxBraceAxialKn'] for x in r['stages'])
            r['objectives']={'maxDisplacementMm':maxdisp,'maxBraceAxialKn':maxbrace,
                              'totalBraceLengthM':r['totalBraceLengthM']}
            control_map={'maximum_wall_displacement_mm':maxdisp,
                         'maximum_brace_axial_kn':maxbrace,
                         'maximum_member_mass_kg':r['estimatedMemberMassKg']}
            numeric_checks={}
            if inp.numeric_controls is not None:
                for field,value in control_map.items():
                    limit=getattr(inp.numeric_controls,field)
                    if limit is not None:numeric_checks[field]={'demand':value,'limit':limit,
                          'status':'numeric_screen_pass' if value<=limit else 'numeric_screen_fail'}
            r['numericConstraintChecks']=numeric_checks
            r['numericalFeasibleOnly']=all(x['status']=='numeric_screen_pass' for x in numeric_checks.values()) if numeric_checks else None
            r['unresolvedEngineeringChecks']=sorted(set(cand['requiredEngineeringChecks']+[
                'TOE_BOUNDARY_METHOD_UNVALIDATED','PRESSURE_PROFILE_AND_SOIL_NONLINEARITY_NOT_COUPLED',
                'STRUT_PRELOAD_NOT_MODELED','BEAM_COLUMN_STABILITY_NOT_CHECKED',
                'CAPACITY_AND_ANCHORAGE_NOT_CHECKED','INDEPENDENT_ENGINEER_REVIEW_REQUIRED']))
            results.append(r)
        except (ValueError,np.linalg.LinAlgError) as exc:
            results.append({'candidateId':cand['candidateId'],'status':'blocked',
                'reason':str(exc),'engineeringQualified':False,'formalConstructionIssue':False})
    solved=[x for x in results if x['status']=='linear_reference_solved_only']
    for x in solved:
        a=x['objectives'];av=(a['maxDisplacementMm'],a['maxBraceAxialKn'],a['totalBraceLengthM'])
        x['paretoNonDominated']=not any(x is not y and _dominated(av,tuple(y['objectives'][q] for q in
            ('maxDisplacementMm','maxBraceAxialKn','totalBraceLengthM'))) for y in solved)
    fingerprint=sha256(json.dumps(inp.model_dump(mode='json',by_alias=True),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'schemaVersion':'pitguard.m3-spatial-study/3.89.1',
        'inputSha256':fingerprint,'geometryInputSha256':base.get('sourceInputSha256'),
        'status':'linear_reference_only' if solved else 'all_candidates_blocked',
        'optimizerMode':'m6_bounded_immutable_trials' if optimizer else 'a_b_c_independent_comparison',
        'evaluatedCount':len(results),'budget':{'maxEvaluations':inp.max_evaluations,
             'maxRuntimeSeconds':inp.max_runtime_seconds,'elapsedSeconds':round(perf_counter()-started,4),
             'stopReason':stop_reason,'inFlightSolverNotForceCancelled':True},
        'candidates':results,
        'paretoCandidateIds':[c['candidateId'] for c in solved if c['paretoNonDominated']],
        'modelScope':'linear_elastic_spatial_beam_truss_with_fixed_toe_and_explicit_stage_wall_resultants',
        'engineeringQualified':False,'automaticDesignAdoptionAllowed':False,
        'formalConstructionIssue':False,
        'limitations':['Reference ideal fixed toe and linear wall soil springs require independent site qualification',
           'Stage face pressure net resultants are user supplied; no automatic excavation pore pressure/earth pressure verification',
           'Euler-Bernoulli beams and axial trusses do not evaluate member stability or nonlinear node capacity',
           'Prestress and asymmetric contact/soil plasticity are not modeled',
           'Original signed review and real field numerical evidence are not contained in this result']}
