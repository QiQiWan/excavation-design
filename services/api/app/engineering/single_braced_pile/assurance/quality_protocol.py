"""P1-P4 evidence-based engineering acceptance gates.

The gate evaluates actual saved model, independent numerical review and the
reviewer's *preliminary* check; it never infers a formal construction approval.
In particular an arbitrary client-supplied boolean cannot change a normative
result into PASS.
"""
from __future__ import annotations
from .workflow import UnifiedAuditInput,audit_unified


PHASES={
 'P1': {'name':'水力模型与渗透稳定', 'standard':'JGJ 120-2012 4.2.6, 7.1, Appendix C',
        'conditions':('现场边界和承压水头来源','渗流路径与土层破坏判据','网格与边界敏感性','独立数值或手算负例','工程师审查')},
 'P2': {'name':'圆桩轴弯剪、裂缝及构造', 'standard':'JGJ 120-2012 4.3.2, Appendix B; GB/T 50010-2010 (2024)',
        'conditions':('材料与钢筋笼原始值','同步N-M-V与使用组合','正式适用裂缝与轴剪方法','手算/独立算例和超限反例','工程师审查')},
 'P3': {'name':'螺屿村3-3同条件对照', 'standard':'项目原计算书及经确认的计算方法',
        'conditions':('桩端与冠梁换算','对侧墙刚度与安装参考','坡台压力及土水方法','五阶段全部原始数值通道','独立工程师确认')},
 'P4': {'name':'同源成果审查与签发', 'standard':'经适用规范、完整工程计算与设计校审流程',
        'conditions':('冻结输入与封存结果','P1-P3适用检查闭合','结构构件和施工图深化','校审签字与发行登记','全套成果哈希校验')},
}


def acceptance_plan(input:UnifiedAuditInput,result:dict,review:dict|None=None,*,independent:bool=False,phase_reviews:tuple[dict,...]=())->dict:
    dossier=audit_unified(input,result,independent=independent)
    section=result['sectionId'];hydro=dossier.get('hydraulic');circular=dossier['circular']
    original=dossier.get('realProject')
    stages=[]
    phase_specs=[('P1',hydro,'hydraulic'),('P2',circular,'circular'),
                 ('P3',original,'luoyu')]
    for phase,report,kind in phase_specs:
        if phase=='P3' and section!='3-3':
            state='not_applicable';inputs=[]
        elif report is None:
            state='not_submitted';inputs=list(PHASES[phase]['conditions'])
        else:
            state=report['assessmentStatus']
            inputs=[]
            if phase=='P1':
                missing=report['sourceEvidence']['items']
                inputs=[x['key'] for x in missing if x['status']!='matched']
                for key,item in report.get('failureModeChecks',{}).items():
                    if not item.get('siteSpecificCriterionVerified'):
                        inputs.append('water_failure_mode:'+key)
                if report['independentNumericalCheck']['status']!='passed':
                    inputs.append('independent_hydraulic_solver_or_manual_check')
                if report.get('farFieldBoundarySensitivity',{}).get('status')=='not_requested':
                    inputs.append('far_field_boundary_sensitivity')
                if report.get('darcyPhysicsInvariants',{}).get('status')!='passed':
                    inputs.append('independent_Darcy_physics_invariance_tests')
            elif phase=='P2':
                inputs=[x['key'] for x in report['evidence']['items'] if x['status']!='matched']
                if report['strength']['axialShearUnverifiedCount']:
                    inputs.append('qualified_axial_shear_method')
                if report['serviceability']['status'] not in ('pass',):
                    inputs.append('qualified_circular_crack_method_and_sustained_actions')
                for code,item in report['construction'].items():
                    if item['status']!='pass': inputs.append('construction:'+code)
            else:
                inputs=[x['key'] for x in report['conditionEvidence'] if x['status']!='reviewed_claim_pending_model_binding']
                if report['nativeCurveComparison'].get('comparisonStatus')!='all_channels_compared_source_peer_review_pending':
                    inputs.append('original_five_stage_all_channel_numeric_export')
                inputs+=['verified_original_model_method','independent_source_bytes_review']
        stages.append({'phase':phase,'name':PHASES[phase]['name'],'basis':PHASES[phase]['standard'],
           'assessmentStatus':state,'releaseApproved':False,
           'requiredEvidenceAndVerification':PHASES[phase]['conditions'],
           'outstanding':sorted(set(inputs))})
    has_review=bool(review and review.get('resultHash')==result.get('resultHash') and
                    review.get('scope')=='preliminary_calculation_review_only' and
                    review.get('formalConstructionIssue') is False)
    p4={'phase':'P4','name':PHASES['P4']['name'],'basis':PHASES['P4']['standard'],
        'assessmentStatus':'working_copy_available' if has_review and result.get('analysisStatus')=='complete'
             else 'needs_calculation_and_preliminary_review',
        'releaseApproved':False,'requiredEvidenceAndVerification':PHASES['P4']['conditions'],
        'outstanding':['P1-P3所有适用专项规范验算与场地证据完成','完整支撑与圆桩结构设计及施工节点',
                       '独立工程校审与发行许可'],
        'preliminaryReviewHash':review.get('reviewHash') if has_review else None}
    stages.append(p4)
    phase_index={p['phase']:p for p in stages}
    accepted_reviews={}
    for record in phase_reviews:
        phase=record.get('phase')
        if phase not in PHASES or record.get('resultHash')!=result.get('resultHash') or record.get('snapshotHash')!=result.get('snapshotHash'):
            raise ValueError('Engineering phase reviews must belong to one frozen calculation')
        if phase in accepted_reviews:raise ValueError('Duplicate engineer review for one acceptance phase')
        if record.get('formalEngineeringApproval') is not False:
            raise ValueError('This preliminary process cannot accept a formal construction approval claim')
        accepted_reviews[phase]=record
    for phase,stage in phase_index.items():
        item=accepted_reviews.get(phase)
        checks={
          'originalInputSourcesDeclared':bool(item and item.get('declaredInputSourceReferences')),
          'methodRevisionDeclared':bool(item and item.get('methodRevision')),
          'independentCalculationDeclared':bool(item and item.get('declaredIndependentVerificationReference')),
          'negativeCaseDeclared':bool(item and item.get('declaredNegativeCaseReference')),
          'engineerReviewRecorded':item is not None,
        }
        stage['engineerReview']={'status':'recorded_preliminary_only' if item is not None else 'missing',
             'reviewHash':item['phaseReviewHash'] if item else None,
             'decision':item['decision'] if item else None,
             'evidenceDeclarations':checks,
             'declarationsAreNotVerifiedEvidence':True}
        for proof_key,present in checks.items():
            if not present and stage['assessmentStatus']!='not_applicable':
                stage['outstanding'].append('phase_proof:'+proof_key)
        if item is None and stage['assessmentStatus']!='not_applicable':
            stage['outstanding'].append('phase_engineer_review_record')
    blockers=[{'phase':s['phase'],'status':s['assessmentStatus'],'items':s['outstanding']}
              for s in stages if s['assessmentStatus'] not in ('not_applicable',)]
    return {'schemaVersion':'pitguard.engineering-P1-P4-gates/1',
       'projectId':result['projectId'],'sectionId':section,
       'snapshotHash':result['snapshotHash'],'resultHash':result['resultHash'],
       'reviewHash':review['reviewHash'] if has_review else None,
       'phaseReviewHashes':{k:v['phaseReviewHash'] for k,v in accepted_reviews.items()},
       'calculationState':result['analysisStatus'],'phases':stages,
       'blockingRegister':blockers,
       'formalConstructionIssue':False,'workCopyExportEligible':bool(has_review and result['analysisStatus']=='complete'),
       'assurance':dossier,
       'limitations':['已核验测试只说明对应数值问题和实现受限条件，不能证明原工程场地条件正确',
           '本工作流不产生施工图签发许可；缺失的规范方法与工程证据必须有独立设计复核']}
