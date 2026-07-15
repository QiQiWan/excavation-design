from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


ARTIFACT_SCHEMA_VERSION = "1.0"
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    cleaned = _SAFE_TOKEN.sub("-", str(value or "artifact")).strip("-.")
    return cleaned[:96] or "artifact"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _item_count(value: Any) -> int | None:
    if isinstance(value, (list, dict)):
        return len(value)
    return None


class ProjectArtifactStore:
    """Content-addressed project object storage.

    Heavy engineering arrays are immutable gzip JSON objects.  Project snapshots
    retain only small summaries and references.  A worker can rehydrate the full
    logical project while the API remains on the bounded working set.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        default = Path(os.getenv("PITGUARD_DB_PATH", Path(__file__).resolve().parents[2] / "pitguard.sqlite3")).parent / "artifacts"
        self.root = Path(root or os.getenv("PITGUARD_ARTIFACT_ROOT", str(default))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        path = (self.root / _safe(project_id)).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Invalid project artifact path")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(
        self,
        project_id: str,
        kind: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind_token = _safe(kind)
        target_dir = (self.root / _safe(project_id) / kind_token).resolve()
        if self.root not in target_dir.parents:
            raise ValueError("Invalid artifact destination")
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target_dir / f".tmp-{uuid4().hex}.json.gz"
        digest_builder = hashlib.sha256()
        logical_bytes = 0
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with gzip.open(temporary, "wb", compresslevel=3) as handle:
            for text in encoder.iterencode(value):
                block = text.encode("utf-8")
                digest_builder.update(block)
                logical_bytes += len(block)
                handle.write(block)
        digest = digest_builder.hexdigest()
        relative = Path(_safe(project_id)) / kind_token / f"{digest}.json.gz"
        destination = (self.root / relative).resolve()
        if destination.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(destination)
        stat = destination.stat()
        artifact_id = f"artifact-{digest[:20]}"
        return {
            "artifactId": artifact_id,
            "schemaVersion": ARTIFACT_SCHEMA_VERSION,
            "projectId": project_id,
            "kind": kind,
            "sha256": digest,
            "relativePath": relative.as_posix(),
            "contentType": "application/json",
            "contentEncoding": "gzip",
            "logicalBytes": logical_bytes,
            "storedBytes": int(stat.st_size),
            "itemCount": _item_count(value),
            "createdAt": _now(),
            "metadata": dict(metadata or {}),
        }

    def resolve(self, ref: dict[str, Any]) -> Path:
        relative = Path(str(ref.get("relativePath") or ""))
        path = (self.root / relative).resolve()
        if self.root not in path.parents or not path.is_file():
            raise FileNotFoundError(str(relative))
        return path

    def read_json(self, ref: dict[str, Any]) -> Any:
        path = self.resolve(ref)
        with gzip.open(path, "rb") as handle:
            raw = handle.read()
        digest = hashlib.sha256(raw).hexdigest()
        expected = str(ref.get("sha256") or "")
        if expected and digest != expected:
            raise RuntimeError(f"Artifact checksum mismatch: {path.name}")
        return json.loads(raw)

    def list_existing(self, refs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for ref in refs:
            item = dict(ref)
            try:
                path = self.resolve(item)
                item["available"] = True
                item["storedBytes"] = path.stat().st_size
            except FileNotFoundError:
                item["available"] = False
            output.append(item)
        return output

    def delete_project(self, project_id: str) -> int:
        directory = self.root / _safe(project_id)
        if not directory.exists():
            return 0
        count = sum(1 for item in directory.rglob("*") if item.is_file())
        shutil.rmtree(directory, ignore_errors=True)
        return count


def artifact_refs(project: dict[str, Any]) -> list[dict[str, Any]]:
    advanced = project.get("advancedEngineering") or {}
    if not isinstance(advanced, dict):
        return []
    storage = advanced.get("artifactStorage") or {}
    if not isinstance(storage, dict):
        return []
    return [dict(item) for item in list(storage.get("artifacts") or []) if isinstance(item, dict)]


def _set_artifact_refs(project: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    advanced = dict(project.get("advancedEngineering") or {})
    logical = sum(int(item.get("logicalBytes") or 0) for item in refs)
    stored = sum(int(item.get("storedBytes") or 0) for item in refs)
    advanced["artifactStorage"] = {
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "mode": "external_content_addressed",
        "artifactCount": len(refs),
        "logicalBytes": logical,
        "storedBytes": stored,
        "compressionRatio": round(stored / max(logical, 1), 6),
        "artifacts": refs,
        "updatedAt": _now(),
    }
    project["advancedEngineering"] = advanced


def _compact_calculation_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    compact["stageResults"] = []
    compact["reportDiagramData"] = {}
    compact["drawingSheets"] = []
    compact["supportLayoutRepair"] = None
    compact["stabilityDetailedResult"] = None
    assurance = dict(compact.get("calculationAssurance") or {})
    assurance["externalized"] = True
    compact["calculationAssurance"] = assurance
    return compact


def _chunk(values: list[Any], size: int) -> Iterable[tuple[int, list[Any]]]:
    for offset in range(0, len(values), size):
        yield offset // size, values[offset:offset + size]


def externalize_project_payload(
    project: dict[str, Any],
    store: ProjectArtifactStore,
    *,
    threshold_bytes: int | None = None,
) -> dict[str, Any]:
    """Mutate and return a project snapshot with heavy fields externalized."""
    project_id = str(project.get("id") or "project")
    threshold = threshold_bytes or max(256 * 1024, int(float(os.getenv("PITGUARD_ARTIFACT_THRESHOLD_MB", "1")) * 1024 * 1024))
    existing = artifact_refs(project)
    refs_by_key = {str(item.get("storageKey") or ""): item for item in existing if item.get("storageKey")}
    new_refs: list[dict[str, Any]] = []

    def preserve(storage_key: str) -> None:
        if storage_key in refs_by_key:
            new_refs.append(refs_by_key[storage_key])

    def store_value(storage_key: str, kind: str, value: Any, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        ref = store.write_json(project_id, kind, value, metadata=metadata)
        ref["storageKey"] = storage_key
        new_refs.append(ref)
        return ref

    # Calculation result stage arrays are split so the browser can load one
    # result chunk at a time and the worker can reconstruct the complete result.
    results = list(project.get("calculationResults") or [])
    compact_results: list[Any] = []
    if results:
        for result in results:
            if not isinstance(result, dict):
                compact_results.append(result)
                continue
            result_id = str(result.get("id") or f"result-{len(compact_results)}")
            stages = list(result.get("stageResults") or [])
            if stages:
                for chunk_index, values in _chunk(stages, max(25, int(os.getenv("PITGUARD_STAGE_RESULT_CHUNK_SIZE", "100")))):
                    store_value(
                        f"calculation:{result_id}:stages:{chunk_index}",
                        "calculation-stage-results",
                        values,
                        metadata={"resultId": result_id, "chunkIndex": chunk_index, "recordCount": len(values)},
                    )
            else:
                for key in refs_by_key:
                    if key.startswith(f"calculation:{result_id}:stages:"):
                        preserve(key)
            details = {
                "reportDiagramData": result.get("reportDiagramData") or {},
                "drawingSheets": result.get("drawingSheets") or [],
                "supportLayoutRepair": result.get("supportLayoutRepair"),
                "stabilityDetailedResult": result.get("stabilityDetailedResult"),
            }
            if any(details.values()):
                store_value(
                    f"calculation:{result_id}:details",
                    "calculation-result-details",
                    details,
                    metadata={"resultId": result_id},
                )
            else:
                preserve(f"calculation:{result_id}:details")
            compact_results.append(_compact_calculation_result(result))
        project["calculationResults"] = compact_results
    elif bool((project.get("advancedEngineering") or {}).get("requiresRecalculation")):
        # A design edit intentionally invalidated calculations. Do not retain
        # stale calculation artifacts in the current revision.
        pass
    else:
        for key in refs_by_key:
            if key.startswith("calculation:"):
                preserve(key)

    geological = project.get("geologicalModel")
    if isinstance(geological, dict):
        vtu = geological.get("vtuMesh")
        if vtu:
            store_value("geology:vtu", "geology-vtu-mesh", vtu)
            geological["vtuMesh"] = None
        else:
            preserve("geology:vtu")
        for field, kind in (("surfaces", "geology-surfaces"), ("volumes", "geology-volumes")):
            value = geological.get(field)
            if value:
                store_value(f"geology:{field}", kind, value, metadata={"recordCount": len(value) if isinstance(value, list) else None})
                geological[field] = []
            else:
                preserve(f"geology:{field}")

    retaining = project.get("retainingSystem")
    if isinstance(retaining, dict):
        repair = retaining.get("supportLayoutRepair")
        if isinstance(repair, dict):
            bundle = {
                "candidateFullCalculations": list(repair.get("candidateFullCalculations") or []),
                "candidateCalculationsById": {
                    str(item.get("id") or index): item.get("fullCalculation")
                    for index, item in enumerate(list(repair.get("candidates") or []))
                    if isinstance(item, dict) and item.get("fullCalculation")
                },
            }
            if bundle["candidateFullCalculations"] or bundle["candidateCalculationsById"]:
                store_value("support:candidate-calculations", "support-candidate-calculations", bundle)
                repair["candidateFullCalculations"] = []
                for item in list(repair.get("candidates") or []):
                    if isinstance(item, dict):
                        item["fullCalculation"] = {}
            else:
                preserve("support:candidate-calculations")
        rebar = retaining.get("rebarDesignScheme")
        if isinstance(rebar, dict):
            keys = ("bars", "barInstances", "fullGeometry", "manufacturingRows", "bbsRows")
            bundle = {key: rebar.get(key) for key in keys if rebar.get(key)}
            if bundle:
                store_value("rebar:geometry", "rebar-geometry", bundle)
                for key in bundle:
                    rebar[key] = []
            else:
                preserve("rebar:geometry")

    advanced = dict(project.get("advancedEngineering") or {})
    heavy_keys = (
        "latestSuite", "industrialDetailing", "qualificationSuite", "detailGeometryPatches",
        "fullRebarGeometry", "manufacturingData", "renderCache", "ifcEntityCache",
        "calculationResultArchive",
    )
    advanced_bundle = {key: advanced.get(key) for key in heavy_keys if advanced.get(key)}
    if advanced_bundle:
        store_value("advanced:heavy", "advanced-engineering-heavy", advanced_bundle)
        for key in advanced_bundle:
            advanced.pop(key, None)
    else:
        preserve("advanced:heavy")
    project["advancedEngineering"] = advanced

    monitoring = list(project.get("monitoringRecords") or [])
    if len(monitoring) > 500:
        for chunk_index, values in _chunk(monitoring, 1000):
            store_value(
                f"monitoring:records:{chunk_index}",
                "monitoring-records",
                values,
                metadata={"chunkIndex": chunk_index, "recordCount": len(values)},
            )
        project["monitoringRecords"] = monitoring[-500:]
    else:
        for key in refs_by_key:
            if key.startswith("monitoring:records:"):
                preserve(key)

    # Preserve unrelated refs and deduplicate by storageKey.
    managed_prefixes = ("calculation:", "geology:", "support:", "rebar:", "advanced:", "monitoring:")
    for ref in existing:
        key = str(ref.get("storageKey") or "")
        if key and not key.startswith(managed_prefixes):
            new_refs.append(ref)
    deduplicated: dict[str, dict[str, Any]] = {}
    for ref in new_refs:
        deduplicated[str(ref.get("storageKey") or ref.get("artifactId"))] = ref
    _set_artifact_refs(project, list(deduplicated.values()))
    return project


def rehydrate_project_payload(project: dict[str, Any], store: ProjectArtifactStore) -> dict[str, Any]:
    refs = artifact_refs(project)
    if not refs:
        return project
    by_key = {str(item.get("storageKey") or ""): item for item in refs}

    result_map = {
        str(item.get("id") or ""): item
        for item in list(project.get("calculationResults") or [])
        if isinstance(item, dict)
    }
    for key, ref in by_key.items():
        if key.startswith("calculation:"):
            parts = key.split(":")
            if len(parts) < 3:
                continue
            result_id = parts[1]
            result = result_map.get(result_id)
            if result is None:
                continue
            value = store.read_json(ref)
            if parts[2] == "stages":
                result.setdefault("stageResults", []).extend(list(value or []))
            elif parts[2] == "details" and isinstance(value, dict):
                result.update(value)
        elif key == "geology:vtu":
            if isinstance(project.get("geologicalModel"), dict):
                project["geologicalModel"]["vtuMesh"] = store.read_json(ref)
        elif key in {"geology:surfaces", "geology:volumes"}:
            if isinstance(project.get("geologicalModel"), dict):
                project["geologicalModel"][key.split(":", 1)[1]] = store.read_json(ref)
        elif key == "support:candidate-calculations":
            retaining = project.get("retainingSystem") or {}
            repair = retaining.get("supportLayoutRepair") if isinstance(retaining, dict) else None
            if isinstance(repair, dict):
                bundle = store.read_json(ref) or {}
                repair["candidateFullCalculations"] = list(bundle.get("candidateFullCalculations") or [])
                mapping = dict(bundle.get("candidateCalculationsById") or {})
                for index, item in enumerate(list(repair.get("candidates") or [])):
                    if isinstance(item, dict):
                        candidate_id = str(item.get("id") or index)
                        if candidate_id in mapping:
                            item["fullCalculation"] = mapping[candidate_id]
        elif key == "rebar:geometry":
            retaining = project.get("retainingSystem") or {}
            rebar = retaining.get("rebarDesignScheme") if isinstance(retaining, dict) else None
            if isinstance(rebar, dict):
                rebar.update(dict(store.read_json(ref) or {}))
        elif key == "advanced:heavy":
            advanced = dict(project.get("advancedEngineering") or {})
            advanced.update(dict(store.read_json(ref) or {}))
            project["advancedEngineering"] = advanced

    monitoring_refs = sorted(
        ((key, ref) for key, ref in by_key.items() if key.startswith("monitoring:records:")),
        key=lambda item: item[0],
    )
    if monitoring_refs:
        records: list[Any] = []
        for _, ref in monitoring_refs:
            records.extend(list(store.read_json(ref) or []))
        project["monitoringRecords"] = records
    return project
