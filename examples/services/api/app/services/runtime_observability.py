from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from typing import Any


class RuntimeObservability:
    def __init__(self, sample_limit: int = 1000) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_monotonic = monotonic()
        self.sample_limit = max(100, sample_limit)
        self._lock = RLock()
        self._request_count = 0
        self._error_count = 0
        self._status_counts: dict[str, int] = defaultdict(int)
        self._path_counts: dict[str, int] = defaultdict(int)
        self._latencies_ms: deque[float] = deque(maxlen=self.sample_limit)

    def record(self, path: str, status_code: int, elapsed_ms: float) -> None:
        with self._lock:
            self._request_count += 1
            self._status_counts[str(status_code)] += 1
            self._path_counts[path] += 1
            self._latencies_ms.append(max(0.0, float(elapsed_ms)))
            if status_code >= 500:
                self._error_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = sorted(self._latencies_ms)
            request_count = self._request_count
            error_count = self._error_count
            status_counts = dict(self._status_counts)
            top_paths = sorted(self._path_counts.items(), key=lambda item: (-item[1], item[0]))[:20]
        def percentile(q: float) -> float | None:
            if not latencies:
                return None
            index = min(len(latencies) - 1, max(0, round((len(latencies) - 1) * q)))
            return round(latencies[index], 3)
        return {
            "startedAt": self.started_at,
            "uptimeSeconds": round(monotonic() - self.started_monotonic, 3),
            "requestCount": request_count,
            "serverErrorCount": error_count,
            "serverErrorRate": round(error_count / request_count, 6) if request_count else 0.0,
            "statusCounts": status_counts,
            "latencyMs": {
                "sampleCount": len(latencies),
                "p50": percentile(0.50),
                "p95": percentile(0.95),
                "p99": percentile(0.99),
                "max": round(max(latencies), 3) if latencies else None,
            },
            "topPaths": [{"path": path, "count": count} for path, count in top_paths],
        }


runtime_observability = RuntimeObservability()
