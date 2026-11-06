#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare bounded PitGuard workspace projections without loading full project JSON into Python.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    db_path = Path(args.database).expanduser().resolve()
    if not db_path.exists():
        print(f"[PitGuard] project database does not exist yet: {db_path}")
        return 0

    os.environ["PITGUARD_DB_PATH"] = str(db_path)
    os.environ["PITGUARD_PROCESS_ROLE"] = "maintenance"
    from app.storage.database import SQLiteProjectStore

    store = SQLiteProjectStore(db_path)
    with sqlite3.connect(db_path, timeout=60.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, payload_bytes, workspace_bytes, revision "
            "FROM projects ORDER BY payload_bytes DESC LIMIT ?",
            (max(1, min(args.top, 200)),),
        ).fetchall()
    if not rows:
        print("[PitGuard] no projects require workspace preparation.")
        return 0

    print("[PitGuard] safe workspace projections prepared:")
    for row in rows:
        full_mb = int(row["payload_bytes"] or 0) / 1048576.0
        workspace_mb = int(row["workspace_bytes"] or 0) / 1048576.0
        print(f"  {row['id']} R{row['revision']} {row['name']}: full={full_mb:.2f} MB workspace={workspace_mb:.2f} MB")
    print("[PitGuard] project opening will use workspace_data; full snapshots remain unchanged for isolated workers and audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
