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

Built and validated, **never run in a real cloud session**. The `codeload` fetch
path in `install.sh` cannot be exercised until the repo is pushed.

- Git initialised on `main`; two commits. **No remote** — `hedonicadapter/claude-env`
  does not exist on GitHub yet (404). Pushing is the user's call, not done.
- `bash -n`, `py_compile`, JSON parse all pass.
- Overlay logic integration-tested against a simulated nix-managed dest, now
  including a **symlinked directory** (`scripts/ -> read-only store`), not just a
  symlinked file: store contents untouched, symlink replaced with a real dir,
  `sessions/` and `history.jsonl` survived, re-run is a 0-write no-op.
- Failure paths tested: missing `claude/` in the source, and a read-only dest.
  Both report `PARTIAL` (or refuse) and still exit 0.
- `hunk_slice.py` tested end-to-end on a scratch repo: a partially-assigned plan
  now verifies its fully-assigned files, a `chmod +x` riding along with a content
  edit commits as `100755`, and untracked files show in the pending report.
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
- **Copy writes to a temp name then renames.** `cp` writes *through* an existing
  symlink, and on the user's Mac `~/.claude/settings.json` points into the
  read-only nix store, so a plain `cp -a` overlay would try to write into the
  store. `rename(2)` replaces the symlink outright instead, and — unlike the
  earlier `rm -f` then `cp -p` — is atomic, which matters because `self-update.sh`
  re-runs the installer against a **live** session's config dir. Symlinked
  *directory* components need separate handling (`ensure_real_dir`): `mkdir -p`
  accepts a symlink-to-dir as already present and would write straight through.
- **Copy is an overlay, not a replace.** `sessions/`, `projects/`,
  `history.jsonl` must survive a re-run.
- **The rtk PreToolUse hook captures output instead of `exec`-ing.** rtk comes
  from nixpkgs and has no upstream install path, so it may legitimately be
  absent — but it can also be *present and wrong* (see the crates.io name
  collision below). The old `... && exec rtk hook claude || exit 0` could never
  reach its fallback: `exec` replaces the shell, so rtk's exit status became the
  hook's. The current form runs rtk as a child, emits its output only on
  success, and exits 0 otherwise — so a broken rtk degrades to a no-op just like
  a missing one.
- **`cargo install --git`, never `cargo install rtk`.** The crates.io `rtk` is
  reachingforthejack/rtk ("Rust Type Kit"), unrelated. It installs fine, passes
  `command -v rtk`, and then fails on `rtk hook claude` — breaking every Bash
  call, which is exactly what the guard exists to prevent. `install.sh` verifies
  with `rtk gain` afterwards and warns if it looks like the wrong binary.
- **The rtk rev is pinned to a sha, not a tag or branch.** `4c6d914` is v0.41.0,
  the version actually verified against `rtk hook claude` and `rtk gain`. Tags are
  mutable upstream; a sha is not. This matters more than it looks: the
  `PreToolUse` hook returns `updatedInput`, so rtk decides what Bash command
  Claude Code actually executes. `--locked` is there because `build.rs` runs at
  install time, so an unpinned dependency tree is also executable input.
- **`self-update.sh` fetches via codeload, not raw.githubusercontent.com.** Only
  codeload is on the Trusted allowlist. It also logs to
  `~/.claude/self-update.log` rather than discarding output — a silently failing
  self-update looks identical to a working one, and that was the whole cache
  workaround quietly not running.
- **`CLAUDE_ENV_NESTED=1` guards every hook.** `track-edits.py` shells out to
  `claude -p` to classify each edit. That child is a full Claude Code session
  reading the same `settings.json`, so it fires its own `SessionStart` (→ a
  complete `~/.claude` reinstall) and `Stop` (→ a notification) per edit.
  `--tools ""` stops it editing; only this sentinel stops its hooks.
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
  falls back to `cargo install --git ... --rev 4c6d914 --locked`, compiling from
  source against the 5-minute budget. Vendoring makes it reliable; it also puts a
  binary in git — decide on Git LFS or a release asset before the first push, not
  after. The installer looks for `bin/rtk-$os-$arch` derived from `uname`, so an
  arm64 build can sit alongside the x86_64 one. If you vendor, add a sha256 check
  before `install -m755`: that path drops a binary straight onto `$PATH`, and it
  is the same binary that gets command-rewrite authority over every Bash call.

## hunk_slice.py parser bugs found while testing

Not from the code review — these surfaced building an edge-case repo, and both
predate this repo. Fixed, with regression cases exercised end to end (paths with
spaces and quotes, a deletion, an untracked file with a space, a binary blob;
all committed across three slices, working tree byte-identical to HEAD after).

- **Any path containing a space crashed the tool.** git appends a TAB after the
  filename on `--- `/`+++ ` lines when it contains a space, so the parsed path
  was `with space.txt\t`, matched nothing in HEAD, and died with `IndexError` on
  an empty baseline. Now stripped in `header_path()`, which also C-unquotes.
- **A modified binary file vanished from the inventory.** Binary diffs carry no
  `---`/`+++` pair, only `Binary files a/X and b/X differ`, so both paths stayed
  `None` and the entry was discarded through the pure-mode-change branch. The
  file then wasn't in `all_ids_set`, so a plan omitting it still counted as
  fully covered and printed "Verified" — while never committing it. The path is
  now recovered from the `diff --git` header (`--no-renames` makes both halves
  identical, so a name with spaces is splittable by length).
- **Untracked paths with spaces arrived C-quoted** from `status --porcelain`,
  matched nothing on disk, and silently degraded to an opaque `F<n>:whole`.
  `status` now runs with `-z`.

Still unhandled, and now guarded rather than fixed: Python's `bytes.splitlines()`
splits on `\r` as well as `\n`, so a file with lone CRs can desync line numbering
against git's `\n`-only split. The per-file byte-for-byte check catches it and
aborts before committing, so it fails loudly instead of corrupting a blob.

## Owed verification

- `code-simplifier@official` — confirm the plugin name with `/plugin marketplace
  add anthropics/claude-plugins-official` then `/plugin`.
- First real cloud run: push, set the setup script, start a session, confirm the
  output style, model, hooks and `/commit-slices` all took.
- **Before the first push:** branch protection on `main`, 2FA, signed commits
  required, no outside collaborators. `self-update.sh` makes push access
  equivalent to unattended root execution in every cloud session, so that is the
  actual security boundary — see README "Security posture".

## Security review — 2026-07-29

Full pass over the repo as a cloud startup script. Decided and applied:

| Finding | Decision |
|---|---|
| `self-update.sh` executes unpinned `main` every session | Keep it; push access is the boundary (repo hardening above) |
| `cargo install --git` tracked HEAD | Pinned `--rev 4c6d914 --locked` |
| rtk rewrites every Bash command | Accepted; the pin is the control |
| `prompt-improver@severity1`, unverified 3rd-party plugin | **Dropped**, marketplace and all |
| No `permissions.deny` | Added a credential-read deny list |
| `track-edits.py` snapshots any file, incl. secrets | Sensitive-path skip; `.gitignore` failure is now a hard stop |
| Model-authored `description` stored unsanitized | `clean_description()` on write — control chars stripped, 100-char clamp |
| `curl -fsSL \| tar` unhardened, no `pipefail` | `--proto '=https' --proto-redir '=https' --max-time 120`, `set -uo pipefail`, `--no-same-owner --no-same-permissions` |
| `chmod +x` over unguarded globs | Now chmods only the files the copy loop installed |

Consciously **not** changed, and why: `CLAUDE_ENV_REPO`/`REF` stay unvalidated
(cloud env vars are already equivalent to code — README says so); the
`Bash(rtk ls *)`/`Bash(rtk grep *)` allowlist stays (prompt-free reads are the
point); `CLAUDE_NTFY_TOPIC` stays uninterpolated-but-unvalidated; the
`CLAUDE_CODE_REMOTE` gate stays a single env var; logs stay unrotated.

## Security review — second pass, 2026-07-29

Re-review of the same surface, decided by the user finding by finding:

| Finding | Decision |
|---|---|
| `self-update.sh` executes unpinned `main` every session | **Reversed the first pass.** Both scripts now default `CLAUDE_ENV_REF` to `PINNED_REF` (a sha) and warn on stderr when the effective ref is not 40-hex. Roll forward by setting the env var in the cloud environment — that does not bust the snapshot |
| Snapshots + verbatim conversation text sat in the work tree, one `git add -f` from a commit | Store moved out of tree to `$CLAUDE_CONFIG_DIR/edit-groups/<flattened-path>-<hash>/`; root chmod `0700`; the generated `.gitignore` is gone with it |
| rtk hook output passed through unvalidated | **Left as-is**, deliberately: sha-pinned source, `--locked`, and `bin/` ships no binary so the path is inert wherever cargo is absent |
| `self-update.log` / `notify.log` unbounded | Trimmed after each write — 500 and 1000 lines |
| tar symlink-escape, `REF`/`REPO` URL validation | **Left as-is**; both only bite once the source is already compromised, at which point `install.sh` is running the attacker's code anyway |

The store move is the one with a reader on the other side: `/commit-slices` must
not re-derive the path. It calls
`track-edits.py --print-store` instead, so the key derivation exists once. If you
change how the key is built, nothing else needs touching — that is the point.

No migration was written. A stale in-repo `.claude/edit-groups/` just sits there;
`/commit-slices` tells you to read it if it holds a category you still need, then
delete it. New edits populate the new store from scratch, and snapshots
self-correct against HEAD on first touch.

Reviewed and found clean, don't churn: `hunk_slice.py` (no `shell=True`, git
called with arg lists, `--` before user paths, working tree never touched,
byte-for-byte verification before any commit), the atomic cp-then-rename overlay,
`mktemp -d` + trap, backup-before-overwrite refusing to overwrite on backup
failure, the `slug` sanitizer already blocking path traversal, and the nested
`claude -p` running `--safe-mode --tools ""` behind a re-entry guard.

`clean_description()` is write-side only, per the decision taken. Pre-existing
group JSON written before this change still holds unsanitized descriptions; they
converge as categories are pruned by `/commit-slices`. (Those files are in the
old in-repo store, which the second pass below orphaned — so in practice they
converge by being deleted.)

## Deferred

Making this the source of truth for the nix config — add it as a flake input,
have `claude-code.nix` read this `settings.json` and merge the darwin-only hooks
on top. The user explicitly scoped this out for now. Do not do it unopposed.

## Note if you are reviewing this in a Claude Code session

The user's global `~/.claude/settings.json` has a `PreToolUse` hook that rewrites
every Bash command through `rtk`, and `permissions.allow` only covers `rtk ls`
and `rtk grep`. Whether that bites depends on the session's permission mode: with
prompting on and nobody to answer, rewritten `git` calls hard-fail, so read the
working tree directly instead. In a mode that doesn't prompt, `git` works fine.

Either way `rtk` filters output — `ls -l` comes back without permission bits, for
one — so use `rtk proxy <cmd>` when you need a command's real, unfiltered output.

Note this repo ships `claude/settings.json`, which is *not* what the running
session reads: hooks execute the installed copy under `~/.claude/`. Editing a
script here does not change the hook behaviour of the session you are in.
