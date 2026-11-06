#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from app.storage.artifact_store import ProjectArtifactStore


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent PitGuard metadata and artifact backup")
    parser.add_argument("--database", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--include-artifacts", action="store_true")
    args = parser.parse_args()
    database = Path(args.database)
    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_copy = destination / f"pitguard_{stamp}.sqlite3"
    with sqlite3.connect(database, timeout=60.0) as source, sqlite3.connect(db_copy, timeout=60.0) as target:
        source.backup(target)
        target.commit()
    artifact_store = ProjectArtifactStore()
    files = [item for item in artifact_store.root.rglob("*.json.gz") if item.is_file()]
    manifest = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "database": {"filename": db_copy.name, "bytes": db_copy.stat().st_size, "sha256": file_sha(db_copy)},
        "artifactRoot": str(artifact_store.root),
        "artifactCount": len(files),
        "artifactBytes": sum(item.stat().st_size for item in files),
        "artifacts": [
            {"path": item.relative_to(artifact_store.root).as_posix(), "bytes": item.stat().st_size, "sha256": file_sha(item)}
            for item in files
        ],
    }
    manifest_path = destination / f"pitguard_{stamp}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = None
    if args.include_artifacts:
        archive_path = destination / f"pitguard_{stamp}_artifacts.tar.gz"
        with tarfile.open(archive_path, "w:gz", compresslevel=3) as archive:
            archive.add(artifact_store.root, arcname="artifacts", recursive=True)
    print(json.dumps({"database": str(db_copy), "manifest": str(manifest_path), "artifactArchive": str(archive_path) if archive_path else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
