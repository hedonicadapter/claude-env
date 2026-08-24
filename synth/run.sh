#!/usr/bin/env bash
# Launch the hot-reloadable synth. First run sets up a local venv.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating venv + installing deps (first run only)…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python engine.py "$@"
