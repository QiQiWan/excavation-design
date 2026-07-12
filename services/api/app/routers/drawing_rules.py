from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.drawing_rules import (
    build_drawing_plan,
    drawing_rule_capabilities,
    get_effective_drawing_rule_set,
    list_drawing_rule_presets,
    normalize_drawing_rule_set,
    optimize_drawing_rule_set,
    validate_drawing_rule_set,
)
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(tags=["drawing-rules"])


@router.get("/api/drawing-rules/presets")
def get_presets() -> dict[str, Any]:
    return {"schemaVersion": "1.0", "presets": list_drawing_rule_presets()}


@router.get("/api/drawing-rules/capabilities")
def get_capabilities() -> dict[str, Any]:
    return drawing_rule_capabilities()


@router.get("/api/projects/{project_id}/drawing-rules")
def get_project_rules(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    rules = get_effective_drawing_rule_set(project)
    validation = validate_drawing_rule_set(project, rules)
    return {"ruleSet": rules, "validation": {"valid": validation["valid"], "errors": validation["errors"], "warnings": validation["warnings"]}}


@router.put("/api/projects/{project_id}/drawing-rules")
def put_project_rules(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    validation = validate_drawing_rule_set(project, payload)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail={"message": "出图规则集校验失败。", **validation})
    project.drawing_rule_set = validation["normalized"]
    repo.save(project)
    return {"ruleSet": project.drawing_rule_set, "validation": {"valid": True, "errors": [], "warnings": validation["warnings"]}, "preview": validation["preview"]}


@router.post("/api/projects/{project_id}/drawing-rules/validate")
def validate_project_rules(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    return validate_drawing_rule_set(repo.require(project_id), payload)


@router.get("/api/projects/{project_id}/drawing-rules/preview")
def preview_project_rules(
    project_id: str,
    scope: Literal["full", "general", "rebar", "details"] = Query("full"),
    issue_mode: Literal["review", "construction"] = Query("review"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    project = repo.require(project_id)
    return build_drawing_plan(project, get_effective_drawing_rule_set(project), scope=scope, issue_mode=issue_mode)


@router.get("/api/projects/{project_id}/drawing-rules/intelligence")
def get_drawing_intelligence(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    plan = build_drawing_plan(project, get_effective_drawing_rule_set(project), scope="full", issue_mode="review")
    return {
        "projectId": project.id,
        "ruleSetHash": plan.get("ruleSetHash"),
        "planHash": plan.get("planHash"),
        "intelligence": plan.get("drawingIntelligence") or {},
    }


@router.post("/api/projects/{project_id}/drawing-rules/optimize")
def optimize_project_rules(project_id: str, payload: dict[str, Any] | None = None, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    return optimize_drawing_rule_set(repo.require(project_id), payload or {})


@router.post("/api/projects/{project_id}/drawing-rules/apply-preset/{preset}")
def apply_project_preset(project_id: str, preset: str, repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    known = {str(item["id"]) for item in list_drawing_rule_presets()}
    if preset not in known:
        raise HTTPException(status_code=404, detail=f"Unknown drawing rule preset: {preset}")
    rules = normalize_drawing_rule_set({"preset": preset}, preset=preset)
    validation = validate_drawing_rule_set(project, rules)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail=validation)
    project.drawing_rule_set = rules
    repo.save(project)
    return {"ruleSet": rules, "preview": validation["preview"], "warnings": validation["warnings"]}


@router.post("/api/projects/{project_id}/drawing-rules/apply-candidate")
def apply_optimized_candidate(project_id: str, payload: dict[str, Any], repo: ProjectRepository = Depends(get_repository)) -> dict[str, Any]:
    project = repo.require(project_id)
    candidate_id = str(payload.get("candidateId") or "")
    supplied_rules = payload.get("ruleSet")
    if isinstance(supplied_rules, dict):
        validation = validate_drawing_rule_set(project, supplied_rules)
        if not validation["valid"]:
            raise HTTPException(status_code=422, detail={"message": "候选规则集校验失败。", **validation})
        candidate = {
            "candidateId": candidate_id or f"drawing-rule-{validation['normalized'].get('ruleSetHash')}",
            "ruleSet": validation["normalized"],
            "score": payload.get("score"),
            "rank": payload.get("rank"),
            "source": payload.get("source") or "client-validated",
        }
    else:
        optimization = dict(payload.get("optimization") or {})
        optimization["includeRuleSets"] = True
        result = optimize_drawing_rule_set(project, optimization)
        candidate = next((x for x in result["candidates"] if x["candidateId"] == candidate_id), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Drawing rule candidate not found or no longer reproducible.")
    project.drawing_rule_set = candidate["ruleSet"]
    repo.save(project)
    return {"applied": True, "candidate": candidate, "preview": build_drawing_plan(project, project.drawing_rule_set)}
