#!/usr/bin/env bash
echo "[PitGuard] Environment check"

echo "Shell: $SHELL"

if command -v python >/dev/null 2>&1; then
    python --version
else
    echo "[ERROR] Python not found"
fi

if command -v node >/dev/null 2>&1; then
    node --version
else
    echo "[WARN] Node not found"
fi
