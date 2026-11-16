"""JGJ120-2012 4.1 method review and staged 3-3 elastic-foundation beam replay.

Runs without FRWS files. The 2020 PDF's printed extrema are DIAGNOSTIC ONLY.
No calibration or least-squares fitting to reported extrema is performed.
"""
from __future__ import annotations
import math
from typing import Any

from .luoyu_three_three import produce_luoyu_research_curves
from .fd_beam_reference import verify_cantilever_uniform_load
from ..normative.reference_review import luoyu_three_three
from ..analysis import canonical_hash
from ..pressure import width as normative_pile_reaction_width

METHOD = 'pitguard.luoyu-3-3-normative-beam/3.89.8'


def soil_m_review(facts: dict) -> list[dict]:
    """JGJ120 4.1.6; denominator uses the >=10mm displacement floor."""
    output=[]
    for index, l in enumerate(facts['soilLayersOriginalGround']):
        phi, c = float(l['phiDeg']), float(l['cKpa'])
        numerator = 0.2*phi**2 - phi + c
        if numerator <= 0:
            raise ValueError('m-method formula requires independent soil testing for this layer')
        calculated = numerator / 10.0
        printed = float(l['mMPaPerM2'])
        matching = math.isclose(calculated, printed, rel_tol=0.015, abs_tol=0.008)
        output.append({'layerIndex':index,'layerName':l['name'], 'cKpa':c, 'phiDeg':phi,
                       'mFromReportMNPerM4':printed,'mFromFormulaMNPerM4':round(calculated,6),
                       'referenceDisplacementMm':10.0,
                       'equation':'JGJ120-2012 4.1.6: (0.2 phi^2 - phi + c) / max(v_b,10mm)',
                       'matchesReportPrintedRounding':matching,
                       'sourceOfStrength':'2020 project design report table; original geotechnical tests unavailable',
                       'constitutiveMethodApproved':False})
    return output


def _validate_pdf(report:dict|None,reference:dict):
    if report is None:
        return {'status':'historical_excerpt_only','sourcePdfSha256':None,'originalReportBytesVerified':False}
    if report.get('engineeringQualified') is not False or report.get('formalConstructionIssue') is not False:
        raise ValueError('Original PDF parser cannot claim engineering approval')
    if report.get('originalDepthSampleArraysPresent') is not False:
        raise ValueError('Native arrays must be evaluated by a separate native evidence protocol')
    p=report.get('standardCase',{}).get('parameters',{})
    f=reference['facts']
    for key in ('pileDiameterM','pileSpacingM','pileLengthM','siteSupportDepthM',
                'siteFinalExcavationDepthM','strutStiffnessMNPerM2'):
        if key not in p or not math.isclose(float(p[key]),float(f[key]),abs_tol=1e-9):
            raise ValueError('PDF printed standard 3-3 does not match frozen case: '+key)
    if not math.isclose(report.get('shortPileCase',{}).get('pileLengthM',float('nan')),5.1):
        raise ValueError('Missing separate 5.1m short pile case')
    r={x['stageId']:x for x in report['standardCase']['stages']}
    if set(r)!={'S1','S2','S3','S4','S5'} or len(report['standardCase']['stages'])!=5:
        raise ValueError('Five printed stages required')
    for ref in reference['stages']:
        sample=r[ref['id']]['ranges']
        for a,b in (('displacementRangeMm','displacementMm'),('momentRangeKnM','momentKnM'),('shearRangeKn','shearKn')):
            if len(sample.get(b,[]))!=2 or any(not math.isclose(float(u),float(v),abs_tol=1e-7)
                                                  for u,v in zip(ref[a],sample[b])):
                raise ValueError('PDF stage extrema changed: '+ref['id']+'/'+b)
    return {'status':'provided_pdf_bytes_and_extrema_verified','sourcePdfSha256':report['sourcePdfSha256'],
            'originalReportBytesVerified':True,'shortPileSeparate':True}


def run_luoyu_normative_beam_recheck(*, mesh_max_m:float=.2,
                                       toe_boundary:str='free',
                                       elastic_modulus_mpa:float=30000.,
                                       inertia_factor:float=1.,
                                       original_report:dict|None=None,
                                       include_stage_curves:bool=False) -> dict:
    """No external finite-element package and no FRWS original arrays required.

    Invariant: all report comparisons are labelled non-equivalent-condition;
    no optimizer tunes unknown inputs to the printed maxima.
    """
    if not 0.10 <= mesh_max_m <= 0.4 or toe_boundary not in {'free','pinned','fixed'}:
        raise ValueError('Mesh outside [0.10,0.4]m or undeclared toe boundary')
    if not 1000 <= elastic_modulus_mpa <= 100000 or not 0 < inertia_factor <= 1:
        raise ValueError('Candidate E/EI must remain physically bounded')
    historical=luoyu_three_three()
    fact=historical['facts']
    source=_validate_pdf(original_report,historical)
    d,s=fact['pileDiameterM'],fact['pileSpacingM']
    formula=.9*(1.5*d+.5) if d<=1 else .9*(d+1)
    b0=min(s,formula)
    beam=produce_luoyu_research_curves(
        mesh_max_m=mesh_max_m,toe_boundary=toe_boundary,
        elastic_modulus_mpa=elastic_modulus_mpa,inertia_factor=inertia_factor)
    if not math.isclose(b0,normative_pile_reaction_width(d,s),abs_tol=1e-10):
        raise RuntimeError('Independent soil-width formula and shared norm kernel disagree')
    if not math.isclose(b0,beam['parameters']['insideSoilReactionProjectionWidthM'],abs_tol=1e-10):
        raise RuntimeError('Actual staged solver has mismatched soil reaction width')
    m_checks=soil_m_review(fact)
    rows=[]
    for reference,stage in zip(historical['stages'],beam['stages']):
        if reference['id']!=stage['id']:
            raise RuntimeError('Report and calculator stage identities differ')
        vals={'uMm':reference['displacementRangeMm'],
              'momentKnM':reference['momentRangeKnM'],'shearKn':reference['shearRangeKn']}
        current={'uMm':stage.get('peakDisplacementMm'),
                 'momentKnM':stage.get('peakMomentKnM'),
                 'shearKn':stage.get('peakShearKn')}
        measures={}
        for key,range_ in vals.items():
            peak=max(abs(float(x)) for x in range_)
            observed=current[key] if stage['status']=='success' else None
            measures[key]={'reportPrintedAbsPeak':peak,'candidateAbsPeak':observed,
                'signedAbsolutePeakDifference':(observed-peak if observed is not None else None),
                'status':'diagnostic_different_conditions_no_error_metric'}
        item={'stageId':stage['id'],'event':stage['id'],
              'originalDesignPrintedPhysicalPage':33 if stage['id'] in {'S1','S2'} else 34,
              'calculationStatus':stage['status'],'measurements':measures,
              'supportReactions':stage.get('strutReactions',[]),
              'passiveCheck':{'code':'JGJ120-2012/4.1.4-2',
                  'mobilizedInsideReactionKnPerPile':stage.get('mobilizedInsideReactionKnPerPile'),
                  'passiveReferenceKnPerPile':stage.get('passiveReferenceKnPerPile'),
                  'passiveResultantLimitSatisfied':stage.get('passiveResultantLimitSatisfied')},
              'globalBalance':stage.get('equilibrium'),
              'originalNativeDepthSamplesAvailable':False,
              'fullCurveRmse':None,'engineeringQualified':False}
        if include_stage_curves:
            item['conditionalCalculationCurve']=stage.get('curve')
        rows.append(item)
    governing={key: max(((x['stageId'],x['measurements'][key]['candidateAbsPeak']) for x in rows
                           if x['measurements'][key]['candidateAbsPeak'] is not None),
                           key=lambda t:t[1], default=None)
               for key in ('uMm','momentKnM','shearKn')}
    case={'meshMaxM':mesh_max_m,'toeBoundary':'unverified_'+toe_boundary,
          'elasticModulusCandidateMpa':elastic_modulus_mpa,
          'flexuralRigidityReductionCandidate':inertia_factor,
          'pileDiameterM':d,'pileSpacingM':s,'pileLengthM':fact['pileLengthM'],
          'pileTopBelowOriginalGroundM':fact['pileTopOriginalGroundDepthM'],
          'reinforcementDesignNotInferredFromPeak':True}
    warnings=list(beam['modelLimitations'])+[
        'JGJ120 4.1.6 m is matched arithmetically to the report; the original tested strength and design choice are unverified',
        'JGJ120 4.1.7 soil reaction width is capped by pile spacing, correcting a previous 1.35m implementation',
        'The reported 32.3 MN/m2 strut and 22.7 MN/m2 crown values lack confirmed FRWS-to-single-pile mapping',
        'Report uses inclined 45-degree support; 4.1.10 simple opposite strut formula does not directly qualify that geometry',
        'Passive resultant and stability, uplift, seismic, water and reinforced concrete joint checks remain separate',
        'Different-condition printed extrema differences are not performance error, not acceptance and never used for fitting']
    payload={'schemaVersion':METHOD,'project':historical['project'],'section':'3-3',
      'analysisType':'JGJ120-2012 4.1 elastic foundation beam, staged conditional check',
      'independentOfFRWSNativeData':True,'source':source,
      'sourceReferencePages':{'geometryParameters':32,'stagesAndStrut':33,'stageExtrema':34,'reinforcement':36},
      'assumptions':case,'normativeMethodChecks':{
          'soilReactionWidth':{'code':'JGJ120-2012/4.1.7','unboundedB0M':formula,
             'usedB0M':b0,'limitedByPileSpacing':formula>s,
             'previousIncorrectWidthM':min(3*d,1.5*s),'verifiedFormulaAndCodeAgree':True},
          'soilM':m_checks,'passiveResultant':{'code':'JGJ120-2012/4.1.4-2',
              'allReportedStagesSatisfy': all(s.get('passiveResultantLimitSatisfied') is True
                                                  for s in beam['stages']),
              'numericalCheckOnly':True},'initialSupportReference':{'code':'JGJ120-2012/4.1.8',
             'stageInstallReferencesFromCommittedPriorStage':True,
             'numericalSupportInstallForceIsZero': bool(beam['solverStatus']=='success' and
                   any(x['id']=='temporary' and abs(x['reactionKnPerPile']) < 1e-6
                       for x in beam['stages'][1]['strutReactions'])),
             'preloadSpecified':False},
          'subgradeProfile':{'code':'JGJ120-2012/4.1.5','ks':'m(layer)*(z-excavationDepth)','units':'kN/m3'},
          'strutStiffness':{'code':'JGJ120-2012/4.1.10','reportKPerAreaMNPerM2':fact['strutStiffnessMNPerM2'],
             'convertedSinglePileCandidateKnPerM':beam['parameters']['supportStiffnessMappingMultiplierUnconfirmed']*fact['strutStiffnessMNPerM2']*1000*s,
             'methodApplicabilityApproved':False}},
      'stages':rows,'governing':governing,'solverStatus':beam['solverStatus'],
      'conditionalModelDiscreteHash':beam['discreteHash'],
      'printedCurvePointwiseError':None,
      'numericalBenchmark':verify_cantilever_uniform_load(segments=120),
      'missingEvidence':warnings,
      'originalPileShortCaseIsSeparate':True,
      'reportExtremaUsedForCalibration':False,
      'engineeringQualified':False,'formalConstructionIssue':False,
      'recommendedNextActions':[
          'Engineer reviews physical water and soil total/effective basis and the associated pressure distribution',
          'Confirm real toe restraint, crown and inclined brace deformation compatibility before comparison',
          'Run toe/mesh/saturated unit-weight sensitivity with sourced assumptions and retain the complete curves',
          'Perform independent member, reinforced concrete, hydraulic and overall stability checks before adoption'
      ]}
    payload['auditHash']=canonical_hash(payload)
    return payload
