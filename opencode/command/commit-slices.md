---
description: Commit the pending diff as one commit per intent, pre-grouped from the edit-groups hints the track-edits hook recorded. Live git diff is authoritative; edit-groups are hints only, so edits made outside the agent are handled too.
---

> **PARTIAL PORT.** This command depends on `track-edits.py` (a PostToolUse
> hook) and the `slice-commits` skill's `hunk_slice.py`. Both are coupled to
> Claude Code's hook-JSON format and were not fully re-wired for OpenCode — see
> `MIGRATION.md`. Until the track-edits shim exists, `$STORE` will be empty and
> everything falls into a single `uncategorized` slice (which is still safe and
> reversible). Paths below use the OpenCode global config dir.

# /commit-slices

Turn the current pending changes into **one commit per intent**, using the
edit-groups hints (written by `track-edits.py`) as *grouping hints*. The live
`git diff` is authoritative for what gets staged — edit-groups only suggest how
to group it. Changes made **outside** the agent are handled fine: they land in
an `uncategorized` slice instead of being lost.

This builds on the `slice-commits` skill's `hunk_slice.py`, which does the
hunk/line staging against `HEAD` (never touching the working tree, fully
reversible).

## Locating the store

Hints live **outside** the work tree. Ask the hook where:

```
python3 "${OPENCODE_CONFIG:-$HOME/.config/opencode}/scripts/track-edits.py" --print-store
```

Everything below calls that directory `$STORE`. Run it from the session cwd, or
pass the work-tree root as a second argument.

## Steps

1. **Read the intent hints.** For each `$STORE/*.json` (skip `.snapshots/` and
   `*.lock`), collect the category slug (filename without `.json`), its
   `description`, the set of `file`s across `edits[]`, and each edit's rough line
   ranges (`edits[].hunks[].start_line` / `line_count`). Intent map: *file
   (+approx line ranges) → category*. Hint only — line numbers drift.

2. **Get the authoritative diff.** Run:
   ```
   python3 "${OPENCODE_CONFIG:-$HOME/.config/opencode}/skills/slice-commits/scripts/hunk_slice.py" show
   ```
   Read the whole `F/H/L` inventory — IDs are positional to *this* invocation.

3. **Assign every change ID to a category:**
   - File claimed by exactly one category → assign whole file (`F<n>`).
   - File claimed by multiple → split at hunk/line level using recorded ranges +
     `description`.
   - File/hunk/line claimed by none → `uncategorized`. **Never drop anything
     silently** — surface what landed here.
   - Seed each slice's subject from the category `description`; match repo
     convention (`git log --oneline -20`).

4. **Write the plan JSON to scratch** — *outside* the repo. One slice per
   non-empty category. Schema: `{"slices":[{"subject":...,"body":...,"changes":[...]}]}`.
   If this session's commits carry a `Co-Authored-By:` trailer, add it to each
   `body`.

5. **Dry-run** until coverage is right:
   ```
   python3 <script> apply-plan <plan.json> --dry-run
   ```

6. **Show the user** the slice list, category→commit mapping, and exactly what
   fell into `uncategorized`. Get a go-ahead — grouping is a judgment call.

7. **Execute** after go-ahead:
   ```
   python3 <script> apply-plan <plan.json> --execute
   ```
   It prints the original HEAD sha; undo with `git reset --mixed <sha>`.

8. **Prune consumed hints.** For every category committed in full, delete
   `$STORE/<slug>.json` and `<slug>.json.lock`. Leave partially-committed hints.
   Do **not** touch `$STORE/.snapshots/`. Report what was pruned and kept.
