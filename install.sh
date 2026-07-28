#!/usr/bin/env bash
# Reconstruct a Claude Code user config anywhere: cloud session VM, plain Linux
# box, devcontainer, CI. Idempotent, and always exits 0 — a cloud setup script
# that exits non-zero kills the session before Claude Code launches.
#
#   curl -fsSL https://raw.githubusercontent.com/hedonicadapter/claude-env/main/install.sh | bash
#
# Env:
#   CLAUDE_ENV_REPO   owner/repo to pull from      (default hedonicadapter/claude-env)
#   CLAUDE_ENV_REF    branch/tag/sha               (default main)
#   CLAUDE_CONFIG_DIR install target               (default $HOME/.claude)

set -u

REPO="${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}"
REF="${CLAUDE_ENV_REF:-main}"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

TMP="$(mktemp -d)" || exit 0
trap 'rm -rf "$TMP"' EXIT

# codeload, not the release-asset host: GitHub release assets are scoped to the
# repos attached to the session and 403 for anything else.
if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" \
     | tar -xz --strip-components=1 -C "$TMP"; then
  echo "claude-env: fetch failed ($REPO@$REF), leaving existing config alone" >&2
  exit 0
fi

mkdir -p "$DEST"
# Overlay, not replace — never clobber sessions/, projects/, history.jsonl.
# Unlink each target first: `cp` writes THROUGH an existing symlink, and on a
# nix-managed host ~/.claude/settings.json points into the read-only store.
( cd "$TMP/claude" && find . -type f -print0 ) | while IFS= read -r -d '' f; do
  mkdir -p "$DEST/$(dirname "$f")"
  rm -f "$DEST/$f"
  cp -p "$TMP/claude/$f" "$DEST/$f"
done
chmod +x "$DEST"/scripts/* 2>/dev/null

# rtk: the PreToolUse Bash hook wants it. Hook is guarded, so a miss degrades to
# a no-op rather than erroring on every Bash call.
if ! command -v rtk >/dev/null 2>&1; then
  if [ -x "$TMP/bin/rtk-linux-x86_64" ]; then
    install -m755 "$TMP/bin/rtk-linux-x86_64" /usr/local/bin/rtk 2>/dev/null || true
  else
    cargo install rtk >/dev/null 2>&1 || true
  fi
fi

echo "claude-env: installed $REPO@$REF -> $DEST" >&2
exit 0
