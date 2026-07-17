#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("PITGUARD_PROCESS_ROLE", "worker")

from app.services.engineering_templates import ensure_design_basis_defaults
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate V3.46 design-basis defaults without confirming them.")
    parser.add_argument("--db", default=str(ROOT / "runtime" / "pitguard.sqlite3"))
    parser.add_argument("--project-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.environ["PITGUARD_DB_PATH"] = args.db
    repo = ProjectRepository(SQLiteProjectStore(args.db), default_actor="v3.46-migration")
    ids = [args.project_id] if args.project_id else [row.id for row in repo.list_summaries()]
    rows = []
    for project_id in ids:
        project = repo.require(project_id)
        migration = ensure_design_basis_defaults(project)
        if migration.get("changedFields") and not args.dry_run:
            repo.save(project, action="project.v346_design_basis_migration", summary="Added V3.46 design-basis defaults; confirmation remains unchanged")
        rows.append({"projectId": project_id, **migration, "saved": bool(migration.get("changedFields")) and not args.dry_run})
    print(json.dumps({"count": len(rows), "projects": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
