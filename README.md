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
curl -fsSL https://raw.githubusercontent.com/hedonicadapter/claude-env/main/install.sh | bash
exit 0
```

Optionally add to **Environment variables**:

```
CLAUDE_NTFY_TOPIC=your-topic
```

If you want ntfy, also set **Network access** to Custom and add `ntfy.sh` —
it is not on the Trusted allowlist. Cloud sessions already notify the Claude
mobile app, so this is optional.

### Anywhere else

```bash
curl -fsSL https://raw.githubusercontent.com/hedonicadapter/claude-env/main/install.sh | bash
```

Honors `CLAUDE_ENV_REPO`, `CLAUDE_ENV_REF`, `CLAUDE_CONFIG_DIR`. The install
overlays onto an existing `~/.claude` rather than replacing it, so `sessions/`,
`projects/` and `history.jsonl` survive.

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
`self-update.sh`, which re-pulls on every session start.

## Gotchas encoded in these files

- **Release assets 403.** GitHub release-asset and API requests are scoped to
  repos attached to the session. `install.sh` fetches through `codeload.github.com`
  instead, which goes via the security proxy and is on the Trusted allowlist.
- **rtk is not installable upstream.** It comes from nixpkgs. `install.sh` tries
  `$PATH`, then a vendored binary in `bin/`, then `cargo install`. The
  `PreToolUse` hook is guarded with `command -v rtk`, so a miss is a no-op rather
  than an error on every Bash call.
- **ntfy topic is not hardcoded.** This repo is public and ntfy topics are
  unauthenticated. Set `CLAUDE_NTFY_TOPIC` or the channel stays off.
- **Plugin pinning is looser than nix.** `extraKnownMarketplaces` pins by `ref`,
  not `sha`. For exact pinning, pre-install into a directory during setup and
  point `CLAUDE_CODE_PLUGIN_SEED_DIR` at it — Claude Code then uses it read-only
  with no runtime clone.
- **Cloud env vars are not secrets.** Anyone using the environment can read them.

## Unverified

- `prompt-improver@severity1` — the plugin's name inside `severity1-marketplace`
  is a guess. Run `/plugin marketplace add severity1/severity1-marketplace` then
  `/plugin` to get the real name, and fix `enabledPlugins`.
- `bin/rtk-linux-x86_64` is not committed yet; `install.sh` falls back to
  `cargo install rtk`, which compiles from source against the 5-minute setup
  budget. Vendoring the binary makes this reliable.
