#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 scripts/verify-release-consistency.py
python3 scripts/verify-v388-integrity.py
python3 -m compileall -q services/api/app
PYTHONPATH="$(pwd)/services/api" python3 -m pytest -q verification/tests
printf '[PitGuard] V3.88.0 tests passed; engineering approval is not implied.\n'
