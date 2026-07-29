#!/usr/bin/env bash
# Reconstruct a Claude Code user config anywhere: cloud session VM, plain Linux
# box, devcontainer, CI. Idempotent, and always exits 0 — a cloud setup script
# that exits non-zero kills the session before Claude Code launches.
#
#   REF=<sha>   # see PINNED_REF below; a branch works but warns
#   curl -fsSL "https://raw.githubusercontent.com/hedonicadapter/claude-env/$REF/install.sh" | bash
#
# Exiting 0 is not the same as claiming success: every failure path prints what
# broke, and the final line says "PARTIAL" if anything did.
#
# Env:
#   CLAUDE_ENV_REPO   owner/repo to pull from      (default hedonicadapter/claude-env)
#   CLAUDE_ENV_REF    branch/tag/sha               (default: the pinned sha below)
#   CLAUDE_ENV_SRC    pre-fetched checkout; skips the download entirely
#   CLAUDE_CONFIG_DIR install target               (default $HOME/.claude)

# pipefail matters on the one pipeline below: without it `curl | tar` reports
# only tar's status, so a stream truncated on a member boundary extracts a
# partial tree and exits 0 — a silent partial install.
set -uo pipefail

# A sha, not `main`. self-update.sh re-runs this script on every cloud session
# start, so a branch ref means whatever sits on that branch at that instant gets
# executed unattended, with the session's repo credentials and network access —
# and no review step in between. A sha does not move.
#
# Rolling out a config change is therefore two steps: push it, then set
# CLAUDE_ENV_REF to the new sha in the cloud environment's variables. Changing an
# env var does NOT bust the filesystem snapshot, so the fast-refresh this whole
# mechanism exists for is preserved — you just choose when it happens.
#
# Bump this default when the pushed sha has been reviewed.
PINNED_REF="04934ab95d1c564c44f28b824e92a34c8a9b7502"

REPO="${CLAUDE_ENV_REPO:-hedonicadapter/claude-env}"
REF="${CLAUDE_ENV_REF:-$PINNED_REF}"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SRC="${CLAUDE_ENV_SRC:-}"
BACKUP="$DEST/.claude-env-backup"

# Not fatal — testing a branch is a legitimate thing to want — but it must not be
# silent. Anything that isn't a full sha is a target someone else can move under
# you between one session start and the next.
if ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "claude-env: WARNING: ref '$REF' is not a commit sha — this install tracks a moving target that anyone with push access to $REPO controls" >&2
fi

TMP="$(mktemp -d)" || exit 0
trap 'rm -rf "$TMP"' EXIT

if [ -z "$SRC" ]; then
  # codeload, not the release-asset host: GitHub release assets are scoped to the
  # repos attached to the session and 403 for anything else.
  # --proto/--proto-redir: -L follows redirects and curl's default redirect
  # protocols include plain http, so an injected redirect could downgrade the
  # fetch. --max-time: a hung connection would otherwise eat the whole ~5 min
  # cloud setup budget and kill the session.
  if ! curl -fsSL --proto '=https' --proto-redir '=https' --max-time 120 \
       "https://codeload.github.com/$REPO/tar.gz/$REF" \
       | tar -xz --strip-components=1 --no-same-owner --no-same-permissions \
             -C "$TMP"; then
    echo "claude-env: fetch failed ($REPO@$REF), leaving existing config alone" >&2
    exit 0
  fi
  SRC="$TMP"
fi

# Guard the layout explicitly. Without this a changed tarball shape makes the
# copy loop below iterate zero times, which is indistinguishable from success.
if [ ! -d "$SRC/claude" ]; then
  echo "claude-env: no claude/ directory under $SRC — unexpected layout, nothing installed" >&2
  exit 0
fi

if ! mkdir -p "$DEST"; then
  echo "claude-env: cannot create $DEST, nothing installed" >&2
  exit 0
fi

# `mkdir -p` accepts a symlink-to-directory as "already there", so a nix- or
# home-manager-managed ~/.claude/scripts -> /nix/store/... would leave every
# write below it aimed at the read-only store. Walk the components and replace
# symlinked ones with real directories.
ensure_real_dir() {
  local acc="$1" rel="$2" part
  # The base itself may not exist yet (the backup dir on a first overwrite).
  # -d follows symlinks, so a deliberately symlinked ~/.claude is left alone;
  # only components *below* the base get de-symlinked.
  if [ ! -d "$acc" ]; then
    mkdir -p "$acc" || return 1
  fi
  while [ -n "$rel" ]; do
    part="${rel%%/*}"
    if [ "$part" = "$rel" ]; then rel=""; else rel="${rel#*/}"; fi
    if [ -z "$part" ] || [ "$part" = "." ]; then continue; fi
    acc="$acc/$part"
    if [ -L "$acc" ]; then
      rm -f "$acc" || return 1
    fi
    if [ ! -d "$acc" ]; then
      mkdir "$acc" || return 1
    fi
  done
  return 0
}

installed=0
skipped=0
backed_up=0
failed=0
exec_files=()

# Overlay, not replace — never clobber sessions/, projects/, history.jsonl.
# Fed by process substitution rather than a pipe so the counters above survive:
# a piped `while` runs in a subshell and its variables die with it.
while IFS= read -r -d '' f; do
  f="${f#./}"
  dir="$(dirname "$f")"
  src="$SRC/claude/$f"
  dst="$DEST/$f"

  # Collected here rather than chmod'd by glob at the end: a glob over
  # $DEST/scripts/* marks whatever else happens to live there executable too,
  # including files this repo never shipped. Recorded before the skip check so
  # a re-run still corrects the mode on an unchanged file.
  case "$f" in
    scripts/*|skills/*/scripts/*) exec_files+=("$dst") ;;
  esac

  # Already byte-identical: skip. Keeps re-runs cheap, shrinks the window in
  # which a live session can observe a half-written file, and on a nix host
  # leaves a matching store symlink alone instead of churning it into a copy.
  if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
    skipped=$((skipped + 1))
    continue
  fi

  if ! ensure_real_dir "$DEST" "$dir"; then
    echo "claude-env: cannot create $DEST/$dir, skipping $f" >&2
    failed=$((failed + 1))
    continue
  fi

  # Whatever is here is user-authored (or an older claude-env) and is about to
  # be destroyed — CLAUDE.md and settings.json especially. Keep a copy. On a
  # re-run the file already matches and we skipped above, so the first backup
  # is never overwritten by our own output.
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if ensure_real_dir "$BACKUP" "$dir" && cp -pL "$dst" "$BACKUP/$f" 2>/dev/null; then
      backed_up=$((backed_up + 1))
    else
      echo "claude-env: could not back up $dst, refusing to overwrite it" >&2
      failed=$((failed + 1))
      continue
    fi
  fi

  # cp-then-rename, never rm-then-cp: self-update.sh runs this against a LIVE
  # session's config dir, and a hook firing mid-write must not see a missing or
  # truncated script. rename(2) is atomic and replaces a symlink outright,
  # which also covers the read-only-store case the old `rm -f` was there for.
  tmpf="$DEST/$dir/.claude-env.tmp.$$"
  if cp -p "$src" "$tmpf" 2>/dev/null && mv -f "$tmpf" "$dst" 2>/dev/null; then
    installed=$((installed + 1))
  else
    rm -f "$tmpf" 2>/dev/null
    echo "claude-env: failed to write $dst" >&2
    failed=$((failed + 1))
  fi
done < <(cd "$SRC/claude" && find . -type f -print0)

if [ "${#exec_files[@]}" -gt 0 ]; then
  chmod +x "${exec_files[@]}" 2>/dev/null
fi

# rtk: the PreToolUse Bash hook wants it. The hook is guarded and degrades to a
# no-op, so every failure path here is survivable.
if ! command -v rtk >/dev/null 2>&1; then
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  vendored="$SRC/bin/rtk-$os-$arch"

  rtkbin=""
  for d in /usr/local/bin "$HOME/.local/bin"; do
    mkdir -p "$d" 2>/dev/null
    if [ -w "$d" ]; then rtkbin="$d"; break; fi
  done

  if [ -z "$rtkbin" ]; then
    echo "claude-env: no writable bin dir for rtk, skipping (hook degrades to a no-op)" >&2
  elif [ -f "$vendored" ]; then
    # Named by uname, and tested with -f rather than -x. Matching on the
    # host's own os/arch stops an x86_64 ELF landing on an arm64 box, where it
    # would satisfy `command -v rtk` and then die with Exec format error on
    # every Bash call; -f stops a binary committed with mode 644 from silently
    # falling through to the cargo path below.
    install -m755 "$vendored" "$rtkbin/rtk" 2>/dev/null \
      || echo "claude-env: could not install $vendored -> $rtkbin/rtk" >&2
  elif command -v cargo >/dev/null 2>&1; then
    # --git, NOT `cargo install rtk`: the crates.io `rtk` is an unrelated tool
    # (reachingforthejack/rtk, "Rust Type Kit"). Installing that would satisfy
    # the hook's `command -v rtk` guard and then fail on `rtk hook claude`.
    #
    # --rev, not a branch or a tag: this binary gets handed command-rewrite
    # authority over every Bash call (the PreToolUse hook returns updatedInput),
    # so it is the last thing that should track a moving target. Tags are
    # mutable upstream; a sha is not. This one is v0.41.0, the version actually
    # verified against `rtk hook claude` and `rtk gain`. --locked pins the
    # dependency tree too, since build.rs runs at install time.
    cargo install --git https://github.com/rtk-ai/rtk \
      --rev 4c6d9147c46384e61652f4cb6c8f0c695f017bfc --locked >/dev/null 2>&1 \
      || echo "claude-env: cargo install of rtk failed (hook degrades to a no-op)" >&2
  else
    echo "claude-env: no vendored rtk for $os-$arch and no cargo, skipping" >&2
  fi
fi

# An rtk that exists but is the wrong rtk is worse than no rtk: `rtk gain` is
# the repo's own documented discriminator (see claude/RTK.md).
if command -v rtk >/dev/null 2>&1 && ! rtk gain >/dev/null 2>&1; then
  echo "claude-env: WARNING: $(command -v rtk) does not answer 'rtk gain' — probably the unrelated Rust Type Kit. The Bash hook will fall back to a no-op." >&2
fi

if [ "$failed" -gt 0 ]; then
  echo "claude-env: PARTIAL install of $REPO@$REF -> $DEST ($installed written, $skipped unchanged, $failed failed)" >&2
else
  echo "claude-env: installed $REPO@$REF -> $DEST ($installed written, $skipped unchanged)" >&2
fi
if [ "$backed_up" -gt 0 ]; then
  echo "claude-env: overwrote $backed_up pre-existing file(s); originals under $BACKUP" >&2
fi
exit 0
