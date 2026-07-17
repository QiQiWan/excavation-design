from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import logging
import os
from threading import Lock
import time
from typing import Any

from app.services.design_qualification import build_design_qualification, build_support_system_options
from app.services.progressive_design import build_progressive_design_session, normalize_progressive_config
from app.services.runtime_resource_policy import adaptive_resource_policy
from app.services.support_layout import plan_shape_diagnostics
from app.storage.repository import ProjectRepository

_CACHE_LOCK = Lock()
_CACHE: "OrderedDict[tuple[str, str, int, int, int], tuple[float, dict[str, Any]]]" = OrderedDict()
_PROJECT_LOCKS: dict[tuple[str, str], Lock] = {}
logger = logging.getLogger("pitguard.design-bootstrap")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_settings() -> tuple[int, float]:
    try:
        maximum = max(2, min(128, int(os.getenv("PITGUARD_DESIGN_BOOTSTRAP_CACHE_SIZE", "24"))))
    except (TypeError, ValueError):
        maximum = 24
    try:
        ttl = max(5.0, min(600.0, float(os.getenv("PITGUARD_DESIGN_BOOTSTRAP_CACHE_TTL_SECONDS", "90"))))
    except (TypeError, ValueError):
        ttl = 90.0
    return maximum, ttl


def _project_lock(db_path: str, project_id: str) -> Lock:
    key = (db_path, project_id)
    with _CACHE_LOCK:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _PROJECT_LOCKS[key] = lock
        return lock


def _cache_key(repo: ProjectRepository, project_id: str, metadata: dict[str, Any], persisted: dict[str, Any]) -> tuple[str, str, int, int, int]:
    return (
        str(repo.store.db_path),
        project_id,
        int(metadata.get("revision") or 0),
        int(metadata.get("workspaceBytes") or 0),
        int(persisted.get("sessionVersion") or 0),
    )


def _get_cached(key: tuple[str, str, int, int, int]) -> dict[str, Any] | None:
    maximum, ttl = _cache_settings()
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        created, payload = item
        if now - created > ttl:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        while len(_CACHE) > maximum:
            _CACHE.popitem(last=False)
        output = dict(payload)
        output["cache"] = {**dict(payload.get("cache") or {}), "hit": True, "ageSeconds": round(now - created, 3)}
        return output


def _put_cached(key: tuple[str, str, int, int, int], payload: dict[str, Any]) -> None:
    maximum, _ = _cache_settings()
    with _CACHE_LOCK:
        stale = [item for item in _CACHE if item[:2] == key[:2] and item != key]
        for item in stale:
            _CACHE.pop(item, None)
        _CACHE[key] = (time.monotonic(), payload)
        _CACHE.move_to_end(key)
        while len(_CACHE) > maximum:
            _CACHE.popitem(last=False)


def invalidate_design_workspace_bootstrap(project_id: str, *, db_path: str | None = None) -> None:
    with _CACHE_LOCK:
        stale = [
            item for item in _CACHE
            if item[1] == project_id and (db_path is None or item[0] == db_path)
        ]
        for item in stale:
            _CACHE.pop(item, None)


def build_design_workspace_bootstrap(
    repo: ProjectRepository,
    project_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Build all initial retaining-design panels from one bounded workspace load.

    Previous releases opened the same workspace and repeated polygon recognition,
    coordinate/geology audits and topology checks in several concurrent HTTP
    requests.  This single-flight snapshot hydrates and validates the workspace
    once, shares intermediate results, and keeps deep member/audit calculations
    deferred until the user opens the advanced panel.
    """
    metadata = repo.store.get_workspace_metadata(project_id)
    if metadata is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    persisted = repo.store.get_progressive_design_config(project_id)
    key = _cache_key(repo, project_id, metadata, persisted)
    if not force:
        cached = _get_cached(key)
        if cached is not None:
            return cached

    lock = _project_lock(str(repo.store.db_path), project_id)
    with lock:
        if not force:
            cached = _get_cached(key)
            if cached is not None:
                return cached
        started = time.perf_counter()
        project = repo.require_workspace(project_id)
        hydration_ms = (time.perf_counter() - started) * 1000.0

        phase = time.perf_counter()
        if project.excavation and project.excavation.outline.points:
            excavation = project.excavation
            diagnostics = plan_shape_diagnostics(
                list(excavation.outline.points),
                local_pit_count=len(excavation.local_pits or []),
                has_center_island=any(
                    getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
                    for item in (excavation.obstacles or [])
                ),
            )
        else:
            diagnostics = {
                "classification": "missing_excavation",
                "archetype": "missing_excavation",
                "capability": "manual_system_selection",
                "supportedTopologyFamilies": [],
                "alternativeSystems": [],
            }
        shape_ms = (time.perf_counter() - phase) * 1000.0

        phase = time.perf_counter()
        storage_info = repo.store.get_payload_info(project_id) or {}
        resource = dict(storage_info.get("resourcePolicy") or adaptive_resource_policy(role="api"))
        systems = build_support_system_options(project, diagnostics=diagnostics)
        qualification = build_design_qualification(
            project,
            storage_info=storage_info,
            diagnostics=diagnostics,
            systems=systems,
            topology_detail="summary",
        )
        qualification_ms = (time.perf_counter() - phase) * 1000.0

        phase = time.perf_counter()
        config = normalize_progressive_config(project, persisted, systems=systems)
        progressive = build_progressive_design_session(
            project,
            persisted=persisted,
            storage_info=storage_info,
            config=config,
            qualification=qualification,
            systems=systems,
            resource=resource,
        )
        progressive_ms = (time.perf_counter() - phase) * 1000.0
        total_ms = (time.perf_counter() - started) * 1000.0

        try:
            slow_threshold_ms = max(250.0, float(os.getenv("PITGUARD_DESIGN_BOOTSTRAP_SLOW_MS", "3000")))
        except (TypeError, ValueError):
            slow_threshold_ms = 3000.0
        if total_ms >= slow_threshold_ms:
            logger.warning(
                "Slow design workspace bootstrap project=%s revision=%s total_ms=%.1f workspace_mb=%.2f",
                project_id, key[2], total_ms, int(metadata.get("workspaceBytes") or 0) / 1048576.0,
            )

        payload = {
            "projectId": project_id,
            "projectRevision": int(metadata.get("revision") or 0),
            "workspaceBytes": int(metadata.get("workspaceBytes") or 0),
            "generatedAt": _now(),
            "qualification": qualification,
            "progressive": progressive,
            "shapeDiagnostics": diagnostics,
            "systemOptions": systems,
            "storageHealth": storage_info,
            "deferredPanels": [
                "support_designer_audit",
                "support_deep_design",
                "calculation_resource_estimate",
                "artifact_manifest",
            ],
            "performance": {
                "workspaceHydrationMs": round(hydration_ms, 2),
                "shapeRecognitionMs": round(shape_ms, 2),
                "qualificationMs": round(qualification_ms, 2),
                "progressiveSessionMs": round(progressive_ms, 2),
                "totalMs": round(total_ms, 2),
                "workspaceModelCache": "enabled",
                "topologyEvaluation": "summary_on_open_full_on_demand",
            },
            "cache": {"hit": False, "ageSeconds": 0.0, "keyRevision": key[2], "sessionVersion": key[4]},
        }
        _put_cached(key, payload)
        return payload
