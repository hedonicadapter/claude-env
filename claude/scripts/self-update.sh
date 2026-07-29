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

REPO="${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
LOG="$DEST/self-update.log"

# Before the first thing that writes to $LOG, not after.
mkdir -p "$DEST" 2>/dev/null

# No PINNED_REF here, deliberately, and no fallback to a branch either.
#
# This script is installed by the very tree it downloads, so a pin baked into it
# ratchets *backwards*: the installed copy pins to N-1, fetches N-1, and installs
# N-1's copy of this script — which pins to N-2. Every session start walks the
# config one commit further back until it falls off the end onto whatever the
# oldest commit defaulted to. A pin cannot live in the thing it updates.
#
# CLAUDE_ENV_REF can, because it is a cloud *environment variable*: present in
# every session, and not something an install can overwrite. If it is absent
# there is no trustworthy ref to fetch, so do nothing — a stale config that the
# setup script installed deliberately beats silently rolling backwards.
if [ -z "${CLAUDE_ENV_REF:-}" ]; then
  printf -- '--- %s no CLAUDE_ENV_REF set, leaving the installed config alone\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"$LOG" 2>/dev/null
  exit 0
fi
REF="$CLAUDE_ENV_REF"

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
