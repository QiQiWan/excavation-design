#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.quality.construction_issue_gate import validate_dxf_package  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a PitGuard R2018 CAD drawing package.")
    parser.add_argument("package", type=Path, help="Unzipped CAD package directory")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    args = parser.parse_args()
    result = validate_dxf_package(args.package)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if result.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
