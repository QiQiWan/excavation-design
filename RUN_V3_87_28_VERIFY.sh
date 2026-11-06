#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 scripts/verify-release-consistency.py
PYTHONPATH="$(pwd)/services/api" python3 -m pytest -q verification/tests
printf '[PitGuard] Software tests passed; engineering sign-off is not implied.\n'
