#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontend_version() -> str:
    return str(json.loads((ROOT / 'apps/web/package.json').read_text(encoding='utf-8')).get('version', ''))


def lock_versions() -> tuple[str, str]:
    payload = json.loads((ROOT / 'apps/web/package-lock.json').read_text(encoding='utf-8'))
    root_package = payload.get('packages', {}).get('', {})
    return str(payload.get('version', '')), str(root_package.get('version', ''))


def backend_package_version() -> str:
    text = (ROOT / 'services/api/pyproject.toml').read_text(encoding='utf-8')
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ''


def runtime_version() -> str:
    text = (ROOT / 'services/api/app/version.py').read_text(encoding='utf-8')
    match = re.search(r'SOFTWARE_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ''


lock_top, lock_root = lock_versions()
versions = {
    'frontend package': frontend_version(),
    'frontend lock': lock_top,
    'frontend lock root': lock_root,
    'backend package': backend_package_version(),
    'runtime manifest': runtime_version(),
}
unique = {value for value in versions.values() if value}
missing = [name for name, value in versions.items() if not value]
if missing or len(unique) != 1:
    print('[PitGuard] Release version mismatch detected:', file=sys.stderr)
    for name, value in versions.items():
        print(f'  - {name}: {value or "<missing>"}', file=sys.stderr)
    sys.exit(1)
print(f'[PitGuard] Release version consistency: {next(iter(unique))}')
