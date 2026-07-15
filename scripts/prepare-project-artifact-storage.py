#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from app.storage.artifact_store import ProjectArtifactStore, artifact_refs, externalize_project_payload
from app.storage.database import SQLiteProjectStore, _canonical_json, _compact_project_for_workspace, _aggressively_compact_workspace, _workspace_limit_bytes


def migrate(database: Path, *, vacuum: bool = False) -> dict[str, int]:
    store = SQLiteProjectStore(database)
    artifact_store = ProjectArtifactStore()
    stats = {"projects": 0, "revisions": 0, "artifacts": 0, "externalBytes": 0}
    with store._connect() as conn:  # maintenance-only script
        project_ids = [str(row[0]) for row in conn.execute("SELECT id FROM projects ORDER BY id").fetchall()]
        for project_id in project_ids:
            row = conn.execute("SELECT id, data FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                continue
            project = json.loads(str(row["data"]))
            project = externalize_project_payload(project, artifact_store)
            encoded = _canonical_json(project)
            workspace = _compact_project_for_workspace(project)
            workspace_encoded = _canonical_json(workspace)
            if len(workspace_encoded.encode("utf-8")) > _workspace_limit_bytes():
                workspace_encoded = _canonical_json(_aggressively_compact_workspace(workspace))
            refs = artifact_refs(project)
            conn.execute(
                "UPDATE projects SET data=?, content_hash=?, workspace_data=?, payload_bytes=?, workspace_bytes=?, external_bytes=?, artifact_count=? WHERE id=?",
                (
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    workspace_encoded,
                    len(encoded.encode("utf-8")),
                    len(workspace_encoded.encode("utf-8")),
                    sum(int(item.get("storedBytes") or 0) for item in refs),
                    len(refs),
                    str(row["id"]),
                ),
            )
            stats["projects"] += 1
            stats["artifacts"] += len(refs)
            stats["externalBytes"] += sum(int(item.get("storedBytes") or 0) for item in refs)
            conn.commit()

        revision_keys = [(str(row[0]), int(row[1])) for row in conn.execute("SELECT project_id, revision FROM project_revisions ORDER BY project_id, revision").fetchall()]
        for project_id, revision in revision_keys:
            row = conn.execute("SELECT project_id, revision, data FROM project_revisions WHERE project_id = ? AND revision = ?", (project_id, revision)).fetchone()
            if row is None:
                continue
            project = json.loads(str(row["data"]))
            project = externalize_project_payload(project, artifact_store)
            encoded = _canonical_json(project)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            conn.execute(
                "UPDATE project_revisions SET data=?, content_hash=? WHERE project_id=? AND revision=?",
                (encoded, digest, str(row["project_id"]), int(row["revision"])),
            )
            stats["revisions"] += 1
            if stats["revisions"] % 10 == 0:
                conn.commit()
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if vacuum:
            conn.execute("VACUUM")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Externalize PitGuard heavy project datasets")
    parser.add_argument("--database", required=True)
    parser.add_argument("--vacuum", action="store_true")
    args = parser.parse_args()
    stats = migrate(Path(args.database), vacuum=args.vacuum)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
