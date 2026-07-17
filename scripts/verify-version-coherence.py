from __future__ import annotations

import ast
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def python_constant(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
                    return str(value)
    raise RuntimeError(f"{name} not found in {path}")


def main() -> int:
    api_version = python_constant(ROOT / "services" / "api" / "app" / "version.py", "SOFTWARE_VERSION")
    web_metadata = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    marker = json.loads((ROOT / "apps" / "web" / "public" / "pitguard-version.json").read_text(encoding="utf-8"))
    versions = {
        "api": api_version,
        "webPackage": str(web_metadata.get("version") or ""),
        "webMarker": str(marker.get("uiVersion") or ""),
    }
    unique = {value for value in versions.values() if value}
    if len(unique) != 1 or any(not value for value in versions.values()):
        print(f"[PitGuard] version mismatch: {json.dumps(versions, ensure_ascii=False)}", file=sys.stderr)
        return 2
    print(f"[PitGuard] coherent application version: {api_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
