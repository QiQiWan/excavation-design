export interface UnitSystem {
  length: 'm' | 'mm';
  force: 'kN' | 'N';
  stress: 'kPa' | 'MPa' | 'Pa';
  angle: 'degree' | 'radian';
}

export interface CoordinateSystem {
  type: 'local' | 'projected' | 'geographic';
  originX: number;
  originY: number;
  originZ: number;
  epsg?: string;
  elevationDatum?: string;
}

export interface DesignSettings {
  safetyGrade: string;
  environmentGrade: string;
  groundwaterLevel: number;
  groundwaterLevelInside?: number;
  confinedWaterHeadElevation?: number;
  surcharge: number;
  minimumSegmentLength: number;
  ruleSet: string;
  pressureMethod?: 'active' | 'at_rest';
  waterSoilMethod?: 'separate' | 'combined';
  autoCenterExcavationOnGeology?: boolean;
  defaultSupportSpacing?: number;
}

export interface Point2D { x: number; y: number }
export interface Polyline2D { points: Point2D[]; closed: boolean }

export interface SoilParameters {
  unitWeight?: number;
  saturatedUnitWeight?: number;
  effectiveUnitWeight?: number;
  cohesion?: number;
  frictionAngle?: number;
  elasticModulus?: number;
  poissonRatio?: number;
  compressionModulus?: number;
  permeabilityX?: number;
  permeabilityY?: number;
  permeabilityZ?: number;
  k0?: number;
  horizontalSubgradeModulus?: number;
}

export interface BoreholeLayer {
  id: string;
  stratumCode: string;
  stratumName: string;
  topDepth: number;
  bottomDepth: number;
  topElevation: number;
  bottomElevation: number;
}

export interface Borehole {
  id: string;
  code: string;
  x: number;
  y: number;
  collarElevation: number;
  depth: number;
  layers: BoreholeLayer[];
}

export interface Stratum {
  id: string;
  code: string;
  name: string;
  color?: string;
  parameters: SoilParameters;
  confidence: string;
}

export interface SurfaceGrid { xValues: number[]; yValues: number[]; zValues: number[][] }
export interface GeologicalSurface { stratumCode: string; surfaceType: 'top' | 'bottom'; grid: SurfaceGrid; confidence: string }
export interface VtuCellBlock { index: number; vtkType?: number; cellType: string; nodes: number[]; attributes?: Record<string, unknown> }
export interface VtuSummary { pointCount?: number; cellCount?: number; cellTypes?: string[] }
export interface VtuMesh { points?: number[][]; cellBlocks?: VtuCellBlock[]; summary?: VtuSummary; detectedFields?: string[]; suggestedMapping?: Record<string, string>; warnings?: string[] }
export interface GeologicalModel { surfaces: GeologicalSurface[]; volumes: unknown[]; vtuMesh?: VtuMesh; warnings: string[] }

export interface ExcavationSegment {
  id: string;
  name: string;
  start: Point2D;
  end: Point2D;
  length: number;
  outwardNormal: Point2D;
  midpoint: Point2D;
  chainage: number;
}

export interface ConstructionObstacle { id?: string; name: string; obstacleType: string; outline?: Polyline2D; center?: Point2D; width?: number; length?: number; clearance?: number; active?: boolean; note?: string; optimizationLocked?: boolean; optimizationLockReason?: string }
export interface ExcavationModel {
  id: string;
  name: string;
  outline: Polyline2D;
  topElevation: number;
  bottomElevation: number;
  depth: number;
  segments: ExcavationSegment[];
  obstacles?: ConstructionObstacle[];
  drawingLayers?: Record<string, unknown>[];
  supportAxisOffset?: number;
  basementWallOffset?: number;
  explicitPlacement?: boolean;
  centeredOnGeology?: boolean;
  placementNote?: string;
  area?: number;
  perimeter?: number;
  warnings: string[];
}

export interface ReinforcementGroup {
  id: string;
  name: string;
  barType: string;
  diameter: number;
  spacing?: number;
  count?: number;
  grade: string;
  locationDescription: string;
  areaPerMeter?: number;
  requiredAreaPerMeter?: number;
  checkStatus?: string;
}

export interface WallDesignResult {
  maxMoment?: number;
  maxShear?: number;
  maxDisplacement?: number;
  maxMomentDesign?: number;
  maxShearDesign?: number;
  requiredReinforcementArea?: number;
  providedReinforcementArea?: number;
  momentCapacity?: number;
  shearCapacity?: number;
  rebarDiameter?: number;
  rebarSpacing?: number;
  checkStatus?: string;
  method?: string;
  notes?: string[];
}

export interface DiaphragmWallPanel {
  id: string;
  segmentId: string;
  panelCode: string;
  axis: Polyline2D;
  designFaceCode?: string;
  designLength?: number;
  faceSegmentIds?: string[];
  thickness: number;
  topElevation: number;
  bottomElevation: number;
  concreteGrade: string;
  rebarGrade: string;
  reinforcement: ReinforcementGroup[];
  designResults?: WallDesignResult;
  professionalReviewRequired: boolean;
  objectLocatorMap?: Record<string, Record<string, unknown>>;
}

export interface SectionDefinition { width?: number; height?: number; diameter?: number; wallThickness?: number; name?: string }
export interface MaterialDefinition { name: string; grade: string; elasticModulus?: number }
export interface SupportElement {
  id: string;
  code: string;
  levelIndex: number;
  elevation: number;
  start: Point2D;
  end: Point2D;
  supportRole?: 'main_strut' | 'corner_diagonal' | 'ring_strut' | 'manual';
  layoutNote?: string;
  spanLength?: number;
  baySpacing?: number;
  startFaceCode?: string;
  endFaceCode?: string;
  startTributaryWidth?: number;
  endTributaryWidth?: number;
  forceDistributionNote?: string;
  sectionType: string;
  section: SectionDefinition;
  material: MaterialDefinition;
  preload?: number;
  preloadRatio?: number;
  temperatureDeltaC?: number;
  thermalAxialForce?: number;
  gapClosureForce?: number;
  constructionDeviationMm?: number;
  eccentricityMoment?: number;
  effectiveAxialForceStandard?: number;
  designAxialForce?: number;
  constructionEffectNote?: string;
  lifecycleNote?: string;
  preloadStageId?: string;
  removalStageId?: string;
  preloadProtocolStatus?: string;
  optimizationLocked?: boolean;
  optimizationLockedStart?: boolean;
  optimizationLockedEnd?: boolean;
  optimizationLockReason?: string;
  reinforcement: ReinforcementGroup[];
}

export interface WaleBeamInternalForcePoint { chainage: number; shear: number; moment: number; deflection: number }
export interface WaleBeamInternalForceResult {
  id: string;
  waleBeamCode: string;
  faceCode: string;
  levelIndex: number;
  elevation: number;
  stageId?: string;
  pressureLineLoad: number;
  beamLength: number;
  supportNodeCount: number;
  points: WaleBeamInternalForcePoint[];
  maxMoment: number;
  maxShear: number;
  maxDeflection: number;
  maxMomentDesign?: number;
  maxShearDesign?: number;
  method: string;
  warnings: string[];
}
export interface WaleBeamEnvelopePoint { chainage: number; maxPositiveMoment: number; maxNegativeMoment: number; maxAbsShear: number; maxAbsDeflection: number }
export interface WaleBeamEnvelopeResult { id: string; waleBeamCode: string; levelIndex?: number; faceCode?: string; governingStageIds: string[]; points: WaleBeamEnvelopePoint[]; maxPositiveMoment: number; maxNegativeMoment: number; maxAbsShear: number; maxAbsDeflection: number; diagramNote?: string }
export interface WaleBeamDesignResult {
  id: string;
  waleBeamCode: string;
  faceCode?: string;
  levelIndex?: number;
  maxMoment: number;
  maxShear: number;
  maxDeflection: number;
  maxMomentDesign: number;
  maxShearDesign: number;
  requiredReinforcementArea?: number;
  providedReinforcementArea?: number;
  momentCapacity?: number;
  shearCapacity?: number;
  mainBarDiameter?: number;
  mainBarSpacing?: number;
  stirrupDiameter?: number;
  stirrupSpacing?: number;
  nodeAdditionalReinforcementNote?: string;
  deflectionLimit?: number;
  deflectionRatio?: number;
  deflectionCheckStatus?: string;
  optimizedWidth?: number;
  optimizedHeight?: number;
  optimizationHistory?: Record<string, unknown>[];
  localBearingSpreadWidth?: number;
  localBearingSpreadHeight?: number;
  wallConnectionNote?: string;
  envelope?: WaleBeamEnvelopeResult;
  checkStatus?: string;
  method?: string;
  notes?: string[];
}
export interface BeamElement { id: string; code: string; axis: Polyline2D; elevation: number; section: SectionDefinition; material: MaterialDefinition; beamRole?: string; designAxialForce?: number; designMoment?: number; designShear?: number; supportLevel?: number; internalForceResults?: WaleBeamInternalForceResult[]; designResult?: WaleBeamDesignResult; reinforcement?: ReinforcementGroup[] }
export interface BearingPlateDesign { plateWidth: number; plateHeight: number; plateThickness: number; bearingArea: number; bearingStress?: number; bearingCapacity?: number; checkStatus?: string; designNote?: string }
export interface SupportWaleNode { id: string; code: string; supportId: string; supportCode: string; levelIndex: number; elevation: number; location: Point2D; faceCode?: string; waleBeamCode?: string; nodeType: string; bearingPlate?: BearingPlateDesign; reinforcement: ReinforcementGroup[]; checkStatus?: string; designNote?: string }
export interface FoundationDesign { code: string; foundationType: string; width: number; length: number; thickness: number; area: number; verticalForce: number; pileDiameter?: number; pileLength?: number; pileCount?: number; pileCapacity?: number; pileUtilization?: number; pileTipElevation?: number; checkStatus?: string; designNote?: string }
export interface ColumnElement { id: string; code: string; location: Point2D; topElevation: number; bottomElevation: number; section: SectionDefinition; material: MaterialDefinition; supportCodes?: string[]; serviceAreaNote?: string; foundationDesign?: FoundationDesign }
export interface RetainingSystem {
  id: string;
  type: string;
  diaphragmWalls: DiaphragmWallPanel[];
  crownBeams: BeamElement[];
  waleBeams: BeamElement[];
  ringBeams?: BeamElement[];
  supports: SupportElement[];
  supportNodes?: SupportWaleNode[];
  columns: ColumnElement[];
  layoutSummary?: Record<string, unknown>;
  optimizationLocks?: Record<string, unknown>[];
  replacementPath?: Record<string, unknown>[];
  supportLayoutRepair?: SupportLayoutRepairSummary;
  warnings: string[];
}

export interface PressurePoint { depth: number; elevation: number; earthPressure: number; waterPressure: number; totalPressure: number; stratumCode?: string; ka?: number; kp?: number; k0?: number; cohesion?: number; frictionAngle?: number }
export interface PressureProfile { points: PressurePoint[]; method: string; standardReferences?: string[]; warnings: string[] }
export interface SupportForceResult { supportId?: string; levelIndex: number; elevation: number; tributaryTop: number; tributaryBottom: number; axialForce: number; axialForceDesign?: number; importanceFactor?: number; partialFactor?: number; unit: string; method: string; faceCode?: string; supportEndpoint?: 'start' | 'end' | 'unknown'; waleBeamCode?: string; waleChainage?: number; tributaryWidth?: number; continuousBeamReaction?: number; elasticSupportStiffness?: number; normalProjectionFactor?: number; beamNodeCount?: number; distributionMethod?: string; distributionNote?: string; preloadEffect?: number; thermalEffect?: number; gapEffect?: number; eccentricityEffect?: number; effectiveAxialForce?: number; constructionEffectNote?: string }
export interface WallInternalForcePoint { depth: number; elevation: number; shear: number; moment: number; displacement?: number }
export interface WallInternalForceResult { segmentId: string; stageId: string; points: WallInternalForcePoint[]; maxMoment: number; maxShear: number; maxDisplacement?: number; maxMomentDesign?: number; maxShearDesign?: number; method: string; warnings: string[] }
export interface CheckResult { ruleId?: string; rule_id?: string; objectId?: string; object_id?: string; objectType?: string; object_type?: string; status: string; calculatedValue?: number; calculated_value?: number; limitValue?: number; limit_value?: number; unit?: string; message: string; clauseReference?: string; clause_reference?: string; [key: string]: unknown }

export interface GlobalCoupledDof { index: number; name: string; value: number; unit: string; dofType?: string; objectId?: string; stageStatus?: string }
export interface GlobalCoupledSupportReaction { supportId: string; supportCode: string; endpoint: string; faceCode: string; levelIndex: number; chainage: number; depth: number; nodeDisplacement: number; springStiffness: number; nodeReaction: number; axialForce: number; axialDeformation: number; normalProjectionFactor: number; directionCosineX?: number; directionCosineY?: number; rigidNodeFactor?: number; governingSource?: string }
export interface GlobalCoupledSystemResult { method: string; stageId?: string; faceCode?: string; fallback: boolean; reason?: string; matrixSize: number; conditionNumber?: number; dofSummary: Record<string, unknown>; dofs: GlobalCoupledDof[]; wallDisplacementProfile: Record<string, unknown>[]; supportReactions: GlobalCoupledSupportReaction[]; columnVerticalSupports: Record<string, unknown>[]; maxWallDisplacement: number; maxSupportAxialForce: number; modelDimension?: string; spatialMatrixSize?: number; spatialConditionNumber?: number; spatialDofSummary?: Record<string, unknown>; wallRotationProfile?: Record<string, unknown>[]; waleNodeProfile?: Record<string, unknown>[]; supportAxialDofs?: Record<string, unknown>[]; columnVerticalDofs?: Record<string, unknown>[]; slabReplacementStiffness?: number; rigidNodeZones?: Record<string, unknown>[]; notes: string[] }
export interface StabilityDetailedResult { method?: string; controllingSectionId?: string; controllingSectionName?: string; heaveFactor?: number; confinedUpliftFactor?: number; seepageFactor?: number; overallStabilityFactor?: number; weakLayerIndex?: number; minSafetyFactor?: number; controllingMode?: string; circularSlipSurfaces?: Record<string, unknown>[]; seepagePaths?: Record<string, unknown>[]; drawdownProcess?: Record<string, unknown>[]; dewateringWells?: Record<string, unknown>[]; depressurizationWells?: Record<string, unknown>[]; improvementOptions?: Record<string, unknown>[]; diagramData?: Record<string, unknown>; reviewNotes?: string[] }
export interface DrawingSheetResult { sheetId: string; title: string; scale: string; filePath?: string; sheetType?: string; modelObjects?: string[]; notes?: string[] }



export interface RebarVisualizationPoint { x: number; y: number; z: number }
export interface RebarVisualizationBar { id: string; ifcClass: string; hostType: string; hostCode: string; hostId: string; groupId: string; groupName: string; barType: string; diameterMm: number; spacingMm?: number; count?: number; grade: string; locationDescription?: string; checkStatus?: string; start: RebarVisualizationPoint; end: RebarVisualizationPoint; lengthM: number; representation: string; estimatedFullCount?: number; sampledFromCount?: number }
export interface RebarVisualizationHost { hostType: string; hostCode: string; groupCount: number; sampledBarCount: number; estimatedFullBarCount: number; tokens: string[] }
export interface RebarIfcVisualization { projectId: string; exportProfileMapping: Record<string, string>; summary: { sampledBarCount: number; estimatedFullBarCount: number; hostCount: number; omittedHostCount?: number; steelMassProxyKg?: number; byBarType: Record<string, number>; byHostType: Record<string, number>; byCheckStatus: Record<string, number>; detailLevel: string; officialDetailingLimit?: string }; bars: RebarVisualizationBar[]; hosts: RebarVisualizationHost[]; notes: string[] }

export interface QualityGateIssue { id?: string; category: string; severity: string; objectId?: string; objectType?: string; message: string; recommendation?: string; highlightGeometry?: Record<string, unknown>; relatedObjectIds?: string[]; displayHint?: string }
export interface SupportLayoutQualitySummary { score: number; status: string; summary: string; metrics: Record<string, unknown>; issues: QualityGateIssue[]; highlights?: Record<string, unknown>[]; crossingPairs?: Record<string, unknown>[]; checkedAt?: string }
export interface SupportLayoutOptimizationCandidate { id?: string; rank: number; score: number; status: string; targetSpacing: number; columnMaxSpan: number; objectiveTerms: Record<string, number>; softObjectives?: Record<string, number>; hardConstraints?: Record<string, unknown>; variableSummary?: Record<string, unknown>; lineAdjustments?: Record<string, unknown>[]; planGeometry?: Record<string, unknown>; deltaGeometry?: Record<string, unknown>; weightSummary?: Record<string, unknown>; exportReadiness?: Record<string, unknown>; metrics: Record<string, unknown>; issueCount: number; failCount: number; warningCount: number; supportCount: number; columnCount: number; maxSpanLength?: number; maxBaySpacing?: number; crossingCount?: number; obstacleConflictCount?: number; axialPeakProxy?: number; symmetryScore?: number; muckPathContinuityScore?: number; fullCalculation?: Record<string, unknown>; constructabilityNote?: string }
export interface SupportLayoutRepairSummary { optimizationMethod?: string; optimizationPhase?: string; hardConstraintLabels?: string[]; softObjectiveLabels?: string[]; objectiveWeights?: Record<string, number>; candidateCount?: number; bestCandidateId?: string; selectedCandidateId?: string; lockedSupportIds?: string[]; lockSummary?: Record<string, unknown>; candidates?: SupportLayoutOptimizationCandidate[]; candidateFullCalculations?: Record<string, unknown>[]; status: string; scoreBefore?: number; scoreAfter?: number; actions: Record<string, unknown>[]; unresolvedIssues: QualityGateIssue[]; summary: string; checkedAt?: string }
export interface IfcViewerProfileRisk { viewer: string; status: string; riskLevel: string; score: number; riskItems: string[]; recommendation?: string }
export interface IfcCompatibilityCheckResult { score: number; status: string; summary: string; filePath?: string; exportMode?: string; entityCounts: Record<string, number>; rawUnicodeFound?: boolean; missingReferences?: string[]; zeroDimensionCount?: number; invalidPlacementCount?: number; missingMaterialAssociationCount?: number; missingSpatialContainmentCount?: number; viewerProfiles?: IfcViewerProfileRisk[]; issues: QualityGateIssue[]; checkedAt?: string }
export interface FormalReportGate { status: string; allowedForOfficialIssue: boolean; headline: string; blockingItems: QualityGateIssue[]; warningItems: QualityGateIssue[]; missingItems: QualityGateIssue[]; checklistSections?: Record<string, unknown>[]; summary: Record<string, unknown>; checkedAt?: string }

export interface DesignReviewSummary { strengthStatus: string; stiffnessStatus: string; stabilityStatus: string; strengthFailCount: number; stiffnessFailCount: number; stabilityFailCount: number; strengthWarningCount: number; stiffnessWarningCount: number; stabilityWarningCount: number; maxStrengthUtilization?: number; maxStiffnessUtilization?: number; minStabilitySafetyFactor?: number; notes: string[] }

export interface StageCalculationResult { stageId: string; segmentId: string; pressureProfile: PressureProfile; supportForces: SupportForceResult[]; waleBeamResults?: WaleBeamInternalForceResult[]; coupledSystemResult?: Record<string, unknown>; globalCoupledResult?: GlobalCoupledSystemResult; wallInternalForce?: WallInternalForceResult; wallInternalForcePlaceholder?: Record<string, unknown>; stabilityChecks?: CheckResult[]; rcChecks?: CheckResult[]; checks: CheckResult[] }
export interface GoverningValues { maxTotalPressure: number; maxSupportAxialForce: number; maxWallMoment?: number; maxWallShear?: number; maxDisplacement?: number; governingCheckStatus?: string; embedmentSafetyFactorMin?: number; heaveSafetyFactorMin?: number; seepageSafetyFactorMin?: number; strengthCheckStatus?: string; stiffnessCheckStatus?: string; stabilityCheckStatus?: string }
export interface CalculationResult {
  id: string;
  projectId: string;
  caseId: string;
  stageResults: StageCalculationResult[];
  governingValues: GoverningValues;
  warnings: string[];
  checks?: CheckResult[];
  checkSummary?: Record<string, number>;
  designIterationSummary?: Record<string, unknown>;
  optimizationActions?: Record<string, unknown>[];
  reportDiagramData?: Record<string, unknown>;
  designReviewSummary?: DesignReviewSummary;
  stabilityDetailedResult?: StabilityDetailedResult;
  drawingSheets?: DrawingSheetResult[];
  supportLayoutQuality?: SupportLayoutQualitySummary;
  supportLayoutRepair?: SupportLayoutRepairSummary;
  ifcCompatibility?: IfcCompatibilityCheckResult;
  formalReportGate?: FormalReportGate;
  standards?: string[];
  professionalReviewRequired: boolean;
}

export interface Project {
  id: string;
  name: string;
  location?: string;
  createdAt: string;
  updatedAt: string;
  unitSystem: UnitSystem;
  coordinateSystem: CoordinateSystem;
  designSettings: DesignSettings;
  boreholes: Borehole[];
  strata: Stratum[];
  geologicalModel?: GeologicalModel;
  excavation?: ExcavationModel;
  retainingSystem?: RetainingSystem;
  calculationCases: unknown[];
  calculationResults: CalculationResult[];
  cadTemplate?: CadTemplateConfig;
  messages: string[];
}

export interface ImportResult {
  success: boolean;
  boreholeCount: number;
  layerCount: number;
  stratumCount: number;
  warnings: string[];
  errors: string[];
  boreholes: Borehole[];
  strata: Stratum[];
}

export interface AcceptanceMatrixItem { id: string; title: string; required: boolean; status: string; message: string }

export interface CalculationTraceEntry { id: string; category: string; title: string; objectType?: string; objectId?: string; stageId?: string; stageName: string; demandName: string; demandValue?: number; capacityValue?: number; utilization?: number; unit?: string; status: string; formula?: string; codeReference?: string; method?: string; inputParameters?: Record<string, unknown>; resultPath?: string; locator?: Record<string, unknown> }
export interface CalculationTraceResult { projectId: string; calculationResultId?: string; summary: { traceCount: number; controlPathCompleteness: number; governingObjectCount: number; codeReferenceCount: number; status: string; message: string; statusCounts?: Record<string, number> }; entries: CalculationTraceEntry[]; governingMap: string[]; notes: string[] }

export interface AssuranceResult {
  projectId: string;
  softwareVersion: string;
  capabilityCompleteness?: number;
  completionPercent: number;
  softwareFlowComplete?: boolean;
  softwareFlowMissingItems?: AcceptanceMatrixItem[];
  engineeringCheckStatus?: string;
  closedLoopComplete: boolean;
  officialIssueGateStatus?: string;
  officialIssueGateAllowed?: boolean;
  officialIssueGateHeadline?: string;
  officialIssueGateDetail?: string;
  officialIssueBlockingItems?: QualityGateIssue[];
  officialIssueWarningItems?: QualityGateIssue[];
  officialIssueMissingItems?: QualityGateIssue[];
  supportLayoutQuality?: SupportLayoutQualitySummary;
  supportLayoutRepair?: SupportLayoutRepairSummary;
  ifcCompatibility?: IfcCompatibilityCheckResult;
  professionalReviewRequired: boolean;
  checkSummary?: Record<string, number>;
  failureCount: number;
  manualReviewCount: number;
  acceptanceMatrix: AcceptanceMatrixItem[];
  remainingBoundaryPolicy: string[];
}


export interface PitTask {
  id: string;
  projectId: string;
  operation: string;
  title: string;
  status: 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | string;
  progress: number;
  currentStep: string;
  result?: Record<string, unknown>;
  error?: string;
  logs?: string[];
  createdAt: string;
  updatedAt: string;
  finishedAt?: string;
  cancelRequested?: boolean;
}

export interface IssueCenterItem {
  id: string;
  category: string;
  severity: 'fail' | 'warning' | 'manual_review' | 'pass' | string;
  message: string;
  recommendation: string;
  workflowStep: string;
  objectType?: string;
  objectId?: string;
  source?: string;
  targetPanel?: string;
  autoFixAvailable?: boolean;
  locator?: Record<string, unknown>;
  impact?: string;
}


export interface IssueCenterMaturity {
  softwareVersion: string;
  overallCompletion: number;
  dataModelCompletion: number;
  designCalculationCompletion: number;
  bimCadDeliverableCompletion: number;
  interactionClosedLoopCompletion: number;
  officialIssueReadiness: number;
  engineeringAcceptanceReadiness?: number;
  projectWorkflowCompletion?: number;
  systemModuleCompletion?: number;
  closedLoopComplete: boolean;
  projectClosedLoopComplete?: boolean;
  limitations: string[];
  moduleLedger?: { id: string; name: string; status: string; completion: number; evidence: string }[];
}

export interface IssueCenterResult {
  projectId: string;
  summary: Record<string, number>;
  issueCount: number;
  issues: IssueCenterItem[];
  maturity: IssueCenterMaturity;
  moduleLedger?: { id: string; name: string; status: string; completion: number; evidence: string }[];
  nextActions: { title: string; severity: string; workflowStep: string; recommendation: string; autoFixAvailable?: boolean }[];
  officialIssueAllowed: boolean;
  professionalReviewRequired: boolean;
  objectLocatorMap?: Record<string, Record<string, unknown>>;
}

export interface RebarDetailingEntry { barMark: string; hostType: string; hostCode: string; hostId?: string; groupId?: string; groupName: string; barType: string; diameterMm: number; spacingMm?: number; quantity: number; grade: string; shapeCode: string; shapeDescription: string; singleLengthM: number; totalLengthM: number; totalWeightKg: number; anchorageStatus: string; lapStatus: string; hookStatus: string; checkStatus?: string; note?: string }
export interface IndividualRebarPoint { x: number; y: number; z: number }
export interface IndividualRebarBar { barId: string; barMark: string; subIndex: number; hostType: string; hostCode: string; hostId: string; groupId: string; groupName: string; barType: string; diameterMm: number; grade: string; shapeCode: string; points: IndividualRebarPoint[]; segments: Record<string, unknown>[]; centerlineLengthM: number; anchorageLengthM: number; lapLengthM: number; hookLengthM: number; cutLengthM: number; unitWeightKgPerM: number; weightKg: number; anchorageStatus: string; lapStatus: string; hookStatus: string; source?: string }
export interface RebarDetailingResult { projectId: string; detailLevel: string; entries: RebarDetailingEntry[]; individualBars?: IndividualRebarBar[]; geometrySummary?: Record<string, unknown>; constructionJointPlan?: Record<string, unknown>[]; cageSegments?: Record<string, unknown>[]; liftingPlan?: Record<string, unknown>[]; spliceSchedule?: Record<string, unknown>[]; bendRadiusChecks?: Record<string, unknown>[]; coverConflictChecks?: Record<string, unknown>[]; signoffChecklist?: Record<string, unknown>[]; shopDrawingReadiness?: Record<string, unknown>; summary: Record<string, unknown>; notes: string[] }
export interface CadTemplateConfig { templateVersion?: string; enterpriseName?: string; projectCode?: string; stage?: string; designer?: string; checker?: string; approver?: string; sheetPrefix?: string; drawingUnit?: string; titleBlock?: Record<string, unknown>; layerStandard?: Record<string, string>; sheetRules?: Record<string, unknown>; dimensionRules?: Record<string, unknown>; issueBinding?: Record<string, unknown> }
export interface BenchmarkCaseSpec { caseId: string; name: string; sourceTitle: string; sourceUrl: string; publicDataBasis: string; lengthM: number; widthM: number; depthM: number; wallDepthM?: number; supportLevels?: number; soilProfile: string; groundwaterM: number; surchargeKpa: number; geometry?: string; notes?: string }
export interface BenchmarkRunResult { benchmarkVersion?: string; caseCount?: number; cases?: Record<string, unknown>[]; caseId?: string; projectId?: string; name?: string; sourceUrl?: string; depthM?: number; planSizeM?: number[]; supportCount?: number; columnCount?: number; checkSummary?: Record<string, number>; issueSummary?: Record<string, number>; traceCount?: number; officialIssueAllowed?: boolean }
