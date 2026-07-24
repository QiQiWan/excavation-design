from fastapi import APIRouter
from app.services.intelligent_design.diagnosis_engine import DesignDiagnosisEngine
from app.services.intelligent_design.optimization_controller import OptimizationController
from app.services.intelligent_design.design_problem_center import build_problem_center
from app.services.intelligent_design.closure_optimizer import ClosureOptimizer

router=APIRouter(prefix="/api/intelligent-design", tags=["intelligent-design"])

diagnoser=DesignDiagnosisEngine()
optimizer=OptimizationController()
closure=ClosureOptimizer()

@router.post('/diagnose')
def diagnose(payload: dict):
    diagnostics=diagnoser.diagnose(payload.get('checks',{}))
    return diagnoser.build_resolution(diagnostics)

@router.post('/optimize')
def optimize(payload: dict):
    return optimizer.suggest(payload)

@router.post('/problem-center')
def problem_center(payload: dict):
    diagnostics=payload.get('diagnostics',[])
    return build_problem_center(diagnostics)

@router.post('/closure-run')
def closure_run(payload: dict):
    return closure.run(payload.get('design',{}), None)
