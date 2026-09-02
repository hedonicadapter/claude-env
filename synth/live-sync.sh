#!/usr/bin/env bash
# Live tweak sync: pull patch.py from the branch so edits pushed by Claude
# hot-reload into your running synth while you play.
#
# Run this on your Mac, in the repo, in its own terminal, alongside the synth.
# It checks the branch every couple seconds and, when patch.py has changed,
# overwrites your local synth/patch.py — which the running engine detects and
# reloads with no dropped notes.
#
#   ./synth/live-sync.sh                       # default branch below
#   ./synth/live-sync.sh some/other-branch     # a different branch
#
# Only Claude should edit patch.py while this runs — your local edits to that
# one file will be overwritten by the next pull.
set -euo pipefail

cd "$(dirname "$0")/.."
BRANCH="${1:-claude/hot-reload-midi-synth-wpckcg}"
FILE="synth/patch.py"

echo "Live-syncing $FILE from origin/$BRANCH every 2s. Ctrl-C to stop."
while true; do
  git fetch -q origin "$BRANCH" 2>/dev/null || true
  if ! git diff --quiet "origin/$BRANCH" -- "$FILE" 2>/dev/null; then
    git checkout -q "origin/$BRANCH" -- "$FILE" \
      && echo "[sync] $FILE updated $(date +%H:%M:%S) — synth will reload"
  fi
  sleep 2
done
