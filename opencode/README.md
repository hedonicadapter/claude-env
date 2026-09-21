# opencode/ — OpenCode port of claude-env

Runs your `claude-env` setup on [OpenCode](https://opencode.ai) instead of
Claude Code, as a browser chat UI on Azure App Service, authenticated with your
Microsoft account, billing LLM usage to your **GitHub Copilot** license (Claude
+ GPT) with an optional Claude-subscription fallback.

## Why OpenCode

Vendor-agnostic harness: one workflow over Claude, GPT, Gemini, and local
models. Switch providers mid-session (**Ctrl+M**) with context preserved, or
auto-fall-back on rate limits. Lets you keep working when a single vendor's
limits are hit.

## Files

```
opencode/
├── opencode.json              model, providers, permissions, plugins
├── AGENTS.md                  memory (RTK + TERSE, folded from CLAUDE.md)
├── rate-limit-fallback.json   auto-switch-on-429 config
├── command/commit-slices.md   ported /commit-slices (partial — see MIGRATION.md)
├── plugins/
│   └── notify.ts              ntfy on session idle/error (ports notify.sh)
│                              (rtk.ts generated at build by `rtk init -g --opencode`)
├── Dockerfile                 App Service container image
├── entrypoint.sh              starts `opencode web` on :8080
├── DEPLOY-AZURE.md            full deploy runbook (Entra auth + one-time OAuth)
└── MIGRATION.md               what ported cleanly vs partially, + auth ToS reality
```

## Quick start

1. Read `MIGRATION.md` (auth ToS reality + what's partial).
2. Follow `DEPLOY-AZURE.md` to build the image and deploy.
3. One-time `opencode auth login` → GitHub Copilot, inside the container.
4. Browse to the app URL, sign in with Microsoft, start coding.

## Local dev (optional)

```bash
docker build -t opencode-env ./opencode
docker run --rm -it -p 8080:8080 \
  -v "$PWD:/home/workspace" \
  -v opencode-auth:/home/.local/share \
  opencode-env
# then `docker exec` in and run `opencode auth login` once.
```

Not a drop-in for the Claude Code `~/.claude` config — that still lives in
`claude/` and installs via `install.sh`. This directory is the parallel
OpenCode target.
