#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.storage.artifact_store import ProjectArtifactStore, artifact_refs


def main() -> None:
    parser = argparse.ArgumentParser(description="Find or remove unreferenced PitGuard artifact objects")
    parser.add_argument("--database", required=True)
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    db = Path(args.database)
    store = ProjectArtifactStore()
    referenced: set[Path] = set()
    with sqlite3.connect(db, timeout=60.0) as conn:
        for table in ("projects", "project_revisions"):
            try:
                rows = conn.execute(f"SELECT data FROM {table}")
            except sqlite3.OperationalError:
                continue
            for (raw,) in rows:
                try:
                    project = json.loads(str(raw))
                except json.JSONDecodeError:
                    continue
                for ref in artifact_refs(project):
                    relative = Path(str(ref.get("relativePath") or ""))
                    if relative:
                        referenced.add(relative)
    files = [item for item in store.root.rglob("*.json.gz") if item.is_file()]
    orphaned = [item for item in files if item.relative_to(store.root) not in referenced]
    bytes_orphaned = sum(item.stat().st_size for item in orphaned)
    if args.delete:
        for item in orphaned:
            item.unlink(missing_ok=True)
        for directory in sorted((item for item in store.root.rglob("*") if item.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
    print(json.dumps({
        "referenced": len(referenced),
        "files": len(files),
        "orphaned": len(orphaned),
        "orphanedBytes": bytes_orphaned,
        "deleted": len(orphaned) if args.delete else 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
