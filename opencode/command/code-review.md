---
description: Review the current diff (or a branch/PR/path given as argument) for correctness bugs first, then reuse/simplification/efficiency. Ranked findings, adversarially verified, low false-positive rate.
---

# /code-review $ARGUMENTS

Review the change below for defects. Port of Claude Code's `/code-review`.

**Target** — from `$ARGUMENTS`:
- empty → the uncommitted working-tree diff (staged + unstaged).
- a branch name → `git diff` against its merge-base with the default branch.
- a path → restrict review to that path.
- a number → treat as a PR; if `gh` is available use `gh pr diff $ARGUMENTS`, else say so.

**Effort** — a trailing `low` / `medium` / `high` / `max` in `$ARGUMENTS` sets depth
(default `medium`): low/medium = fewer, high-confidence findings; high/max = broader,
may include uncertain findings. `--fix` = apply findings after review; `--comment` =
only relevant when a PR tool is wired (note if asked and unavailable).

## Context

Status:
!`git status --short`

Working-tree diff vs HEAD:
!`git diff HEAD`

Diff vs default branch (for branch reviews; empty on the default branch):
!`git diff $(git merge-base HEAD origin/HEAD 2>/dev/null || git merge-base HEAD main 2>/dev/null || echo HEAD)...HEAD 2>/dev/null | head -4000`

## Method

1. Read the diff and enough surrounding code (open files as needed) to judge each
   change in context — never review a hunk in isolation.
2. Hunt, in priority order:
   - **Correctness**: logic errors, off-by-one, null/undefined, wrong operator,
     unhandled error path, race, resource leak, broken invariant, security (injection,
     authz, secrets), data loss.
   - **Then** reuse (duplicated logic), simplification (dead code, needless
     complexity), efficiency (needless allocation, N+1, wrong data structure).
3. **Adversarially verify every candidate before reporting it.** State the concrete
   input/state → wrong output/crash. If you cannot construct that path, drop it — no
   speculation, no style nits, no "consider maybe".
4. Rank findings most-severe first. For each: `file:line`, one-sentence defect,
   the failure scenario, and a minimal suggested fix.
5. If nothing survives verification, say so plainly — do not invent findings.
6. If `--fix` was passed, apply the confirmed fixes to the working tree after listing
   them; keep each fix minimal and re-check the diff.

Match the repo's conventions (`git log`, neighbouring code). Terse output per global
rules, but keep `file:line` and code snippets exact.
