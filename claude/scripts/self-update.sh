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

# pipefail: `curl | tar` otherwise reports only tar's status, so a truncated
# stream can extract a partial tree, exit 0, and get handed to install.sh.
set -uo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# track-edits.py spawns a nested `claude -p` per edit, and that child fires this
# same SessionStart hook. Without this guard every single file edit triggers a
# full ~/.claude reinstall, racing the live session's own hooks.
[ -z "${CLAUDE_ENV_NESTED:-}" ] || exit 0

# Keep in step with install.sh's PINNED_REF. A sha, not a branch: this fetch runs
# unattended on every session start, so a branch ref would mean the contents of
# that branch execute here with no review step. Bump both after pushing, or set
# CLAUDE_ENV_REF in the cloud environment's variables to roll forward without
# touching the repo.
PINNED_REF="04934ab95d1c564c44f28b824e92a34c8a9b7502"

REPO="${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}"
REF="${CLAUDE_ENV_REF:-$PINNED_REF}"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
LOG="$DEST/self-update.log"

mkdir -p "$DEST" 2>/dev/null
TMP="$(mktemp -d)" || exit 0
trap 'rm -rf "$TMP"' EXIT

{
  printf -- '--- %s %s@%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$REPO" "$REF"

  if ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
    echo "self-update: WARNING: ref '$REF' is not a commit sha — tracking a moving target"
  fi

  # codeload, NOT raw.githubusercontent.com. The cloud session's proxy allowlists
  # codeload and not raw, so the old raw fetch failed on every start and `|| true`
  # hid it — leaving the ~7-day snapshot staleness this script exists to fix.
  # --proto/--proto-redir: -L follows redirects, and curl's defaults permit a
  # downgrade to plain http. --max-time: this runs async at session start, so a
  # hung fetch would sit there indefinitely holding a temp dir.
  if curl -fsSL --proto '=https' --proto-redir '=https' --max-time 120 \
       "https://codeload.github.com/$REPO/tar.gz/$REF" \
       | tar -xz --strip-components=1 --no-same-owner --no-same-permissions \
             -C "$TMP"; then
    # Hand install.sh the checkout we already paid for rather than downloading twice.
    CLAUDE_ENV_SRC="$TMP" bash "$TMP/install.sh"
  else
    echo "self-update: fetch failed, keeping the installed config"
  fi
} >>"$LOG" 2>&1

# Appends on every session start and nothing else ever prunes it. Trim after the
# write, not before, so a run's own output is never the thing that gets cut.
if [ -f "$LOG" ] && tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null; then
  mv -f "$LOG.tmp" "$LOG" 2>/dev/null || rm -f "$LOG.tmp" 2>/dev/null
else
  rm -f "$LOG.tmp" 2>/dev/null
fi

exit 0
