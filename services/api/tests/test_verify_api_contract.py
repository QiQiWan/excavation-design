"""API/task verification must not silently become a design operation."""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app.calculation import engine
from app.routers import calculation
from app.schemas.domain import (CalculationCase, CalculationResult, ConstructionStage,
    DesignControlStage, ExcavationModel, Point2D, Polyline2D, Project, RetainingSystem)
from app.services import calculation_assurance, construction_stages, intelligent_design_closure, workflow_v381
from app.tasks.manager import TaskManager, TaskRecord


def project_fixture():
    case=CalculationCase(id="saved",name="saved",source="auto_default",stages=[
        ConstructionStage(id="final",name="final",excavation_elevation=-6,stage_type="final")])
    project=Project(name="verify",excavation=ExcavationModel(outline=Polyline2D(points=[
        Point2D(x=0,y=0),Point2D(x=10,y=0),Point2D(x=10,y=10)],closed=True),
        top_elevation=0,bottom_elevation=-6,depth=6),retaining_system=RetainingSystem(),calculation_cases=[case])
    project.design_settings.design_basis_confirmed=True
    project.design_settings.auto_intelligent_design_closure_enabled=True
    project.retaining_system.layout_summary={"wallLengthOptimizationHistory":[{"recomputeRequired":True}]}
    return project


class MemoryRepository:
    def __init__(self,project):
        self.project=project
        self.current_revision=7
        self.saves=[]
    def require(self,_):
        return self.project
    def require_for_calculation(self,_,*,migrate=True):
        assert migrate is False, "verify must not migrate saved inputs during load"
        return self.project
    def revision(self,_):
        return self.current_revision
    def save(self,project,**kwargs):
        self.saves.append(kwargs)
        if kwargs.get("expected_revision")!=self.current_revision:
            raise HTTPException(status_code=409,detail="concurrent project edit")
        self.current_revision+=1
        return project


def install_boundaries(monkeypatch,repo,*,concurrent=False):
    seen=[]
    def forbidden(*args,**kwargs):
        raise AssertionError("verification invoked design mutation")
    for module,name in [(intelligent_design_closure,"run_intelligent_design_closure"),
        (intelligent_design_closure,"apply_intervention_action"),
        (workflow_v381,"repair_design_control_support_references"),
        (workflow_v381,"synchronize_design_control_case"),
        (engine,"build_default_construction_cases"),(engine,"run_candidate_comparison_for_project")]:
        monkeypatch.setattr(module,name,forbidden)
    def solve(project,case,**kwargs):
        assert kwargs["mode"]=="verify"
        assert kwargs["auto_repair"] is False
        seen.append(("solve",case.id))
        if concurrent:
            repo.current_revision+=1
        return CalculationResult(project_id=project.id,case_id=case.id,calculation_mode="verify")
    def assure(project,case,result,**kwargs):
        assert result.design_iteration_summary["constructionStageSelection"]["caseId"]==case.id
        seen.append(("assure",case.id))
        result.result_hash="after-stage-selection"
        return result
    monkeypatch.setattr(engine,"run_calculation",solve)
    monkeypatch.setattr(calculation_assurance,"apply_calculation_assurance",assure)
    return seen,forbidden



def manager(monkeypatch,repo,forbidden):
    instance=TaskManager.__new__(TaskManager)
    monkeypatch.setattr(instance,"_repo",lambda:repo)
    monkeypatch.setattr(instance,"_assert_calculation_qualified",forbidden)
    monkeypatch.setattr(instance,"_stage",lambda *a,**k:None)
    monkeypatch.setattr(instance,"_append_log",lambda *a,**k:None)
    monkeypatch.setattr(instance,"_check_cancel",lambda *a,**k:None)
    monkeypatch.setattr(instance,"_resource_preflight",lambda *a,**k:{})
    return instance


def test_run_route_defaults_verify_preserves_design_and_freezes_metadata_last(monkeypatch):
    project=project_fixture(); repo=MemoryRepository(project)
    before=(deepcopy(project.retaining_system.model_dump()),deepcopy(project.calculation_cases[0].model_dump()))
    seen,_=install_boundaries(monkeypatch,repo)
    response=calculation.run(project.id,repo=repo)
    assert seen==[("solve","saved"),("assure","saved")]
    assert repo.saves[-1]["expected_revision"]==7
    assert project.retaining_system.model_dump()==before[0]
    assert project.calculation_cases[0].model_dump()==before[1]
    assert response.result_hash=="after-stage-selection"


def test_task_defaults_verify_bypasses_mutating_qualification_and_closure(monkeypatch):
    project=project_fixture(); repo=MemoryRepository(project)
    before=(deepcopy(project.retaining_system.model_dump()),deepcopy(project.calculation_cases[0].model_dump()))
    seen,forbidden=install_boundaries(monkeypatch,repo)
    task=TaskRecord(id="task",project_id=project.id,operation="calculation_full",title="test")
    result=manager(monkeypatch,repo,forbidden)._run_calculation_full(task,{"forceClosure":True,"autoInterventions":True})
    assert result["candidateComparisonCount"]==0
    assert seen==[("solve","saved"),("assure","saved")]
    assert repo.saves[-1]["expected_revision"]==7
    assert project.retaining_system.model_dump()==before[0]
    assert project.calculation_cases[0].model_dump()==before[1]


@pytest.mark.parametrize("entry",["route","task"])
def test_concurrent_edit_rejects_verify_save(monkeypatch,entry):
    project=project_fixture();repo=MemoryRepository(project)
    _,forbidden=install_boundaries(monkeypatch,repo,concurrent=True)
    if entry=="route":
        with pytest.raises(HTTPException) as raised:
            calculation.run(project.id,repo=repo)
        assert raised.value.status_code==409
    else:
        with pytest.raises(HTTPException) as raised:
            manager(monkeypatch,repo,forbidden)._run_calculation_full(
                TaskRecord(id="t",project_id=project.id,operation="calculation_full",title="t"),{})
        assert raised.value.status_code==409
    assert repo.saves[-1]["expected_revision"]==7


def test_verify_task_rejects_candidate_comparison(monkeypatch):
    project=project_fixture();repo=MemoryRepository(project)
    _,forbidden=install_boundaries(monkeypatch,repo)
    with pytest.raises(ValueError,match="topN|比选"):
        manager(monkeypatch,repo,forbidden)._run_calculation_full(
            TaskRecord(id="t",project_id=project.id,operation="calculation_full",title="t"),{"topN":1})
    assert repo.saves==[]


def select(project,case_id=None):
    assert hasattr(construction_stages,"select_calculation_case_for_verification"),"read-only case selector missing"
    return construction_stages.select_calculation_case_for_verification(project,case_id)


def test_verify_selector_preserves_saved_default_and_user_locked_priority():
    project=project_fixture();original=project.calculation_cases[0]
    assert select(project)[0] is original
    locked=original.model_copy(deep=True,update={"id":"locked","source":"user_defined","locked":True})
    project.calculation_cases.append(locked)
    assert select(project,"saved")[0] is locked


@pytest.mark.parametrize("kind",["missing","stale","semantic"])
def test_verify_selector_rejects_missing_or_stale_without_mutation(kind):
    project=project_fixture()
    if kind=="missing": project.calculation_cases=[]
    elif kind=="stale": project.calculation_cases[0].stages[0].active_support_ids=["missing-support"]
    else:
        project.design_control_stages=[DesignControlStage(code="D",name="D",source_stage_id="final",
            excavation_elevation_lower=-5,excavation_elevation_upper=-5,stage_type="final")]
    before=project.model_dump()
    with pytest.raises(ValueError): select(project)
    assert project.model_dump()==before


def test_run_route_rejects_unknown_mode(monkeypatch):
    project=project_fixture();repo=MemoryRepository(project)
    install_boundaries(monkeypatch,repo)
    with pytest.raises(HTTPException) as raised:
        calculation.run(project.id,repo=repo,mode="repair")
    assert raised.value.status_code==422
    assert repo.saves==[]


def test_verify_selector_matching_semantics_is_read_only(monkeypatch):
    project=project_fixture()
    project.design_control_stages=[DesignControlStage(code="D",name="D",source_stage_id="final",
        excavation_elevation_lower=-6,excavation_elevation_upper=-6,stage_type="final",surcharge_limit=20)]
    before=project.model_dump()
    def forbidden(*args,**kwargs): raise AssertionError("unexpected synchronization")
    monkeypatch.setattr(workflow_v381,"synchronize_design_control_case",forbidden)
    assert select(project)[0] is project.calculation_cases[0]
    assert project.model_dump()==before


def test_explicit_design_task_keeps_design_mode(monkeypatch):
    project=project_fixture();project.design_settings.auto_intelligent_design_closure_enabled=False
    repo=MemoryRepository(project)
    seen=[]
    def forbidden(*args,**kwargs): raise AssertionError("unexpected closure")
    instance=manager(monkeypatch,repo,forbidden)
    monkeypatch.setattr(instance,"_assert_calculation_qualified",lambda *a,**k:seen.append("qualification"))
    monkeypatch.setattr(workflow_v381,"repair_design_control_support_references",lambda p:{})
    monkeypatch.setattr(construction_stages,"select_calculation_case_for_run",lambda p:(p.calculation_cases[0],{"preserved":True,"caseId":"saved"}))
    def solve(project,case,**kwargs):
        seen.append(kwargs["mode"])
        return CalculationResult(project_id=project.id,case_id=case.id)
    monkeypatch.setattr(engine,"run_calculation",solve)
    monkeypatch.setattr(calculation_assurance,"apply_calculation_assurance",lambda *a,**k:a[2])
    monkeypatch.setattr(repo,"save",lambda *a,**k:None)
    instance._run_calculation_full(TaskRecord(id="t",project_id=project.id,operation="calculation_full",title="t"),{"mode":"design"})
    assert seen==["qualification","design"]


@pytest.mark.parametrize("entry",["route","task"])
def test_verify_metadata_has_current_idempotent_assurance(monkeypatch,entry):
    from app.services.support_topology_contract import support_topology_hash
    project=project_fixture();repo=MemoryRepository(project)
    real_assure=calculation_assurance.apply_calculation_assurance
    _,forbidden=install_boundaries(monkeypatch,repo)
    monkeypatch.setattr(calculation_assurance,"apply_calculation_assurance",real_assure)
    def solve(project,case,**kwargs):
        return CalculationResult(project_id=project.id,case_id=case.id,calculation_mode="verify",
            support_topology_hash=support_topology_hash(project))
    monkeypatch.setattr(engine,"run_calculation",solve)
    instance=manager(monkeypatch,repo,forbidden)
    for expected_revision in (7,8):
        if entry=="route": calculation.run(project.id,repo=repo)
        else:
            instance._run_calculation_full(TaskRecord(id="t",project_id=project.id,operation="calculation_full",title="t"),{})
        result=project.calculation_results[-1]
        assert repo.saves[-1]["expected_revision"]==expected_revision
        assert calculation_assurance.verify_current_calculation_contract(project,result)["current"]
        previous_hash=result.result_hash
        real_assure(project,project.calculation_cases[0],result)
        assert result.result_hash==previous_hash


def test_repository_verify_load_does_not_migrate_saved_case(monkeypatch):
    from types import SimpleNamespace
    from app.storage import repository
    project=project_fixture()
    payload=project.model_dump(mode="json",by_alias=True)
    repo=repository.ProjectRepository.__new__(repository.ProjectRepository)
    repo.store=SimpleNamespace(get=lambda _:deepcopy(payload))
    def forbidden(*args,**kwargs): raise AssertionError("verification loaded through migration")
    monkeypatch.setattr(repository,"_migrate_loaded_project",forbidden)
    monkeypatch.setattr(repository,"ProjectArtifactStore",lambda:None)
    monkeypatch.setattr(repository,"rehydrate_geological_evidence",lambda data,_:data)
    monkeypatch.setattr(repository,"rehydrate_latest_calculation_evidence",lambda data,_:data)
    loaded=repo.require_for_calculation(project.id,migrate=False)
    assert loaded.calculation_cases[0].model_dump()==project.calculation_cases[0].model_dump()
    assert loaded.design_settings.model_dump()==project.design_settings.model_dump()
