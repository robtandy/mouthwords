#!/usr/bin/env bash
# Idempotent launcher: creates venv on first run, installs deps if
# requirements.txt has changed, then runs dictate.py. Re-runs are fast.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
STAMP="$VENV/.requirements-stamp"

if [ ! -d "$VENV" ]; then
  echo "creating venv..."
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "installing dependencies..."
  pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
  touch "$STAMP"
fi

exec python dictate.py "$@"
