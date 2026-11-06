"""Readiness, provenance and scope gates. Numerical solver cannot repair evidence."""
from __future__ import annotations
from pydantic import ValidationError
from .contracts import Section,required_sources

def preflight(payload:dict)->dict:
    try:
        model=Section.model_validate(payload)
    except ValidationError as exc:
        missing=[];errors=[]
        for item in exc.errors(include_url=False):
            item_path='.'.join(map(str,item['loc']))
            errors.append({'field':item_path,'code':item['type'],'message':item['msg']})
            if item['type']=='missing':missing.append(item_path)
        return {'schemaVersion':'pitguard.one-strut-preflight/1','schemaValid':False,
                'readyForCompute':False,'missing':missing,'issues':errors,'sourceIssues':[]}
    source_by_path={s.path:s for s in model.sources}
    issues=[]
    for key in required_sources(model):
        source=source_by_path.get(key)
        if source is None:
            issues.append({'field':key,'code':'SOURCE_MISSING','message':'需要项目勘察/设计来源，不能依据默认值代替'})
        elif source.state!='confirmed':
            issues.append({'field':key,'code':'SOURCE_NOT_APPROVED','message':'候选值须经工程师确认后才能用于正式工程资料'})
        elif source.origin=='example':
            issues.append({'field':key,'code':'SYNTHETIC_VALUE','message':'示例不能作为真实工程来源'})
    if model.purpose=='verification_example':
        issues.append({'field':'purpose','code':'VERIFICATION_ONLY','message':'合成验证例仅用于程序数值验证'})
    if model.project_region.startswith('浙江'):
        issues.append({'field':'project_region','code':'LOCAL_STANDARD_REVIEW',
                       'message':'浙江地方基坑规范的增补要求仍需要专项核对'})
    if model.pile.toe_boundary!='free':
        issues.append({'field':'pile.toe_boundary','code':'TOE_SUPPORT_REQUIRES_EVIDENCE',
                       'message':'自由墙趾以外的边界需有实际岩层约束证据'})
    if any(l.c_effective_kpa>0 for l in model.layers):
        issues.append({'field':'layers','code':'TENSION_CRACK_WATER_CASE_REQUIRED',
                       'message':'有黏聚力的土层须另外审查张裂缝充水不利工况'})
    return {'schemaVersion':'pitguard.one-strut-preflight/1','schemaValid':True,
            'readyForCompute':True,'sourceIssues':issues,
            'engineeringEvidenceComplete':not any(x['code'] in ('SOURCE_MISSING','SOURCE_NOT_APPROVED','SYNTHETIC_VALUE') for x in issues),
            'missing':[],'issues':issues,
            'scope':'单排非咬合圆桩/一道无预力弹性支撑/水平地面/有效应力分算/不考虑渗流',
            'method':'JGJ120-2012 3.4,4.1.3~4.1.10; GB55003-2021 7.1~7.2'}
