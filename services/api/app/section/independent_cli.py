"""A process boundary around the optional native OpenSees backend."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from app.section.independent import solve_stages
    result = solve_stages(json.loads(Path(args.input).read_text(encoding='utf-8')))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
