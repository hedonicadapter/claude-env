---
name: slice-commits
description: Split pending git changes (staged, unstaged, and untracked) into multiple well-scoped commits at hunk or single-line granularity instead of one big commit. Use whenever the user wants to split a diff into separate commits, clean up a messy working tree into a readable history, commit in smaller logical pieces, do "vertical slice" commits, or stage/commit at the hunk or line level rather than whole-file. Reach for this any time a pending diff mixes multiple unrelated changes — even within the same file or the same hunk — and the user wants them committed separately and reviewably.
compatibility: Requires git and a python3 interpreter (see Runtime note below).
---

# Slice Commits

Splits everything currently pending in a git working tree (staged, unstaged, and untracked) into multiple commits at hunk or single-line granularity, grouped into coherent "vertical slices" — instead of one commit that mixes unrelated changes.

## Why this needs more than `git add -p`

`git add -p` can't be driven non-interactively at single-line granularity, and hand-splitting a hunk normally means rewriting its `@@ -l,s +l,s @@` header counts by hand — exactly the kind of arithmetic that's easy to get subtly wrong. This skill sidesteps that: `scripts/hunk_slice.py` reconstructs each slice's file content directly from the file as it exists at `HEAD` plus a "has this specific line landed yet" decision per changed line, then stages the result straight into the index via `git hash-object` + `git update-index`. There's no patch text to get right, and the working tree is never touched — only the index and new commits, which is what makes the whole operation trivially reversible (see step 7).

## Workflow

1. **Inventory.** Run:
   ```
   python3 scripts/hunk_slice.py show
   ```
   This prints every file, hunk, and changed line pending against `HEAD`, each tagged with a stable ID (`F2`, `F2H1`, `F2H1L3`) — the real diff content is inline, not just IDs. Read the whole thing before designing slices.

   If the diff is large, scope it with pathspecs (`... show src/foo/`) — pass the *same* pathspecs to `apply-plan` later so the IDs still match.

2. **Design the slices.** This is the part that needs judgment, not a script:
   - Group by *intent*, not by file or by hunk. One file's changes can span multiple slices; one hunk's lines can too — that's the point of having line-level IDs at all.
   - Prefer an ordering that leaves the tree coherent at each step (e.g. a helper before its first caller) where the diff allows it.
   - Check `git log --oneline -20` and match the repo's actual commit message convention (prefix style, language, tense) rather than assuming one.
   - Every changed-line ID should end up in at most one slice's `"changes"` list. IDs left out of every slice are fine — they just stay as ordinary pending changes — but tell the user what you deliberately excluded and why; never drop something silently.
   - A slice's `"changes"` list can mix granularities freely: a whole file (`"F3"`), a whole hunk (`"F1H1"`), or individual lines (`"F2H1L3"`), in any combination.

3. **Write the plan JSON somewhere *outside* the repo** (e.g. a scratch directory) — a plan file written inside the repo shows up as its own untracked change on the next `show` and contaminates it. Format:
   ```json
   {
     "slices": [
       { "subject": "feat(nav): add NavLink component", "changes": ["F2"] },
       { "subject": "fix(schedule): read history top before popping",
         "body": "optional longer explanation",
         "changes": ["F1H1L1", "F1H1L2", "F1H1L3"] }
     ]
   }
   ```
   If this session's own commits normally carry a `Co-Authored-By: Claude <...>` trailer, append it to each slice's `body` for consistency.

4. **Dry-run it:**
   ```
   python3 scripts/hunk_slice.py apply-plan <plan.json> --dry-run
   ```
   This validates coverage (errors — with nothing committed — if any ID is claimed by more than one slice; reports anything left unassigned) and verifies in memory, *before* anything is written, that every **fully-assigned file** reconstructs byte-for-byte to its current working-tree content. Verification is per file, so deliberately leaving other IDs pending doesn't switch it off. Files only partially assigned are listed as unverifiable end-states — that's expected, not a warning. Fix the plan and re-run if it errors.

5. **Show the user the slice list the dry-run printed and get a quick go-ahead** before committing for real. This isn't about risk — the operation is trivially reversible either way — it's that the grouping is a semantic judgment call, and a five-second glance catches a bad split before it's five commits to fix instead of one plan to edit.

6. **Execute, only after that go-ahead:**
   ```
   python3 scripts/hunk_slice.py apply-plan <plan.json> --execute
   ```
   `--dry-run` and `--execute` are mutually exclusive and one is required — there's no bare/default invocation that commits, specifically so this step is never reached by accident. `--execute` resets the index to `HEAD` first (so anything already `git add`-ed manually gets folded into the plan instead of leaking into the wrong slice), then commits each slice in order, printing the original `HEAD` sha up front.

7. **Report back** what was committed and anything intentionally left pending. If anything looks wrong after the fact, undo the whole run in one step — the working tree was never touched, so this is safe to reach for without hesitation:
   ```
   git reset --mixed <original-HEAD-sha>
   ```

## Runtime note

Try `python3 scripts/hunk_slice.py ...` directly first. If `python3` isn't on `PATH`, the environment likely provisions interpreters on demand rather than installing them globally — fall back to `nix-shell -p python3 --run "python3 scripts/hunk_slice.py ..."` (check for a project-local convention first, e.g. an existing `flake.nix`/`shell.nix`, before assuming that's the right invocation).

## Limitations

- Renames aren't detected as such (diffed with `--no-renames`); a rename shows up as a full delete + full add. Land both halves in the same slice if you want it to still read as a rename in `git log`.
- Each file is staged with its **working-tree** mode, so a `chmod +x` made alongside a content edit is preserved. A mode change isn't separately sliceable, though: it rides along with whichever slice first stages that file. `show` flags it as `[mode 100644 -> 100755]` and `apply-plan` lists every one up front, so check that placement is what you want.
- A mode change with *no* content change produces nothing to slice at all — that file won't appear in `show`. Commit it yourself.
- Symlinks and submodules aren't specially handled; symlinks keep HEAD's mode rather than being staged as links. Anything the parser can't confidently break into hunks (this includes binary files) falls back to one atomic `F<n>:whole` id — include or exclude it as a unit.
- Assumes nothing else is committing or rewriting history in the same repo while a plan is being applied.

`scripts/hunk_slice.py`'s module docstring (also shown by `--help` on either subcommand) has the exact ID scheme and plan schema if you need the full reference.
