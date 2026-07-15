from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import re
from threading import RLock
from time import monotonic
from typing import Any


_DYNAMIC_PARENT_SEGMENTS = {
    "projects": "project_id",
    "tasks": "task_id",
    "calculation-results": "result_id",
    "artifacts": "artifact_id",
    "storage-revisions": "revision",
    "issues": "issue_id",
    "benchmarks": "benchmark_id",
}
_HEXISH_ID = re.compile(r"^(?:[a-z]+-)?[0-9a-f]{8,}$", re.IGNORECASE)
_NUMERIC_ID = re.compile(r"^\d+$")


def normalize_observability_path(path: str) -> str:
    """Collapse request-specific identifiers before aggregation.

    Project/task/result identifiers previously created an unbounded path-count map
    in long-running API processes. Normalizing them keeps metrics useful while
    bounding memory growth and avoiding project identifiers in diagnostics.
    """
    clean = str(path or "/").split("?", 1)[0]
    segments = [segment for segment in clean.split("/") if segment]
    normalized: list[str] = []
    previous = ""
    for segment in segments:
        placeholder = _DYNAMIC_PARENT_SEGMENTS.get(previous)
        if placeholder:
            normalized.append(f":{placeholder}")
        elif _HEXISH_ID.match(segment) or (_NUMERIC_ID.match(segment) and previous in {"revisions", "chunks"}):
            normalized.append(":id")
        else:
            normalized.append(segment)
        previous = segment
    return "/" + "/".join(normalized)


class RuntimeObservability:
    def __init__(self, sample_limit: int = 1000, path_limit: int = 512) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_monotonic = monotonic()
        self.sample_limit = max(100, sample_limit)
        self.path_limit = max(64, path_limit)
        self._lock = RLock()
        self._request_count = 0
        self._server_error_count = 0
        self._client_error_count = 0
        self._slow_request_count = 0
        self._active_request_count = 0
        self._max_active_request_count = 0
        self._status_counts: dict[str, int] = defaultdict(int)
        self._path_counts: dict[str, int] = defaultdict(int)
        self._latencies_ms: deque[float] = deque(maxlen=self.sample_limit)
        self._recent_failures: deque[dict[str, Any]] = deque(maxlen=30)

    def begin(self) -> None:
        with self._lock:
            self._active_request_count += 1
            self._max_active_request_count = max(self._max_active_request_count, self._active_request_count)

    def record(self, path: str, status_code: int, elapsed_ms: float, *, slow_threshold_ms: float = 1200.0) -> None:
        normalized_path = normalize_observability_path(path)
        elapsed = max(0.0, float(elapsed_ms))
        status = int(status_code)
        with self._lock:
            self._active_request_count = max(0, self._active_request_count - 1)
            self._request_count += 1
            self._status_counts[str(status)] += 1
            if normalized_path not in self._path_counts and len(self._path_counts) >= self.path_limit:
                normalized_path = "/:other"
            self._path_counts[normalized_path] += 1
            self._latencies_ms.append(elapsed)
            if 400 <= status < 500:
                self._client_error_count += 1
            if status >= 500:
                self._server_error_count += 1
            if elapsed >= max(1.0, float(slow_threshold_ms)):
                self._slow_request_count += 1
            if status >= 500:
                self._recent_failures.append({
                    "path": normalized_path,
                    "statusCode": status,
                    "elapsedMs": round(elapsed, 3),
                    "recordedAt": datetime.now(timezone.utc).isoformat(),
                })

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = sorted(self._latencies_ms)
            request_count = self._request_count
            server_error_count = self._server_error_count
            client_error_count = self._client_error_count
            slow_request_count = self._slow_request_count
            active_request_count = self._active_request_count
            max_active_request_count = self._max_active_request_count
            status_counts = dict(self._status_counts)
            top_paths = sorted(self._path_counts.items(), key=lambda item: (-item[1], item[0]))[:20]
            recent_failures = list(self._recent_failures)
            path_cardinality = len(self._path_counts)

        def percentile(q: float) -> float | None:
            if not latencies:
                return None
            index = min(len(latencies) - 1, max(0, round((len(latencies) - 1) * q)))
            return round(latencies[index], 3)

        return {
            "startedAt": self.started_at,
            "uptimeSeconds": round(monotonic() - self.started_monotonic, 3),
            "requestCount": request_count,
            "activeRequestCount": active_request_count,
            "maximumConcurrentRequestCount": max_active_request_count,
            "serverErrorCount": server_error_count,
            "serverErrorRate": round(server_error_count / request_count, 6) if request_count else 0.0,
            "clientErrorCount": client_error_count,
            "clientErrorRate": round(client_error_count / request_count, 6) if request_count else 0.0,
            "slowRequestCount": slow_request_count,
            "slowRequestRate": round(slow_request_count / request_count, 6) if request_count else 0.0,
            "statusCounts": status_counts,
            "latencyMs": {
                "sampleCount": len(latencies),
                "p50": percentile(0.50),
                "p95": percentile(0.95),
                "p99": percentile(0.99),
                "max": round(max(latencies), 3) if latencies else None,
            },
            "topPaths": [{"path": path, "count": count} for path, count in top_paths],
            "recentServerFailures": recent_failures[-10:],
            "pathCardinality": path_cardinality,
            "pathAggregationBound": self.path_limit,
        }


runtime_observability = RuntimeObservability()
