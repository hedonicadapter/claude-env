# Agent instructions (OpenCode port of claude-env)

Global operating rules. Ported from `claude/CLAUDE.md` (which `@`-includes
`RTK.md` + `TERSE.md`). OpenCode has no "output styles"; the terse rules live
here as persistent instructions instead of a per-turn hook.

## Terse output — hard rule

Applies to every response, every turn, start to finish. Binding constraint, not
a stylistic preference.

- Drop articles (a, an, the) in EVERY sentence — first and last alike. Includes
  long technical explanations, bullets, headers, plan text.
- No preamble. Never open with "I'll…", "Let me…", "Sure,", "Great question", or
  a restatement of the request. First words carry information.
- No closing recap of what was just said or shown.
- State facts and outcomes. Cut hedging, option surveys, self-narration.
- Delete any sentence that would survive removal without information loss.

Exceptions — keep full grammar: user-facing strings (errors, UI, CLI help,
logs), commit messages, external docs/README/docstrings, search queries, and
anywhere terseness changes meaning.

Thoroughness governs WHAT gets done, never word count. Verbose prose is not
thoroughness.

## RTK — Rust Token Killer

Token-optimized CLI proxy (60–90% savings on dev operations).

Meta commands (run `rtk` directly):

```bash
rtk gain              # token savings analytics
rtk gain --history    # command usage history with savings
rtk discover          # analyze history for missed opportunities
rtk proxy <cmd>       # raw command, no filtering (debugging)
```

Verify install: `rtk --version`, `rtk gain`, `which rtk`. Name collision: if
`rtk gain` fails you likely have reachingforthejack/rtk (Rust Type Kit) instead.

Bash rewriting uses rtk's official OpenCode integration: `rtk init -g --opencode`
generates `~/.config/opencode/plugins/rtk.ts`, which hooks `tool.execute.before`
and transparently routes Bash commands through rtk (e.g. `git status` →
`rtk git status`). The model sees full output; rtk compresses it. Applies to the
Bash tool only. Baked at image build (see Dockerfile).
