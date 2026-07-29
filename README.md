# claude-env

A portable Claude Code user config. One `curl | bash` reconstructs `~/.claude`
on any machine: a Claude Code cloud session VM, a plain Linux box, a
devcontainer, CI.

Nothing here depends on nix. The nix config on the Mac installs *binaries*
(`rtk`, `terminal-notifier`, `stay-awake`); this repo carries *configuration*.

## What's in it

```
claude/
├── settings.json              model, effort, output style, permissions, plugins, hooks
├── CLAUDE.md  RTK.md          global memory
├── output-styles/terse.md
├── commands/commit-slices.md
├── skills/slice-commits/      hunk/line-level commit slicing
└── scripts/
    ├── track-edits.py         PostToolUse: groups edits into vertical slices
    ├── notify.sh              desktop (macOS) + ntfy
    └── self-update.sh         cloud-only re-pull on session start
```

## Use it

### Claude Code cloud sessions

Open the environment selector at [claude.ai/code](https://claude.ai/code) (the
cloud icon above the message box — there is no settings page for it), edit the
environment, and put this in **Setup script**:

```bash
#!/bin/bash
# claude-env v1   <- bump to bust the environment cache
REF="${CLAUDE_ENV_REF:-54855fc34578834feef8e50cbbd354df005c8382}"
curl -fsSL "https://raw.githubusercontent.com/hedonicadapter/claude-env/$REF/install.sh" | bash
exit 0
```

The ref is in the URL on purpose. Fetching `main/install.sh` would mean the
bootstrap runs whatever is on the branch *before* any of the pinning inside
`install.sh` gets a say — the pin would only ever constrain the second fetch, not
the code doing the fetching. Same env var drives both, so there is one dial.

Add to **Environment variables**:

```
CLAUDE_ENV_REF=<the sha you reviewed>
CLAUDE_NTFY_TOPIC=your-topic          # optional
```

`CLAUDE_ENV_REF` is the dial you turn to roll out a config change: push, then
update the variable. It does **not** bust the environment cache, so the setup
script does not re-run — `self-update.sh` picks the new sha up at the next
session start. Bump the fallback baked into the setup script (and `PINNED_REF`
in both scripts) only when you want a freshly-built environment to land there
too.

If you want ntfy, also set **Network access** to Custom and add `ntfy.sh` —
it is not on the Trusted allowlist. Cloud sessions already notify the Claude
mobile app, so this is optional.

### Anywhere else

```bash
REF=54855fc34578834feef8e50cbbd354df005c8382
curl -fsSL "https://raw.githubusercontent.com/hedonicadapter/claude-env/$REF/install.sh" | bash
```

Honors `CLAUDE_ENV_REPO`, `CLAUDE_ENV_REF`, `CLAUDE_CONFIG_DIR`, and
`CLAUDE_ENV_SRC` (a pre-fetched checkout, which skips the download). Passing
`main` works and installs fine — it just prints a warning, because a branch is a
target someone with push access can move between one session start and the next.

The install overlays onto an existing `~/.claude` rather than replacing it, so
anything this repo does not ship — `sessions/`, `projects/`, `history.jsonl` —
survives untouched. Paths it *does* ship are overwritten: if you already have a
`~/.claude/CLAUDE.md` or `settings.json`, **they get replaced**. The original is
copied to `~/.claude/.claude-env-backup/` first and the run tells you how many
files that happened to. Re-runs are no-ops for unchanged files, so that backup
is never overwritten by a later install.

## How it behaves in a cloud session

Verified against a live session: `$HOME` is `/root`, `~/.claude` is the config
dir, and `cargo`/`rustup` are present.

| | |
|---|---|
| Base image | Ubuntu 24.04, 4 vCPU / 16 GB / 30 GB |
| Preinstalled | python3, git, ripgrep, node, cargo, docker, postgres, redis |
| Setup script | root, before Claude Code launches, must exit 0, ~5 min budget |
| Caching | filesystem snapshot after first run, reused ~7 days |
| `CLAUDE_CODE_REMOTE` | `true` — hooks branch on this |

**The cache is the thing that surprises you.** Pushing here does not re-run the
setup script. Either bump the version comment in the setup script, or rely on
`self-update.sh`, which re-pulls on every session start — through `codeload`,
like `install.sh`, since `raw.githubusercontent.com` is not on the allowlist.
It logs every run to `~/.claude/self-update.log`; check there if a push doesn't
seem to have landed.

## Gotchas encoded in these files

- **Release assets 403.** GitHub release-asset and API requests are scoped to
  repos attached to the session. `install.sh` fetches through `codeload.github.com`
  instead, which goes via the security proxy and is on the Trusted allowlist.
- **rtk is not installable upstream.** It comes from nixpkgs. `install.sh` tries
  `$PATH`, then `bin/rtk-$(uname -s)-$(uname -m)`, then
  `cargo install --git https://github.com/rtk-ai/rtk --rev <sha> --locked`. Not
  plain `cargo install rtk` — the crates.io name belongs to an unrelated tool
  (reachingforthejack/rtk, "Rust Type Kit"), which would install cleanly, satisfy
  the hook's `command -v rtk` guard, and then fail on every Bash call. After
  installing, the script runs `rtk gain` and warns loudly if it's the wrong binary.
- **The rtk rev is pinned, deliberately.** The `PreToolUse` hook hands rtk
  command-rewrite authority over every Bash call — it returns `updatedInput`, so
  what Claude Code executes is whatever rtk says. That is the last thing in this
  repo that should track a moving branch, so the fallback pins a sha (v0.41.0,
  the version verified against `rtk hook claude` and `rtk gain`) rather than HEAD
  or a tag, and `--locked` pins the dependency tree since `build.rs` runs at
  install time. Bumping it is a deliberate edit, not a side effect of upstream
  pushing.
- **The rtk hook survives a broken rtk, not just a missing one.** The
  `PreToolUse` hook captures rtk's output and only emits it on success, so a
  wrong-crate, wrong-arch, or too-old rtk degrades to a no-op instead of
  erroring on every Bash call.
- **ntfy topic is not hardcoded.** This repo is public and ntfy topics are
  unauthenticated. Set `CLAUDE_NTFY_TOPIC` or the channel stays off.
- **Plugin pinning is looser than nix.** `extraKnownMarketplaces` pins by `ref`,
  not `sha`, and a plugin can ship hooks, skills and MCP servers — arbitrary code
  loaded at launch. Only `anthropics/claude-plugins-official` is configured for
  that reason. For exact pinning, pre-install into a directory during setup and
  point `CLAUDE_CODE_PLUGIN_SEED_DIR` at it — Claude Code then uses it read-only
  with no runtime clone.
- **Cloud env vars are not secrets.** Anyone using the environment can read them.
  Since `CLAUDE_ENV_REPO` and `CLAUDE_ENV_REF` steer what `install.sh` downloads
  and runs, edit access to the environment is equivalent to code execution in it.
- **`permissions.deny` blocks credential reads.** `.env*`, `*.pem`, `*.key`,
  `*_rsa`, `*.tfvars`, `secrets/**`, `~/.ssh`, `~/.aws`, `~/.config/gh`.
  `track-edits.py` skips the same set independently — it writes a full plaintext
  snapshot of every file it tracks, so a tracked credential file would mean two
  more copies of it on disk. Both layers exist because the deny list only
  constrains Claude; the hook fires on any edit.
- **The edit-groups store lives outside the work tree**, under
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/edit-groups/<flattened-path>-<hash>/`
  (`track-edits.py --print-store` resolves it; `/commit-slices` asks rather than
  deriving, so the key exists in one place). It used to sit at
  `.claude/edit-groups/` with a generated `.gitignore` of `*` as the only thing
  between a plaintext copy of every edited file — plus verbatim conversation
  text — and a commit. One `git add -f`, one `tar` of the project directory, or
  one tool that does not honour gitignores was enough. Out of tree there is
  nothing to ignore and nothing to leak into a diff. The store root is chmod
  `0700` because it now aggregates plaintext across every repo on the box.

## Security posture

`install.sh` runs as root before Claude Code launches, and `self-update.sh`
re-downloads and executes it on every session start. **Whatever ref that fetch
resolves to is unattended root code execution in every cloud session**, with that
session's repo credentials and network access.

That ref is a **pinned sha**, not `main`. Both scripts default `CLAUDE_ENV_REF`
to `PINNED_REF`, and both warn loudly on stderr if the effective ref is not a
40-hex sha, so tracking a branch is possible but never silent. A branch would
mean the contents of that branch execute here the next time any session starts,
with no review step in between; a sha cannot be moved under you.

Rolling out a config change is two steps:

1. Push it.
2. Set `CLAUDE_ENV_REF` to the new sha in the cloud environment's variables.

Changing an env var does **not** bust the filesystem snapshot, so this keeps the
fast-refresh the self-update mechanism exists for — you just choose when it
happens. Bump `PINNED_REF` in both scripts once a sha has been reviewed, so a
fresh environment with no vars set also lands somewhere known-good.

Push access is still worth treating as a security boundary, since it is what
produces the shas you will pin:

- Branch protection on `main`, no direct pushes.
- 2FA on the GitHub account; no outside collaborators.
- Require signed commits.
- Read merged PRs as though they were `curl | sudo bash` — because the sha you
  pin next will be.

With the pin in place, reviewing a checkout does tell you what runs next session,
which is the whole point of the change. The other pinned things (`--rev` for rtk,
the `official` marketplace only) exist for the same reason.

## Unverified

- `code-simplifier@official` — confirm the plugin name with
  `/plugin marketplace add anthropics/claude-plugins-official` then `/plugin`.
- `bin/rtk-linux-x86_64` is not committed yet, so `install.sh` falls back to
  `cargo install --git ... --rev <sha> --locked`, which compiles from source
  against the 5-minute setup budget. Vendoring the binary makes this reliable —
  verify a sha256 when you do, since the vendored path is `install -m755`
  straight onto `$PATH`. Name it `rtk-$(uname -s | tr A-Z a-z)-$(uname -m)`; the
  installer only picks up an exact os/arch match.
