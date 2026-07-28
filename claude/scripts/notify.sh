#!/usr/bin/env bash
# Usage: notify.sh <event> <message>
#
# Two channels, both best-effort. Desktop notification is macOS-only; ntfy works
# anywhere the host can reach ntfy.sh.
#
# Topic comes from CLAUDE_NTFY_TOPIC rather than being hardcoded — this repo is
# public and ntfy topics are unauthenticated, so anyone who reads the topic name
# can publish to it and read your notifications.
#
# In a cloud session: set CLAUDE_NTFY_TOPIC in the environment's variables, and
# add ntfy.sh to a Custom network allowlist (it is NOT on the Trusted list).
# Cloud sessions already notify the Claude mobile app, so this is optional there.

event="${1:-event}"
message="${2:-Claude Code}"
ts="$(date '+%H:%M:%S')"
log="${CLAUDE_NOTIFY_LOG:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/notify.log}"
mkdir -p "$(dirname "$log")"

tn=skip
if [ "$(uname)" = Darwin ] && command -v terminal-notifier >/dev/null 2>&1; then
  tn=0
  # Unique -group + timestamped msg per fire defeats macOS coalescing.
  terminal-notifier -title 'Claude Code' -subtitle "$event" \
    -message "$message ($ts)" -group "claude-$event-$RANDOM$RANDOM" \
    -sound default || tn=$?
fi

nt=skip
if [ -n "${CLAUDE_NTFY_TOPIC:-}" ]; then
  nt=0
  curl -fsS -m 5 -H 'Title: Claude Code' -d "$message" \
    "https://ntfy.sh/${CLAUDE_NTFY_TOPIC}" >/dev/null || nt=$?
fi

printf '%s\t%s\ttn=%s\tntfy=%s\t%s\n' "$ts" "$event" "$tn" "$nt" "$message" >> "$log"
exit 0
