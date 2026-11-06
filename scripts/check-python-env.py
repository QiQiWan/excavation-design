#!/usr/bin/env python3
"""Check PitGuard backend dependencies in the currently active Python environment.

The script reads services/api/pyproject.toml, maps distribution names to import
modules, and reports missing or broken imports. It never installs packages.
"""
from __future__ import annotations

import argparse
import importlib
import json
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any

IMPORT_NAME_OVERRIDES = {
    "python-multipart": "multipart",
    "python-docx": "docx",
    "uvicorn": "uvicorn",
    "uvicorn[standard]": "uvicorn",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dependencies(pyproject: Path) -> list[str]:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(payload.get("project", {}).get("dependencies", []))


def distribution_name(requirement: str) -> str:
    token = requirement.strip().split(";", 1)[0].strip()
    for marker in (">=", "<=", "==", "~=", "!=", ">", "<"):
        token = token.split(marker, 1)[0].strip()
    return token


def import_name(requirement: str) -> str:
    dist = distribution_name(requirement)
    base = dist.split("[", 1)[0].lower()
    if dist.lower() in IMPORT_NAME_OVERRIDES:
        return IMPORT_NAME_OVERRIDES[dist.lower()]
    if base in IMPORT_NAME_OVERRIDES:
        return IMPORT_NAME_OVERRIDES[base]
    return base.replace("-", "_")


def inspect_environment(pyproject: Path) -> dict[str, Any]:
    dependencies = load_dependencies(pyproject)
    checks: list[dict[str, str]] = []
    missing: list[str] = []
    for requirement in dependencies:
        module = import_name(requirement)
        status = "pass"
        error = ""
        try:
            importlib.import_module(module)
        except Exception as exc:  # broken binary wheels must be treated as unavailable
            status = "missing_or_broken"
            error = f"{type(exc).__name__}: {exc}"
            missing.append(requirement)
        checks.append({
            "requirement": requirement,
            "distribution": distribution_name(requirement),
            "importName": module,
            "status": status,
            "error": error,
        })
    quoted = " ".join(shlex.quote(item) for item in missing)
    install_command = f"{shlex.quote(sys.executable)} -m pip install {quoted}" if missing else ""
    editable_command = f"{shlex.quote(sys.executable)} -m pip install -e {shlex.quote(str(pyproject.parent))}"
    return {
        "pythonExecutable": sys.executable,
        "pythonVersion": ".".join(map(str, sys.version_info[:3])),
        "pyproject": str(pyproject),
        "status": "pass" if not missing else "fail",
        "missingRequirements": missing,
        "checks": checks,
        "installCommand": install_command,
        "editableInstallCommand": editable_command,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, default=project_root() / "services" / "api" / "pyproject.toml")
    parser.add_argument("--format", choices=("text", "json", "missing", "install-command", "editable-command"), default="text")
    args = parser.parse_args()
    report = inspect_environment(args.pyproject.resolve())
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.format == "missing":
        print("\n".join(report["missingRequirements"]))
    elif args.format == "install-command":
        print(report["installCommand"])
    elif args.format == "editable-command":
        print(report["editableInstallCommand"])
    else:
        print(f"Python: {report['pythonExecutable']} ({report['pythonVersion']})")
        if report["status"] == "pass":
            print("PitGuard backend dependencies: OK")
        else:
            print("Missing or broken backend dependencies:")
            for item in report["checks"]:
                if item["status"] != "pass":
                    print(f"  - {item['requirement']} (import {item['importName']}): {item['error']}")
            print("Install command:")
            print(f"  {report['installCommand']}")
            print("Or install the locked project dependencies:")
            print(f"  {report['editableInstallCommand']}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
