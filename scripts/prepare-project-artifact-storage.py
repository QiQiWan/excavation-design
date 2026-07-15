#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.storage.database import SQLiteProjectStore


def migrate(database: Path, *, vacuum: bool = False, include_revisions: bool = False) -> dict[str, object]:
    """Run memory-aware project storage maintenance.

    Each project is preflighted against current worker headroom. Large snapshots
    that cannot be safely hydrated receive a SQLite-only workspace rebuild and
    are left for a later high-headroom worker instead of risking startup OOM.
    """
    store = SQLiteProjectStore(database)
    with store._connect() as conn:
        project_ids = [str(row[0]) for row in conn.execute("SELECT id FROM projects ORDER BY id").fetchall()]
    stats: dict[str, object] = {
        "projects": 0,
        "fullExternalizations": 0,
        "workspaceOnlyRebuilds": 0,
        "payloadReductionBytes": 0,
        "workspaceReductionBytes": 0,
        "deferredProjects": [],
    }
    for project_id in project_ids:
        result = store.compact_project_storage(project_id, include_revisions=include_revisions)
        stats["projects"] = int(stats["projects"]) + 1
        stats["payloadReductionBytes"] = int(stats["payloadReductionBytes"]) + int(result.get("payloadReductionBytes") or 0)
        stats["workspaceReductionBytes"] = int(stats["workspaceReductionBytes"]) + int(result.get("workspaceReductionBytes") or 0)
        if result.get("mode") == "full_externalization":
            stats["fullExternalizations"] = int(stats["fullExternalizations"]) + 1
        else:
            stats["workspaceOnlyRebuilds"] = int(stats["workspaceOnlyRebuilds"]) + 1
            deferred = list(stats["deferredProjects"])
            deferred.append(project_id)
            stats["deferredProjects"] = deferred
    with sqlite3.connect(database, timeout=60.0) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if vacuum:
            conn.execute("VACUUM")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory-aware PitGuard project artifact maintenance")
    parser.add_argument("--database", required=True)
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--include-revisions", action="store_true")
    args = parser.parse_args()
    stats = migrate(Path(args.database), vacuum=args.vacuum, include_revisions=args.include_revisions)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
