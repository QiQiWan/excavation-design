from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.contracts.storage_status import StorageStatus, classify_storage_status
from app.services.system_resources import physical_memory_bytes, process_effective_memory_bytes, process_rss_bytes


def _read_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _proc_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as stream:
            for line in stream:
                key, _, rest = line.partition(":")
                if key not in {"MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached"}:
                    continue
                fields = rest.strip().split()
                if not fields:
                    continue
                values[key] = int(fields[0]) * 1024
    except OSError:
        pass
    if "MemAvailable" not in values:
        values["MemAvailable"] = (
            values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
        )
    return values


def _process_rss_bytes() -> int:
    return int(process_rss_bytes())


def _cgroup_memory() -> tuple[int | None, int | None]:
    # cgroup v2
    limit = _read_int("/sys/fs/cgroup/memory.max")
    current = _read_int("/sys/fs/cgroup/memory.current")
    if limit is not None or current is not None:
        return limit, current
    # cgroup v1
    limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    current = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    return limit, current


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_optional_mb(name: str) -> int | None:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(1, int(value * 1024 * 1024))


def runtime_memory_snapshot() -> dict[str, Any]:
    host_total, host_available = physical_memory_bytes()
    host_total = int(host_total or 0)
    host_available = int(host_available or 0)
    cgroup_limit, cgroup_current = _cgroup_memory()
    effective_total = host_total
    if cgroup_limit and (not effective_total or cgroup_limit < effective_total):
        effective_total = cgroup_limit
    cgroup_available = None
    if cgroup_limit is not None and cgroup_current is not None:
        cgroup_available = max(0, cgroup_limit - cgroup_current)
    effective_available = host_available
    if cgroup_available is not None and (not effective_available or cgroup_available < effective_available):
        effective_available = cgroup_available
    if effective_total <= 0:
        effective_total = max(effective_available, 8 * 1024**3)
    if effective_available <= 0:
        effective_available = max(512 * 1024**2, effective_total // 2)
    db_path = Path(os.getenv("PITGUARD_DB_PATH", Path.cwd() / "pitguard.sqlite3")).expanduser()
    disk_root = db_path.parent if db_path.parent.exists() else Path.cwd()
    try:
        disk = shutil.disk_usage(disk_root)
        disk_total, disk_used, disk_free = int(disk.total), int(disk.used), int(disk.free)
    except OSError:
        disk_total = disk_used = disk_free = 0
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0
    return {
        "hostTotalBytes": host_total,
        "hostAvailableBytes": host_available,
        "cgroupLimitBytes": cgroup_limit,
        "cgroupCurrentBytes": cgroup_current,
        "effectiveTotalBytes": effective_total,
        "effectiveAvailableBytes": min(effective_available, effective_total),
        "processRssBytes": _process_rss_bytes(),
        "processEffectiveBytes": int(process_effective_memory_bytes()),
        "cpuCount": max(1, os.cpu_count() or 1),
        "loadAverage1m": round(float(load1), 3),
        "loadAverage5m": round(float(load5), 3),
        "loadAverage15m": round(float(load15), 3),
        "diskRoot": str(disk_root),
        "diskTotalBytes": disk_total,
        "diskUsedBytes": disk_used,
        "diskFreeBytes": disk_free,
    }


def adaptive_resource_policy(*, role: str | None = None) -> dict[str, Any]:
    """Derive runtime limits from current host/cgroup headroom.

    Environment values remain hard caps or explicit overrides. The default mode
    intentionally avoids a fixed project-size threshold because JSON parsing,
    Pydantic hydration and result serialization need several copies of the raw
    document and the safe payload size therefore depends on current headroom.
    """
    snapshot = runtime_memory_snapshot()
    effective_total = int(snapshot["effectiveTotalBytes"])
    available = int(snapshot["effectiveAvailableBytes"])
    rss = int(snapshot["processRssBytes"])
    process_effective = int(snapshot.get("processEffectiveBytes") or rss)
    selected_role = str(role or os.getenv("PITGUARD_PROCESS_ROLE", "api")).strip().lower() or "api"
    mode = str(os.getenv("PITGUARD_RESOURCE_POLICY_MODE", "adaptive")).strip().lower()

    reserve_override = _env_optional_mb("PITGUARD_SYSTEM_MEMORY_RESERVE_MB")
    reserve = reserve_override if reserve_override is not None else int(
        max(256 * 1024**2, min(8 * 1024**3, effective_total * 0.16))
    )
    reserve = min(reserve, max(128 * 1024**2, int(effective_total * 0.45)))
    usable = max(0, available - reserve)

    disk_total = int(snapshot.get("diskTotalBytes") or 0)
    disk_free = int(snapshot.get("diskFreeBytes") or 0)
    disk_reserve_override = _env_optional_mb("PITGUARD_DISK_RESERVE_MB")
    disk_reserve = disk_reserve_override if disk_reserve_override is not None else int(
        max(2 * 1024**3, min(20 * 1024**3, disk_total * 0.08 if disk_total else 2 * 1024**3))
    )
    if disk_total:
        disk_reserve = min(disk_reserve, max(512 * 1024**2, int(disk_total * 0.45)))
    disk_usable = max(0, disk_free - disk_reserve)

    amplification = _env_float("PITGUARD_API_JSON_AMPLIFICATION", 5.5, 2.5, 12.0)
    api_fraction = _env_float("PITGUARD_API_FULL_LOAD_HEADROOM_FRACTION", 0.42, 0.10, 0.80)
    hard_cap = _env_optional_mb("PITGUARD_API_FULL_PROJECT_HARD_CAP_MB") or 2048 * 1024**2
    legacy_override = _env_optional_mb("PITGUARD_API_FULL_PROJECT_LIMIT_MB")
    if mode == "fixed" and legacy_override is not None:
        full_limit = legacy_override
    else:
        derived = int((usable * api_fraction) / amplification)
        floor = 32 * 1024**2 if effective_total < 8 * 1024**3 else 64 * 1024**2
        full_limit = max(floor, min(hard_cap, derived))
        # Existing deployments use this variable as an administrative ceiling.
        # Adaptive sizing may lower the limit under pressure, but must never
        # silently exceed an explicit operator-configured cap.
        if legacy_override is not None:
            full_limit = min(full_limit, legacy_override)

    workspace_fraction = _env_float("PITGUARD_WORKSPACE_HEADROOM_FRACTION", 0.08, 0.02, 0.25)
    workspace_hard_cap = _env_optional_mb("PITGUARD_WORKSPACE_PAYLOAD_HARD_CAP_MB") or 256 * 1024**2
    workspace_override = _env_optional_mb("PITGUARD_WORKSPACE_PAYLOAD_LIMIT_MB")
    workspace_limit = max(8 * 1024**2, min(workspace_hard_cap, int(max(available, 1) * workspace_fraction)))
    if workspace_override is not None:
        workspace_limit = min(workspace_limit, workspace_override)

    worker_hard_override = _env_optional_mb("PITGUARD_WORKER_RSS_HARD_LIMIT_MB")
    worker_soft_override = _env_optional_mb("PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB")
    worker_max_override = _env_optional_mb("PITGUARD_WORKER_MEMORY_MAX_MB")
    # Engineering search must leave enough memory for the desktop, database and
    # browser. The old 82% host-memory rule allowed a 32 GB workstation worker to
    # grow beyond 18 GB before intervention. Use a conservative default cap and
    # permit administrators to raise it explicitly for dedicated servers.
    absolute_worker_cap = max(384 * 1024**2, effective_total - max(1024 * 1024**2, reserve))
    default_cap_override = _env_optional_mb("PITGUARD_WORKER_DEFAULT_HARD_CAP_MB")
    default_desktop_cap = default_cap_override if default_cap_override is not None else min(
        6 * 1024**3, max(2 * 1024**3, int(effective_total * 0.20))
    )
    dynamic_hard = min(
        int(effective_total * 0.45),
        process_effective + int(usable * 0.50),
        absolute_worker_cap,
        default_desktop_cap,
    )
    minimum_worker_hard = min(2 * 1024**3, absolute_worker_cap, default_desktop_cap)
    worker_hard = min(max(minimum_worker_hard, dynamic_hard), absolute_worker_cap, default_desktop_cap)
    if worker_hard_override is not None:
        worker_hard = min(worker_hard, worker_hard_override)
    if worker_max_override is not None:
        worker_hard = min(worker_hard, worker_max_override)
    worker_soft = min(max(512 * 1024**2, int(worker_hard * 0.70)), worker_hard)
    if worker_soft_override is not None:
        worker_soft = min(worker_soft, worker_soft_override)

    cpu_count = max(1, int(snapshot.get("cpuCount") or os.cpu_count() or 1))
    load_ratio = float(snapshot.get("loadAverage1m") or 0.0) / max(cpu_count, 1)
    heavy_concurrency_override = str(os.getenv("PITGUARD_HEAVY_TASK_CONCURRENCY", "")).strip()
    if heavy_concurrency_override:
        try:
            heavy_concurrency = max(1, min(4, int(heavy_concurrency_override)))
        except ValueError:
            heavy_concurrency = 1
    else:
        per_heavy = max(3 * 1024**3, int(worker_soft * 0.65))
        cpu_parallel_cap = max(1, cpu_count // 4)
        if load_ratio >= 0.85:
            cpu_parallel_cap = 1
        elif load_ratio >= 0.65:
            cpu_parallel_cap = min(cpu_parallel_cap, 2)
        heavy_concurrency = 1 if selected_role in {"worker", "api"} else max(1, min(2, usable // max(per_heavy, 1), cpu_parallel_cap))

    storage_compaction_allowed = disk_usable >= max(1024 * 1024**2, int(hard_cap * 0.35))

    return {
        "mode": mode,
        "role": selected_role,
        **snapshot,
        "reserveBytes": reserve,
        "usableHeadroomBytes": usable,
        "diskReserveBytes": disk_reserve,
        "diskUsableBytes": disk_usable,
        "storageCompactionAllowed": storage_compaction_allowed,
        "cpuLoadRatio": round(load_ratio, 4),
        "apiJsonAmplification": amplification,
        "apiFullLoadLimitBytes": int(full_limit),
        "workspaceLimitBytes": int(workspace_limit),
        "workerSoftLimitBytes": int(worker_soft),
        "workerHardLimitBytes": int(worker_hard),
        "workerDefaultHardCapBytes": int(default_desktop_cap),
        "recommendedHeavyConcurrency": int(heavy_concurrency),
        "workspaceFirst": True,
        "workerFullHydrationAllowed": usable >= 1024 * 1024**2,
        "policyExplanation": (
            "网页仅加载工作区投影；完整工程对象由独立 worker 按当前可用内存动态评估后加载。"
            "阈值由有效物理/容器内存、系统保留量、当前进程RSS/私有提交量、CPU负载、磁盘余量和JSON放大系数共同确定；"
            "默认worker最多使用主机内存20%且不超过6GB，专用服务器可通过环境变量显式提高。"
        ),
    }


def classify_payload(payload_bytes: int, *, policy: dict[str, Any] | None = None) -> StorageStatus:
    resource = policy or adaptive_resource_policy()
    return classify_storage_status(payload_bytes, int(resource.get("apiFullLoadLimitBytes") or 1))


def mb(value: int | float | None) -> float:
    return round(float(value or 0) / 1048576.0, 2)
