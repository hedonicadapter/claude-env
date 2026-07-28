# Handoff — 2026-07-28

State of this project for a fresh session. [README.md](README.md) is the
user-facing doc; this is the working state, the decisions still open, and the
reasoning behind choices that look wrong at a glance.

## Goal

Make one Claude Code setup available from anywhere — Claude Code cloud sessions
first, but also any Linux box, devcontainer, or CI runner. Cloud session VMs get
a fresh Ubuntu 24.04 with the repo cloned and **nothing** from the user's
`~/.claude`, so config has to arrive over the network at session start.

Scope is Claude Code config only. Not a dotfiles port.

## Status

Built and validated, **never run end to end**. The `codeload` fetch path in
`install.sh` cannot be exercised until the repo is pushed.

- No `git init`. No commit. No remote.
- `bash -n`, `py_compile`, JSON parse all pass.
- Overlay logic integration-tested against a simulated nix-managed dest
  (symlinked `settings.json` into a read-only store): store file untouched,
  symlink replaced with a real file, `sessions/` and `history.jsonl` survived.
- Not yet run in an actual cloud session.

## Source of truth

This is **not** the source of truth for the user's nix config. The dotfiles repo
at `~/Documents/projects/dotfiles/main` is unchanged and stays authoritative
until the user says otherwise. Do not edit it from here.

## Layout and provenance

| Path | Came from |
|---|---|
| `claude/settings.json` | `home-manager-modules/claude-code.nix`, darwin-only parts removed |
| `claude/output-styles/terse.md` | same, verbatim |
| `claude/commands/commit-slices.md` | `home-manager-modules/commit-slices.md`, nix-shell steps made platform-aware |
| `claude/scripts/track-edits.py` | extracted verbatim from the `writePython3Bin` string in `claude-code.nix` |
| `claude/scripts/notify.sh` | rewritten from the `writeShellApplication` notifier |
| `claude/scripts/self-update.sh` | new |
| `claude/skills/slice-commits/` | copied from `~/.claude/skills/` — **not** nix-managed |
| `claude/CLAUDE.md`, `claude/RTK.md` | copied from `~/.claude/` |
| `install.sh`, `README.md` | new |

`slice-commits` is worth calling out: it exists only as a real directory in the
user's `~/.claude/skills/`, created by nothing in the nix config. `/commit-slices`
calls its `hunk_slice.py`, so without it the command breaks silently anywhere but
the user's Mac.

Dropped deliberately, macOS-only and meaningless on a VM: the `stay-awake` /
`caffeinate` lease hooks and `terminal-notifier`.

## Verified about cloud sessions

From a live session plus the docs:

- `$HOME` is `/root`; `~/.claude` is the config dir; `CLAUDE_CONFIG_DIR` unset.
- `cargo` and `rustup` are present.
- Setup script: bash, root, before Claude Code launches, must exit 0 or the
  session dies, ~5 min budget, configured only in the web UI env dialog.
- Filesystem snapshot after the first successful run, reused ~7 days. **Pushing
  here does not re-run it.** Bump the version comment in the setup script, or
  rely on `self-update.sh`.
- `CLAUDE_CODE_REMOTE=true` in the VM, never true locally.
- GitHub release assets and API calls are scoped to repos attached to the
  session; anything else 403s.
- `ntfy.sh` is not on the Trusted network allowlist. `*.nixos.org`, crates.io,
  npm, PyPI and `codeload.github.com` are.
- Cloud env vars are plaintext, readable by anyone using that environment.

## Design choices that look wrong but aren't

Do not "fix" these without reading the reason.

- **`install.sh` exits 0 on every failure path.** A cloud setup script that exits
  non-zero kills the session before Claude Code launches. Failures degrade to
  "config not installed", never "no session".
- **Copy unlinks each target before writing.** `cp` writes *through* an existing
  symlink. On the user's Mac `~/.claude/settings.json` points into the read-only
  nix store, so a plain `cp -a` overlay would try to write into the store. The
  per-file `rm -f` then `cp -p` loop is also portable to BSD cp, which has no
  `--remove-destination`.
- **Copy is an overlay, not a replace.** `sessions/`, `projects/`,
  `history.jsonl` must survive a re-run.
- **The rtk PreToolUse hook is guarded with `command -v`.** rtk comes from
  nixpkgs and has no upstream install path, so it may legitimately be absent. The
  guard makes a miss a no-op instead of an error on every single Bash call.
- **`codeload.github.com`, not release assets.** See the 403 scoping above.
- **ntfy topic is not hardcoded.** The repo has to be public for
  `raw.githubusercontent.com` to work, and ntfy topics are unauthenticated —
  a topic name in a public repo is a world-readable, world-writable
  notification channel.

## Open decisions

- **Repo name and visibility.** `hedonicadapter/claude-env` is baked into
  `install.sh` and `self-update.sh` as the default, and into the setup script in
  the README. Must be public, or `raw.githubusercontent.com` and `codeload` need
  a token. Changing the name means updating all three.
- **Vendor a prebuilt rtk?** `bin/rtk-linux-x86_64` is absent, so `install.sh`
  falls back to `cargo install rtk`, compiling from source against the 5-minute
  budget. Vendoring makes it reliable; it also puts a binary in git.

## Owed verification

- `prompt-improver@severity1` in `enabledPlugins` is a guess at the plugin's name
  inside `severity1-marketplace`. Run `/plugin marketplace add
  severity1/severity1-marketplace` then `/plugin` for the real name.
- `code-simplifier@official` should be confirmed the same way.
- First real cloud run: push, set the setup script, start a session, confirm the
  output style, model, hooks and `/commit-slices` all took.

## Deferred

Making this the source of truth for the nix config — add it as a flake input,
have `claude-code.nix` read this `settings.json` and merge the darwin-only hooks
on top. The user explicitly scoped this out for now. Do not do it unopposed.

## Note if you are reviewing this in a Claude Code session

The user's global `~/.claude/settings.json` has a `PreToolUse` hook that rewrites
every Bash command through `rtk`, and the allowlist only covers `rtk ls` and
`rtk grep`. Rewritten `git` calls hit an approval prompt, which hard-fails in a
non-interactive session. Expect `git diff` to be unavailable and plan to read the
working tree directly.
