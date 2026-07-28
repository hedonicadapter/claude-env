#!/usr/bin/env bash
# Cloud-only refresh of ~/.claude from the claude-env repo.
#
# Why: a cloud environment snapshots its filesystem after the setup script runs
# once, then reuses that snapshot for ~7 days. Pushing to claude-env does NOT
# re-run the setup script. This hook re-pulls on every session start so changes
# land without bumping the setup script to bust the cache.
#
# Runs after Claude Code launched, so settings.json and the output style are
# already read — those take effect next session. Skills, commands and scripts
# are read lazily, so they apply immediately.
#
# Locally this is a no-op: your machine manages ~/.claude itself.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

curl -fsSL "https://raw.githubusercontent.com/${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}/${CLAUDE_ENV_REF:-main}/install.sh" \
  | bash >/dev/null 2>&1 || true

exit 0
