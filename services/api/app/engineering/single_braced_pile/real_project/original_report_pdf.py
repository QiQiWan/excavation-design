"""V3.89.7: bounded, section-aware extraction from the actual Luoyu design PDF.

This module verifies source bytes and explicitly labeled *report excerpts*; it never
creates native depth samples, a geotechnical original, signed drawings, or
professional approval. Coordinates/page numbers are PDF physical 1-based pages.
The fixed parser targets the 2020 FRWS report layout; other revisions remain
unparsed and need a separately reviewed extraction template.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
from math import pi, isclose
import re

MAX_PDF_BYTES = 24_000_000
REPORT_SCHEMA = 'pitguard.luoyu-original-report/3.89.7'
RANGE_RE = re.compile(r'([-+]?\d+(?:\.\d+)?)\s*~\s*([-+]?\d+(?:\.\d+)?)')


def _find_num(text: str, pattern: str, name: str) -> float:
    matches = re.findall(pattern, text, re.S)
    if len(matches) != 1:
        raise ValueError('Report segment missing or ambiguous: ' + name)
    return float(matches[0])


def _range_before_caption(text: str, caption: str) -> dict:
    # Caption follows exactly one plot; isolate its preceding set of U/M/V extrema.
    pos = text.find(caption)
    if pos == -1 or text.count(caption) != 1:
        raise ValueError('Report stage caption missing or repeated: ' + caption)
    block = text[:pos]
    key_map = (('displacementMm', '位移(mm)'), ('momentKnM', '弯矩(kNm)'), ('shearKn', '剪力(kN)'))
    out = {}
    for key, label in key_map:
        # Word's PDF export includes axis labels between the heading and the range.
        hits = re.findall(re.escape(label) + r'\s*(' + RANGE_RE.pattern + r')', block)
        if not hits:
            raise ValueError('Stage lacks ' + key + ' before ' + caption)
        lo, hi = float(hits[-1][1]), float(hits[-1][2])
        if lo > hi:
            raise ValueError('Inverted report range: ' + key)
        out[key] = [lo, hi]
    return out


def extract_report_page_texts(pages: dict[int, str], *, source_hash: str,
                              physical_page_count: int) -> dict:
    """Parse physically distinct standard 3-3 and short-pile 3-3 report sections.

    The function is pure to allow no-PDF-dependency parser tests. A textual
    index or claimed hash alone is never evidence of PDF origin; only the
    bytes-based read_design_report_pdf entrypoint establishes that link.
    """
    if not all(page in pages and isinstance(pages[page], str) for page in (1, 21, 32, 33, 34, 35, 36, 37, 38, 39, 40, 42)):
        raise ValueError('The Luoyu report needs required physically numbered pages')
    if '螺屿村' not in pages[1] or 'GY060207' not in pages[1] or '基坑支护设计方案' not in pages[1]:
        raise ValueError('Document is not the recorded Luoyu design report')
    p32, p33, p34, p35, p36, p37, p38, p39, p40, p42 = (
        pages[p] for p in (32, 33, 34, 35, 36, 37, 38, 39, 40, 42))
    if p32.count('4.5 3-3') != 1 or p37.count('4.6 3-3') != 1:
        raise ValueError('Standard 3-3 and short-pile 3-3 headings must be separate')
    # Page 37 is mixed: text BEFORE 4.6 belongs to normal 3-3, later text
    # belongs to short-pile. Never allow short-pile values to enter normal case.
    p37_standard = p37.split('4.6 3-3', 1)[0]
    p37_short = p37.split('4.6 3-3', 1)[1]
    s32 = p32.split('4.5 3-3', 1)[1]
    standard = {
        'pileDiameterM': _find_num(s32, r'桩径\s*:\s*(\d+(?:\.\d+)?)\s*m\s*;', 'pile diameter'),
        'pileSpacingM': _find_num(s32, r'桩间距\s*:\s*(\d+(?:\.\d+)?)\s*m', 'pile spacing'),
        'pileLengthM': _find_num(s32, r'桩长\s*:\s*(\d+(?:\.\d+)?)\s*m', 'pile length'),
        'pileTopOriginalGroundDepthM': _find_num(s32, r'顶标高\s*:\s*-(\d+(?:\.\d+)?)\s*m', 'pile top depth'),
        'siteFinalExcavationDepthM': _find_num(s32, r'基坑设计深度\s*(\d+(?:\.\d+)?)\s*m', 'excavation depth'),
        'outsideWaterOriginalGroundDepthM': _find_num(s32, r'地下水位埋深\s*[:：]\s*(\d+(?:\.\d+)?)\s*m', 'water datum'),
        'siteSupportDepthM': _find_num(s32, r'1\s*\n\s*(1\.625)\s*\n\s*32\.3\s*\n\s*内支撑', 'support depth'),
        'strutStiffnessMNPerM2': _find_num(s32, r'1\s*\n\s*1\.625\s*\n\s*(32\.3)\s*\n\s*内支撑', 'strut stiffness'),
        'siteReplacementLevelM': _find_num(p33, r'换撑\s*\n\s*深度\s*\n.*?\n\s*1\s*\n\s*(\d+(?:\.\d+)?)\s*\n\s*固定', 'replacement depth'),
        'crownEquivalentStiffnessMNPerM2': _find_num(p33, r'冠梁等效支撑刚度\s*(\d+(?:\.\d+)?)\s*MN/m2', 'crown stiffness'),
    }
    surcharge = re.search(r'邻近荷载.*?长度\(m\)\s*\n\s*30\s*\n\s*0\s*\n\s*(4\.3)\s*\n\s*1\.3\s*\n\s*(12)\s*\n\s*(65)', s32, re.S)
    if surcharge is None:
        raise ValueError('Finite surcharge table is missing or differs from original reference')
    standard.update({'finiteAdjacentSurcharge.pressureKpa': 30.,
                     'finiteAdjacentSurcharge.nearDistanceM': float(surcharge[1]),
                     'finiteAdjacentSurcharge.widthM': float(surcharge[2]),
                     'finiteAdjacentSurcharge.lengthM': float(surcharge[3])})
    if not re.search(r'放坡\s*\n\s*坡高\(m\).*?\n\s*1\.3\s*\n\s*1\.3\s*\n\s*1\.7', s32, re.S):
        raise ValueError('Sloped-bench geometry is missing or ambiguous')
    standard.update({'terrain.slopeHeightM': 1.3, 'terrain.slopeWidthM': 1.3, 'terrain.benchWidthM': 1.7})
    soil_fragment = s32.split('土层参数', 1)[1].split('续表', 1)[0]
    soil_evidence = (
        (r'填土\s*\n\s*1\.1\s*\n\s*18\s*\n\s*10\s*\n\s*15\s*\n\s*分算', 1),
        (r'粘土\s*\n\s*0\.7\s*\n\s*18\s*\n\s*14\.85\s*\n\s*8\.39\s*\n\s*合算', 1),
        (r'淤泥\s*\n\s*5\.2\s*\n\s*16\.22\s*\n\s*5\.55\s*\n\s*6\.36\s*\n\s*合算', 1),
        (r'岩石\(强风化\)\s*\n\s*0\.6\s*\n\s*21\s*\n\s*30\s*\n\s*30\s*\n\s*合算', 1),
        (r'岩石\(中风化\)\s*\n\s*50\s*\n\s*22\s*\n\s*50\s*\n\s*30\s*\n\s*合算', 1),
    )
    soil_status = all(len(re.findall(pattern, soil_fragment)) == expected for pattern, expected in soil_evidence)
    if not soil_status:
        raise ValueError('3-3 geological layer table differs from recorded text; human reconciliation required')
    stage_specs = (
        ('S1', 33, '1.开挖到2.05m', p33),
        ('S2', 33, '2.在1.625m 处加撑', p33),
        ('S3', 34, '3.开挖到4.9m', p34),
        ('S4', 34, '4.在4.6m 处换撑', p34),
        ('S5', 34, '5.在1.625m 处拆撑', p34),
    )
    stages = []
    for stage_id, page, caption, text in stage_specs:
        stages.append({'stageId': stage_id, 'physicalPage': page,
                       'ranges': _range_before_caption(text, caption),
                       'sourceKind': 'printed_plot_extrema_not_depth_samples'})
    # Independent sections are distinctly scoped; shorter pile cannot silently
    # supersede standard 6.6 m report and its five-stage extrema.
    short_length = _find_num(p38, r'桩长\s*:\s*(\d+(?:\.\d+)?)\s*m', 'short-pile length')
    if '4.6 3-3' not in p37 or not isclose(short_length, 5.1, abs_tol=1e-8):
        raise ValueError('Short pile independent case identity not confirmed')
    short_moment = _find_num(p42, r'(?m)^弯矩标准值Mk\(kNm\)\s*(\d+(?:\.\d+)?)', 'short pile moment')
    report_moment = _find_num(p36, r'(?m)^弯矩标准值Mk\(kNm\)\s*(\d+(?:\.\d+)?)', 'standard moment')
    design_moment = _find_num(p36, r'(?m)^弯矩设计值Md\(kNm\)\s*(\d+(?:\.\d+)?)', 'design moment')
    bars = int(_find_num(p36, r'实配根数\s*(\d+)', 'reinforcement count'))
    bar_diameter = _find_num(p36, r'钢筋直径\(mm\)\s*(\d+)', 'reinforcement diameter')
    required_area = _find_num(p36, r'配筋面积As\(mm2\)\s*(\d+(?:\.\d+)?)', 'reinforcement area')
    support_spacing = _find_num(p33, r'水平间距\(m\)\s*(\d+(?:\.\d+)?)', 'support spacing')
    if not isclose(standard['pileLengthM'], 6.6, abs_tol=1e-8) or not isclose(standard['siteSupportDepthM'], 1.625, abs_tol=1e-8):
        raise ValueError('Standard 3-3 case has a changed identity; requires revisioned parser')
    if not isclose(stages[2]['ranges']['momentKnM'][1], report_moment, abs_tol=0.01):
        raise ValueError('S3 plot and printed design moment are inconsistent')
    # Independent arithmetic evaluation of numbers printed in the source
    # report. These calculations do NOT rederive the geotechnical resistance
    # method or authenticate the original working files.
    printed_stability = []
    for target_depth, target in ((2.05, 1.25), (4.9, 1.66)):
        pattern = (r'(?m)^H=' + str(target_depth) + r'm\s*倾覆:K=(\d+(?:\.\d+)?)')
        claimed = _find_num(p35, pattern, f'overturning K at H={target_depth}')
        if not isclose(claimed, target, abs_tol=0.02):
            raise ValueError('Overturning reported K changed outside the reviewed report version')
    factors = re.findall(r'(\d+(?:\.\d+)?)\s*×\s*(\d+(?:\.\d+)?)\s*\n\s*'
                         r'(\d+(?:\.\d+)?)\s*×\s*(\d+(?:\.\d+)?)\s*=\s*'
                         r'(\d+(?:\.\d+)?)', p35)
    if len(factors) != 2:
        raise ValueError('Expected two original overturning factor expressions')
    for depth, coefficients in zip((2.05, 4.9), factors):
        numerator_force, numerator_arm, denominator_force, denominator_arm, claimed = map(float, coefficients)
        computed = numerator_force*numerator_arm/(denominator_force*denominator_arm)
        printed_stability.append({'method':'reported_overturning_force_arm_ratio',
            'excavationDepthM':depth, 'computed':round(computed,6),
            'printed':claimed, 'arithmeticMatchesPrintedRounding':abs(computed-claimed)<=0.01,
            'methodIndependentlyQualified':False, 'physicalPage':35})
    heave_match = re.search(
        r'(\d+(?:\.\d+)?)·(\d+(?:\.\d+)?)\s*\+\s*'
        r'(\d+(?:\.\d+)?)·(\d+(?:\.\d+)?)\s*\n\s*'
        r'(\d+(?:\.\d+)?)\s*\n\s*=\s*(\d+(?:\.\d+)?)', p36)
    if heave_match is None:
        raise ValueError('Original printed heave expression missing')
    a,b,c,d,denom,claimed=map(float,heave_match.groups())
    computed=(a*b+c*d)/denom
    printed_stability.append({'method':'reported_base_heave_expression',
        'excavationDepthM':4.9,'computed':round(computed,6),
        'printed':claimed,'arithmeticMatchesPrintedRounding':abs(computed-claimed)<=0.01,
        'methodIndependentlyQualified':False,'physicalPage':36})
    # All checks here are arithmetic/reference-only, not normative method approval.
    area = bars * pi * bar_diameter ** 2 / 4
    if '𝐾=' not in p35 and '倾覆:' not in p35:
        raise ValueError('Independent report stability arithmetic pages missing')
    arithmetic = {
        'momentDesignFactor': 1.25,
        'reportedCharacteristicMomentKnM': report_moment,
        'reportedDesignMomentKnM': design_moment,
        'computedDesignMomentKnM': round(report_moment * 1.25, 4),
        'momentPrintedValueCompatible': abs(report_moment * 1.25 - design_moment) <= 0.051,
        'longitudinalBarCount': bars, 'barDiameterMm': bar_diameter,
        'providedLongitudinalAreaMm2': round(area, 3),
        'reportedRequiredAreaMm2': required_area,
        'minimumAreaScreenPass': area >= required_area,
        'fullRCSectionStrengthQualified': False,
    }
    return {
        'schemaVersion': REPORT_SCHEMA, 'sourcePdfSha256': source_hash,
        'sourcePdfPhysicalPages': physical_page_count,
        'documentStatus': 'uploaded_source_bytes_text_audited_authorship_unverified',
        'standardCase': {'section': '3-3', 'physicalPages': [32, 33, 34, 35, 36, 37],
                         'parameters': standard,
                         'soilTableMatchesRecordedExcerpt': soil_status,
                         'supportSpacingM': support_spacing,
                         'stages': stages, 'arithmetic': arithmetic,
                         'printedStabilityArithmetic': printed_stability},
        'shortPileCase': {'section': '3-3-short-pile', 'physicalPages': [37, 38, 39, 40, 41, 42],
                          'pileLengthM': short_length, 'printedMaximumMomentKnM': short_moment,
                          'mustNotMergeWithStandardCase': True},
        'originalDepthSampleArraysPresent': False,
        'originalGeotechnicalAnnexPresent': False,
        'rawFieldWaterObservationsPresent': False,
        'completeConnectionConstructionDrawingsPresent': False,
        'reportTextIsNotIndependentNumericExport': True,
        'issuerSignatureVerified': False, 'engineeringQualified': False,
        'formalConstructionIssue': False,
    }


def read_design_report_pdf(blob: bytes) -> dict:
    if not isinstance(blob, bytes) or not 1000 < len(blob) <= MAX_PDF_BYTES:
        raise ValueError('Luoyu original report PDF is missing or exceeds 24 MB')
    if not blob.startswith(b'%PDF-'):
        raise ValueError('Expected PDF source bytes')
    try:
        import fitz
    except ImportError as exc:
        raise ValueError('PyMuPDF is required to extract original report evidence') from exc
    try:
        doc = fitz.open(stream=blob, filetype='pdf')
        try:
            if doc.is_encrypted or not 42 <= doc.page_count <= 220:
                raise ValueError('Report is encrypted or missing original physical pages')
            needed = (1, 21, 32, 33, 34, 35, 36, 37, 38, 39, 40, 42)
            pages = {n: doc[n-1].get_text('text') for n in needed}
            return extract_report_page_texts(pages, source_hash=sha256(blob).hexdigest(),
                                             physical_page_count=doc.page_count)
        finally:
            doc.close()
    except (RuntimeError, TypeError) as exc:
        raise ValueError('Cannot read original report PDF') from exc
