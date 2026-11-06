from __future__ import annotations

"""Low-overhead structured runtime diagnostics.

The files in ``runtime/diagnostics`` are deliberately append-only JSONL so a
large engineering task can be inspected even when the worker is force-killed.
No full project object is serialized here; only counters, sizes and resource
snapshots are recorded.
"""

from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.system_resources import memory_debug_snapshot

_LOCK = RLock()
_MAX_BYTES = 16 * 1024 * 1024
_BACKUPS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_root() -> Path:
    db_path = Path(os.getenv("PITGUARD_DB_PATH", Path.cwd() / "runtime" / "pitguard.sqlite3")).expanduser()
    return db_path.parent


def diagnostics_dir() -> Path:
    path = Path(os.getenv("PITGUARD_DIAGNOSTICS_DIR", runtime_root() / "diagnostics"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rotate(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < _MAX_BYTES:
            return
        oldest = path.with_suffix(path.suffix + f".{_BACKUPS}")
        if oldest.exists():
            oldest.unlink()
        for index in range(_BACKUPS - 1, 0, -1):
            source = path.with_suffix(path.suffix + f".{index}")
            if source.exists():
                source.replace(path.with_suffix(path.suffix + f".{index + 1}"))
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def append_event(stream: str, event: str, **fields: Any) -> None:
    if str(os.getenv("PITGUARD_RUNTIME_DIAGNOSTICS", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return
    path = diagnostics_dir() / f"{stream}.jsonl"
    record = {
        "timestamp": _now(),
        "event": event,
        "pid": os.getpid(),
        "processRole": os.getenv("PITGUARD_PROCESS_ROLE", "unknown"),
        **fields,
    }
    try:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        with _LOCK:
            _rotate(path)
            with path.open("a", encoding="utf-8") as output:
                output.write(encoded + "\n")
    except (OSError, TypeError, ValueError):
        pass


def memory_event(stream: str, event: str, **fields: Any) -> dict[str, Any]:
    snapshot = memory_debug_snapshot()
    snapshot["gcGenerationCounts"] = list(gc.get_count())
    append_event(stream, event, **fields, **snapshot)
    return snapshot


def safe_length(value: Any) -> int | None:
    try:
        return len(value)
    except (TypeError, AttributeError):
        return None


def approximate_json_bytes(value: Any, *, maximum_records: int = 200000) -> int | None:
    """Return a diagnostic-only JSON size without allowing unbounded work."""
    try:
        if isinstance(value, (list, tuple, dict)) and len(value) > maximum_records:
            return None
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, MemoryError):
        return None
