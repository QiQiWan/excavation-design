#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
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


def backend_lock_metadata() -> tuple[str, list[str]]:
    """Check the project entry; full resolver validation is `uv lock --check`."""
    project = tomllib.loads((ROOT / 'services/api/pyproject.toml').read_text(encoding='utf-8'))['project']
    lock = tomllib.loads((ROOT / 'services/api/uv.lock').read_text(encoding='utf-8'))
    roots = [row for row in lock.get('package', []) if row.get('name') == project['name']]
    if len(roots) != 1:
        return '', ['backend lock must contain exactly one project package']
    root = roots[0]
    issues: list[str] = []
    if lock.get('requires-python') != project.get('requires-python'):
        issues.append('backend lock requires-python differs from pyproject.toml')
    declared_extras = project.get('optional-dependencies', {})
    locked_extras = root.get('optional-dependencies', {})
    if set(declared_extras) != set(locked_extras) or set(declared_extras) != set(root.get('metadata', {}).get('provides-extras', [])):
        issues.append('backend lock extras differ from pyproject.toml')
    def names(requirements: list[str]) -> set[str]:
        return {re.match(r'[A-Za-z0-9_.-]+', requirement).group(0).lower().replace('_', '-')
                for requirement in requirements}
    groups = [('core', project.get('dependencies', []), root.get('dependencies', []))]
    groups += [(name, requirements, locked_extras.get(name, [])) for name, requirements in declared_extras.items()]
    available = {row['name'] for row in lock.get('package', [])}
    for name, declared, locked in groups:
        expected = names(declared)
        actual = {row['name'] for row in locked}
        if expected != actual or not actual.issubset(available):
            issues.append(f'backend lock {name} dependency entries differ from pyproject.toml')
    return str(root.get('version', '')), issues


lock_top, lock_root = lock_versions()
backend_lock, lock_issues = backend_lock_metadata()
versions = {
    'frontend package': frontend_version(),
    'frontend lock': lock_top,
    'frontend lock root': lock_root,
    'backend package': backend_package_version(),
    'backend uv lock': backend_lock,
    'runtime manifest': runtime_version(),
}
unique = {value for value in versions.values() if value}
missing = [name for name, value in versions.items() if not value]
if missing or len(unique) != 1 or lock_issues:
    print('[PitGuard] Release version mismatch detected:', file=sys.stderr)
    for name, value in versions.items():
        print(f'  - {name}: {value or "<missing>"}', file=sys.stderr)
    for issue in lock_issues:
        print(f'  - {issue}', file=sys.stderr)
    sys.exit(1)
print(f'[PitGuard] Release version consistency: {next(iter(unique))}')
