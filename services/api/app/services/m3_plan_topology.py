"""M3 3.89.0 preliminary 2D bracing layouts for orthogonal excavations.

Geometry-only: do NOT infer a valid 3D load path, member N-M-V, corner
stiffness, concrete reinforcement, construction sequence or final design.
Every proposed transverse member has two wall-seat endpoints. Concrete/steel
member and wale capacities remain unverified and cannot be auto approved.
"""
from __future__ import annotations
from hashlib import sha256
from math import ceil, hypot, isfinite
import json
from typing import Literal
from pydantic import Field, model_validator
from shapely.geometry import LineString, Polygon
from shapely.validation import explain_validity
from app.engineering.single_braced_pile.assurance.model import Strict

class PlanPoint(Strict):
    x:float=Field(ge=-100000,le=100000)
    y:float=Field(ge=-100000,le=100000)

class PlanOutline(Strict):
    id:str=Field(min_length=1,max_length=80)
    vertices:tuple[PlanPoint,...]=Field(min_length=4,max_length=128)

class M3TopologyInput(Strict):
    schema_version:Literal['pitguard.m3-plan-input/1'] = Field(default='pitguard.m3-plan-input/1',alias='schemaVersion')
    project_id:str=Field(min_length=2,max_length=160)
    support_level:int=Field(ge=0,le=30)
    excavation:PlanOutline
    forbidden_zones:tuple[PlanOutline,...]=Field(default=(),max_length=24)
    min_support_spacing_m:float=Field(default=4.0,ge=4.0,le=20)
    target_spacing_m:float=Field(default=9.0,ge=4.0,le=35)
    end_inset_m:float=Field(default=4.0,gt=0,le=50)
    corner_brace_offset_m:float=Field(default=3.0,ge=1,le=30)
    minimum_member_length_m:float=Field(default=3.0,ge=1,le=30)
    obstacle_clearance_m:float=Field(default=0.6,ge=0,le=10)
    @model_validator(mode='after')
    def consistent_spacing(self):
        if self.target_spacing_m < self.min_support_spacing_m:
            raise ValueError('Target spacing may not be below declared minimum')
        return self


def _polygon(vertices):
    coords=[(p.x,p.y) for p in vertices]
    if coords[0]==coords[-1]:coords.pop()
    poly=Polygon(coords)
    if not poly.is_valid or poly.is_empty or poly.area<1e-3:
        raise ValueError('Invalid excavation polygon: '+explain_validity(poly))
    return poly,coords


def _orthogonal(coords):
    return all(abs(a[0]-b[0])<=1e-7 or abs(a[1]-b[1])<=1e-7
               for a,b in zip(coords, coords[1:]+coords[:1]))


def _concave_vertices(coords):
    signed=sum(x*y2-x2*y for ((x,y),(x2,y2)) in zip(coords,coords[1:]+coords[:1]))
    ccw=signed>0
    out=[]
    for i,b in enumerate(coords):
        a,c=coords[i-1],coords[(i+1)%len(coords)]
        cross=(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])
        if (ccw and cross<-1e-7) or (not ccw and cross>1e-7):
            out.append({'vertexIndex':i,'x':b[0],'y':b[1]})
    return out


def _line_valid(poly,obs,line,min_length,clearance):
    if line.length<min_length-1e-7 or not poly.buffer(1e-7).covers(line):return False
    if poly.boundary.distance(line.boundary)>1e-5:return False
    return not any(line.buffer(clearance,cap_style=2).intersects(o) for o in obs)


def _segments(g):
    if g.is_empty:return []
    if g.geom_type=='LineString':return [g]
    return [part for part in getattr(g,'geoms',[]) if part.geom_type=='LineString']


def _end_corner_lines(poly,coords,axis,offset,obs,clearance,minlen):
    """Diagonal braces at convex *end* corners only; concave corner flagged."""
    xs=[c[0] for c in coords];ys=[c[1] for c in coords]
    low,high=(min(xs),max(xs)) if axis=='x' else (min(ys),max(ys))
    diagonals=[]
    for i,v in enumerate(coords):
        main=v[0] if axis=='x' else v[1]
        if abs(main-low)>1e-6 and abs(main-high)>1e-6:continue
        prev=coords[i-1];nxt=coords[(i+1)%len(coords)]
        length1=hypot(prev[0]-v[0],prev[1]-v[1])
        length2=hypot(nxt[0]-v[0],nxt[1]-v[1])
        if min(length1,length2)<offset+1e-6:continue
        p=(v[0]+(prev[0]-v[0])*offset/length1,v[1]+(prev[1]-v[1])*offset/length1)
        q=(v[0]+(nxt[0]-v[0])*offset/length2,v[1]+(nxt[1]-v[1])*offset/length2)
        line=LineString([p,q])
        if _line_valid(poly,obs,line,minlen,clearance):diagonals.append(line)
    return diagonals


def _station_values(start,end,inset,target,minimum):
    available=end-start-2*inset
    if available<0:return []
    intervals=max(1,int(available/target))
    if intervals==1:return [(start+end)/2]
    n=min(intervals+1,200)
    delta=available/(n-1)
    if delta<minimum-1e-8:
        n=max(1,int(available//minimum)+1)
        if n==1:return [(start+end)/2]
        delta=available/(n-1)
    return [start+inset+i*delta for i in range(n)]


def _chords(poly,axis,stations,obs,clearance,min_length):
    minx,miny,maxx,maxy=poly.bounds
    lines=[]
    for station in stations:
        line=(LineString([(station,miny-1),(station,maxy+1)]) if axis=='x'
              else LineString([(minx-1,station),(maxx+1,station)]))
        for part in _segments(line.intersection(poly)):
            if _line_valid(poly,obs,part,min_length,clearance):lines.append(part)
    return lines


def _face_index(coords,point):
    for i,(a,b) in enumerate(zip(coords,coords[1:]+coords[:1])):
        if LineString([a,b]).distance(__import__('shapely').geometry.Point(point))<=1e-6:
            return i
    return None


def _unconnected_crossings(members):
    lines=[LineString([(m['start']['x'],m['start']['y']),
                       (m['end']['x'],m['end']['y'])]) for m in members]
    out=[]
    for i,a in enumerate(lines):
        for j in range(i+1,len(lines)):
            cross=a.intersection(lines[j])
            if cross.geom_type=='Point' and not cross.is_empty and a.boundary.distance(cross)>1e-5 and lines[j].boundary.distance(cross)>1e-5:
                out.append({'memberIds':[members[i]['id'],members[j]['id']],
                     'x':round(cross.x,5),'y':round(cross.y,5),
                     'engineeringConnectionRequired':True})
    return out


def plan_topology_candidates(spec:M3TopologyInput|dict)->dict:
    request=M3TopologyInput.model_validate(spec)
    polygon,coords=_polygon(request.excavation.vertices)
    obs=[]
    for z in request.forbidden_zones:
        geom,_=_polygon(z.vertices)
        obs.append(geom)
    if not _orthogonal(coords):
        return {'schemaVersion':'pitguard.m3-preliminary-plan/3.89.0',
            'status':'unsupported_geometry','reason':'Initial candidate generator supports orthogonal polygons only',
            'candidates':[],'engineeringQualified':False,'formalConstructionIssue':False}
    minx,miny,maxx,maxy=polygon.bounds
    axis='x' if maxx-minx>=maxy-miny else 'y'
    start,end=(minx,maxx) if axis=='x' else (miny,maxy)
    concave=_concave_vertices(coords)
    fingerprint=sha256(json.dumps(request.model_dump(mode='json',by_alias=True),sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()
    candidates=[]
    for i,spacing in enumerate((request.target_spacing_m,max(request.min_support_spacing_m,
                              request.target_spacing_m*.75),request.target_spacing_m*1.3)):
        stations=_station_values(start,end,request.end_inset_m,spacing,request.min_support_spacing_m)
        straight=_chords(polygon,axis,stations,obs,request.obstacle_clearance_m,
                         request.minimum_member_length_m)
        angles=_end_corner_lines(polygon,coords,axis,request.corner_brace_offset_m,obs,
                                 request.obstacle_clearance_m,request.minimum_member_length_m)
        branch=[]
        if concave:
            # Concave/irregular orthogonal sites require regional coverage.
            # Add a second direction of chords; their crossings are explicitly
            # UNCONNECTED pending column/beam node design and 3D calculation.
            second='y' if axis=='x' else 'x'
            low,high=(miny,maxy) if second=='y' else (minx,maxx)
            branch_stations=_station_values(low,high,request.end_inset_m,spacing,request.min_support_spacing_m)
            branch=_chords(polygon,second,branch_stations,obs,
                           request.obstacle_clearance_m,request.minimum_member_length_m)
        members=[]
        seen=set()
        for kind,items in [('transverse',straight),('branch_transverse',branch),('end_corner_diagonal',angles)]:
            for part in items:
                ends=list(part.coords); a,b=ends[0],ends[-1]
                key=tuple(sorted(((round(a[0],5),round(a[1],5)),(round(b[0],5),round(b[1],5)))))
                if key in seen:continue
                seen.add(key)
                members.append({'id':f'M3-L{request.support_level}-{i+1}-{len(members)+1:03}',
                   'type':kind,'start':{'x':round(a[0],5),'y':round(a[1],5)},
                   'end':{'x':round(b[0],5),'y':round(b[1],5)},
                   'lengthM':round(part.length,5),'endConnections':'wall_seats_to_be_designed',
                   'startFaceIndex':_face_index(coords,a),'endFaceIndex':_face_index(coords,b),
                   'memberCapacityQualified':False})
        crossings=_unconnected_crossings(members)
        total=round(sum(m['lengthM'] for m in members),5)
        # All members have geometric wall seats; none has been verified for
        # actual wale loads, 3D load paths, joint stiffness or construction.
        blockers=['3D_FRAME_ANALYSIS_REQUIRED','WALE_AND_CROWN_BEAM_FORCES_REQUIRED',
                  'END_SEAT_AND_JOINT_DESIGN_REQUIRED','STAGE_CONSTRUCTION_CHECK_REQUIRED',
                  'DISPLACEMENT_AND_GLOBAL_STABILITY_REQUIRED']
        if concave:blockers.append('CONCAVE_CORNER_TRANSFER_DETAIL_REQUIRED')
        if crossings:blockers.append('INTERSECTING_MEMBERS_NEED_EXPLICIT_JOINT_AND_COLUMN_MODEL')
        if not members:blockers.append('NO_GEOMETRICALLY_VALID_CANDIDATE')
        candidates.append({'candidateId':f'M3-C{i+1}',
          'strategy':f'cross_bays_spacing_{round(spacing,2)}m_with_end_diagonals',
          'supportLevel':request.support_level,'layoutStatus':'geometry_precheck_only' if members else 'no_candidates',
          'members':members,'memberCount':len(members),'totalLengthM':total,
          'unconnectedCrossings':crossings,'unconnectedCrossingCount':len(crossings),
          'concaveCornersUnresolved':concave,'requiredEngineeringChecks':blockers,
          'formalConstructionIssue':False,'engineeringQualified':False})
    # Deterministic ranking for geometry workload only, not structural quality.
    candidates.sort(key=lambda c:(not bool(c['members']),c['totalLengthM'],c['candidateId']))
    return {'schemaVersion':'pitguard.m3-preliminary-plan/3.89.0',
      'status':'geometry_candidates_only' if any(c['members'] for c in candidates) else 'no_feasible_geometry',
      'sourceInputSha256':fingerprint,'projectId':request.project_id,
      'coordinateSystem':'local_plan_xy_m','preferredAxis':axis,
      'areaM2':round(polygon.area,5),'concaveCorners':concave,
      'candidates':candidates,'automaticDesignAdoptionAllowed':False,
      'threeDimensionalEngineeringQualified':False,'formalConstructionIssue':False,
      'limitations':['Geometry preview is not a structural load-path verification',
       'Member endpoints touch polygon boundary but wale/seat bearing and node actions have not been calculated',
       'Concave corners need separate structural detailing and numerical verification',
       'No realistic floor/strut installation interaction, columns, deflection, strength or construction-phase analysis']}
