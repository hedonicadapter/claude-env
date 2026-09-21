# claude-env → OpenCode migration notes

What ported cleanly, what is partial, what was dropped, and the auth reality.
Read this before trusting any single piece.

## Mapping

| claude-env (Claude Code) | OpenCode port | Status |
|---|---|---|
| `settings.json` → `model: opus[1m]` | `opencode.json` → `model: github-copilot/claude-sonnet-4.5` | **Clean** (slug needs verifying — see below) |
| `settings.json` → `effortLevel: xhigh` | — | **Dropped.** OpenCode has no global reasoning-effort dial; some providers expose per-model reasoning options in `provider.*.options`. |
| `settings.json` → `outputStyle: Terse` | Terse rules in `AGENTS.md` | **Clean** (better — persistent, no per-turn hook) |
| `settings.json` → `permissions.allow` (rtk) | `opencode.json` → `permission.bash` | **Clean** |
| `settings.json` → `permissions.deny` (secret file reads) | partial `permission.bash` denies | **Partial.** OpenCode gates *tools* (bash/edit/webfetch), not file **reads**. The `Read(./.env)` etc. denies have no direct equivalent. Mitigations: keep secrets out of `/home/workspace`, or write a `permission.asked` plugin that blocks reads by path. |
| `extraKnownMarketplaces` + `code-simplifier@official` | — | **Dropped.** No Claude plugin marketplace in OpenCode. Reimplement as an OpenCode `agent` or `command` if wanted. |
| `CLAUDE.md` / `RTK.md` / `TERSE.md` | `AGENTS.md` | **Clean** |
| Hook: PreToolUse `rtk hook claude` | `plugin/rtk.ts` | **Partial.** rtk has no OpenCode hook mode; plugin prefixes known verbs with `rtk `. No full command coverage / output-format guarantee. |
| Hook: PostToolUse `track-edits.py` | — | **Not ported.** Script parses Claude-Code hook JSON and spawns nested `claude -p`. Needs a shim reading OpenCode's `tool.execute.after` / `file.edited` payloads instead. |
| Hook: UserPromptSubmit `terse-reminder.py` | folded into `AGENTS.md` | **Clean** (replaced by persistent instruction) |
| Hook: SessionStart `self-update.sh` | — | **N/A.** Container config is immutable; "update" = rebuild+redeploy the image (`az acr build` then `az webapp restart`). |
| Hook: Notification/Stop `notify.sh` | `plugin/notify.ts` | **Clean** (ntfy only; macOS `terminal-notifier` path dropped — host is Linux) |
| `commands/commit-slices.md` | `command/commit-slices.md` | **Partial.** Depends on track-edits (above) + slice-commits scripts. Runs, but `$STORE` is empty until the track-edits shim exists → everything lands in one `uncategorized` slice. |
| `skills/slice-commits/` | — | **Not ported.** OpenCode has no "skills". The `hunk_slice.py` script can be dropped into `~/.config/opencode/skills/slice-commits/scripts/` and called by the command as-is; grouping logic lives in the command prompt. |
| `install.sh` (`curl \| bash` rebuild `~/.claude`) | `Dockerfile` + `DEPLOY-AZURE.md` | **Replaced.** Reproducibility now comes from the pinned image, not a setup script. |

## Auth reality — read before relying on Claude-sub fallback

- **Anthropic prohibits Claude Pro/Max subscription use in third-party tools.**
  OpenCode removed bundled Claude-sub plugins in v1.3.0 to comply. The
  "Claude Pro/Max" OAuth option may still appear, but it is a gray area, is not
  guaranteed, and can be blocked or broken at any time.
- **Sanctioned ways to reach Claude here, in order of preference:**
  1. **Through GitHub Copilot** (this config's primary). Your Copilot license
     legitimately serves Claude Sonnet/Opus. No API key, no ToS risk.
  2. **A Claude API key** in `provider.anthropic` (pay-as-you-go, fully allowed).
  3. **A Claude Team/Enterprise seat**, if your plan permits API/tool use.
- The direct Pro/Max OAuth leg is included in `opencode.json` only as an
  optional switch and is left unauthenticated by default. Treat it as
  best-effort.

## Rate-limit fallback plugin

Auto-switch on 429/quota is provided by the community plugin
`opencode-rate-limit-fallback` (github.com/liamvinberg/opencode-rate-limit-fallback),
which reads `rate-limit-fallback.json`. It aborts the failed retry, reverts the
attempt, and resends the same message on the fallback model, keeping history
clean.

Vendor it one of two ways:

```bash
# A) if published to npm — add to opencode.json "plugin": ["opencode-rate-limit-fallback"]
# B) vendor locally into the config tree:
git clone https://github.com/liamvinberg/opencode-rate-limit-fallback \
  /opt/xdg-config/opencode/plugin/rate-limit-fallback
```

Then rebuild the image. Verify the plugin's expected config keys against its
README — `rate-limit-fallback.json` here uses `fallbackModel` / `patterns` /
`cooldownMs`, which may differ from the upstream schema.

**Reliable manual fallback regardless of the plugin:** press **Ctrl+M** in the
web UI / TUI to switch model mid-session. Context is preserved by OpenCode
either way — the harness owns conversation state and re-sends it to whichever
model you pick.

## Before first real use — verify these

1. `opencode models` → confirm the exact Copilot slugs; fix `opencode.json`
   (`model`, `small_model`) and `rate-limit-fallback.json` (`fallbackModel`) if
   they differ from the 2026-09 guesses.
2. `opencode auth list` → `github-copilot` present.
3. Send one prompt; confirm terse style holds and `rtk gain` shows nonzero
   savings (proves `plugin/rtk.ts` fires).
4. Trigger `session.idle` and confirm an ntfy push if `NTFY_TOPIC` is set.
