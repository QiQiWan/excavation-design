#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.reports.docx_report import export_docx_report
from app.schemas.domain import Project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    project = Project.model_validate_json(args.project_json.read_text(encoding="utf-8"))
    path = export_docx_report(project, args.output_dir)
    print(path)


if __name__ == "__main__":
    main()
