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

set -u

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# track-edits.py spawns a nested `claude -p` per edit, and that child fires this
# same SessionStart hook. Without this guard every single file edit triggers a
# full ~/.claude reinstall, racing the live session's own hooks.
[ -z "${CLAUDE_ENV_NESTED:-}" ] || exit 0

REPO="${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}"
REF="${CLAUDE_ENV_REF:-main}"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
LOG="$DEST/self-update.log"

mkdir -p "$DEST" 2>/dev/null
TMP="$(mktemp -d)" || exit 0
trap 'rm -rf "$TMP"' EXIT

{
  printf -- '--- %s %s@%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$REPO" "$REF"

  # codeload, NOT raw.githubusercontent.com. The cloud session's proxy allowlists
  # codeload and not raw, so the old raw fetch failed on every start and `|| true`
  # hid it — leaving the ~7-day snapshot staleness this script exists to fix.
  if curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" \
       | tar -xz --strip-components=1 -C "$TMP"; then
    # Hand install.sh the checkout we already paid for rather than downloading twice.
    CLAUDE_ENV_SRC="$TMP" bash "$TMP/install.sh"
  else
    echo "self-update: fetch failed, keeping the installed config"
  fi
} >>"$LOG" 2>&1

exit 0
