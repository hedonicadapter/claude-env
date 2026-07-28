#!/usr/bin/env python3
"""Split pending git changes into multiple commits at hunk/line granularity.

This tool never touches the working tree. It only reads it. All staging
happens directly against git's object database and index (hash-object +
update-index), and each intermediate file's content is recomputed from
scratch against the fixed HEAD blob every time -- so there is no patch-header
math, no line-offset bookkeeping, and no risk of one slice's edits shifting
line numbers for another.

Subcommands
-----------
show [pathspec...]
    Print every pending change (staged + unstaged + untracked, relative to
    HEAD) annotated with stable IDs:
      F<n>            a whole file
      F<n>H<n>        a whole hunk within that file
      F<n>H<n>L<n>    one added/removed line within that hunk
    Binary files (or anything the line-level parser can't confidently
    handle) get a single atomic pseudo-line ID: F<n>:whole.

apply-plan PLAN.json (--dry-run | --execute) [pathspec...]
    Read a JSON plan (see below) and validate that it assigns every change
    to at most one slice. --dry-run stops there and changes nothing.
    --execute goes on to create one commit per slice, in order. Exactly one
    of the two is required -- there is no bare/default mode -- so a real
    run is always an explicit, visible choice, never an accident. Pass the
    same pathspec(s) used for `show` so IDs match.

Plan format
-----------
{
  "slices": [
    {
      "subject": "feat(nav): add NavLink component",   // required, commit subject
      "body": "optional longer explanation",            // optional
      "changes": ["F2", "F1H1L2", "F1H1L3"]              // required, non-empty
    },
    ...
  ]
}

Each entry in "changes" is a reference: a file ID (all of that file's
changes), a hunk ID (all lines in that hunk), or a single line ID. Slices
are committed in the order listed. Every ID must appear in at most one
slice -- appearing in zero slices is allowed (it's reported and left as a
pending working-tree change, not an error); appearing in more than one is a
hard error and aborts before anything is committed.

Before committing anything for real, the tool re-simulates the whole plan
and checks that fully-assigned files reconstruct byte-for-byte to their
current working-tree content. If that check fails, nothing is committed.

Undo: since the working tree is never touched, and these are ordinary local
commits, `git reset --mixed <original-HEAD>` (printed before any commits
are made) instantly restores the exact starting state.
"""
import argparse
import json
import os
import re
import subprocess
import sys


# --------------------------------------------------------------------------
# git plumbing helpers
# --------------------------------------------------------------------------

def run(args, input_bytes=None, check=True):
    proc = subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed: %s\n%s" % (" ".join(args), proc.stderr.decode("utf-8", "replace"))
        )
    return proc


def git(repo_root, args, input_bytes=None, check=True):
    return run(["git", "-C", repo_root, "-c", "core.quotePath=false"] + args, input_bytes=input_bytes, check=check)


def repo_root():
    proc = run(["git", "rev-parse", "--show-toplevel"])
    return proc.stdout.decode("utf-8").strip()


def to_repo_relative(root, p):
    return os.path.relpath(os.path.abspath(p), root)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

class Line:
    __slots__ = ("id", "kind", "old_no", "new_no")

    def __init__(self, kind, old_no, new_no):
        self.id = None
        self.kind = kind  # 'context' | 'add' | 'remove'
        self.old_no = old_no
        self.new_no = new_no


class Hunk:
    def __init__(self, old_start, old_count, new_start, new_count):
        self.id = None
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.lines = []

    def header(self):
        return "@@ -%d,%d +%d,%d @@" % (self.old_start, self.old_count, self.new_start, self.new_count)

    def changed_line_ids(self):
        return [l.id for l in self.lines if l.kind != "context"]


class FileEntry:
    def __init__(self, path, status, binary=False):
        self.id = None
        self.path = path
        self.status = status  # 'modified' | 'added' | 'deleted'
        self.binary = binary
        self.hunks = []
        self.head_bytes = None
        self.working_bytes = None
        self.head_lines = None
        self.working_lines = None

    def unparsed(self):
        return self.binary or not self.hunks

    def all_ids(self):
        if self.unparsed():
            return ["%s:whole" % self.id]
        return [lid for h in self.hunks for lid in h.changed_line_ids()]


# --------------------------------------------------------------------------
# Parsing `git diff` output
# --------------------------------------------------------------------------

HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff_bytes):
    lines = diff_bytes.split(b"\n")
    files = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].startswith(b"diff --git "):
            fe, i = parse_one_file(lines, i)
            if fe is not None:
                files.append(fe)
        else:
            i += 1
    return files


def parse_one_file(lines, i):
    i += 1  # skip "diff --git a/... b/..."
    status = "modified"
    is_binary = False
    a_path = b_path = None
    while i < len(lines):
        l = lines[i]
        if l.startswith(b"diff --git ") or l.startswith(b"@@"):
            break
        if l.startswith(b"--- "):
            a_path = l[4:]
        elif l.startswith(b"+++ "):
            b_path = l[4:]
        elif l.startswith(b"new file mode"):
            status = "added"
        elif l.startswith(b"deleted file mode"):
            status = "deleted"
        elif l.startswith(b"Binary files ") or l.startswith(b"GIT binary patch"):
            is_binary = True
        i += 1

    if a_path is None and b_path is None:
        # e.g. a pure mode change with no content diff -- nothing to slice
        while i < len(lines) and not lines[i].startswith(b"diff --git "):
            i += 1
        return None, i

    if a_path == b"/dev/null":
        path = strip_prefix(b_path, b"b/").decode("utf-8")
        status = "added"
    elif b_path == b"/dev/null":
        path = strip_prefix(a_path, b"a/").decode("utf-8")
        status = "deleted"
    else:
        path = strip_prefix(b_path, b"b/").decode("utf-8")

    fe = FileEntry(path=path, status=status, binary=is_binary)

    if is_binary:
        while i < len(lines) and not lines[i].startswith(b"diff --git "):
            i += 1
        return fe, i

    while i < len(lines) and lines[i].startswith(b"@@"):
        hunk, i = parse_hunk(lines, i)
        fe.hunks.append(hunk)
    return fe, i


def strip_prefix(b, prefix):
    return b[len(prefix):] if b.startswith(prefix) else b


def parse_hunk(lines, i):
    m = HUNK_RE.match(lines[i])
    if not m:
        raise RuntimeError("unparseable hunk header: %r" % lines[i])
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_start = int(m.group(3))
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    hunk = Hunk(old_start, old_count, new_start, new_count)
    i += 1
    old_no, new_no = old_start, new_start
    remaining_old, remaining_new = old_count, new_count
    while i < len(lines):
        l = lines[i]
        if l.startswith(b"\\ No newline at end of file"):
            i += 1
            continue
        if remaining_old <= 0 and remaining_new <= 0:
            break
        if l.startswith(b"@@") or l.startswith(b"diff --git "):
            break
        if l == b"" and remaining_old <= 0 and remaining_new <= 0:
            break
        prefix = l[:1]
        if prefix == b" " or l == b"":
            hunk.lines.append(Line("context", old_no, new_no))
            old_no += 1
            new_no += 1
            remaining_old -= 1
            remaining_new -= 1
        elif prefix == b"-":
            hunk.lines.append(Line("remove", old_no, None))
            old_no += 1
            remaining_old -= 1
        elif prefix == b"+":
            hunk.lines.append(Line("add", None, new_no))
            new_no += 1
            remaining_new -= 1
        else:
            raise RuntimeError("unexpected line in hunk body: %r" % l)
        i += 1

    old_seen = sum(1 for l in hunk.lines if l.kind in ("context", "remove"))
    new_seen = sum(1 for l in hunk.lines if l.kind in ("context", "add"))
    if old_seen != old_count or new_seen != new_count:
        raise RuntimeError(
            "internal parser error in hunk %s: expected old=%d new=%d, got old=%d new=%d"
            % (hunk.header(), old_count, new_count, old_seen, new_seen)
        )
    return hunk, i


def assign_ids(files):
    for fi, fe in enumerate(files, start=1):
        fe.id = "F%d" % fi
        for hi, h in enumerate(fe.hunks, start=1):
            h.id = "%sH%d" % (fe.id, hi)
            li = 0
            for line in h.lines:
                if line.kind != "context":
                    li += 1
                    line.id = "%sL%d" % (h.id, li)


# --------------------------------------------------------------------------
# Gathering: diff + untracked files + raw content
# --------------------------------------------------------------------------

def gather(root, pathspecs):
    rel = [to_repo_relative(root, p) for p in pathspecs]
    diff_args = ["diff", "--no-color", "--no-ext-diff", "--no-renames", "-U3", "HEAD"]
    if rel:
        diff_args += ["--"] + rel
    diff_bytes = git(root, diff_args).stdout
    files = parse_diff(diff_bytes)

    status_args = ["status", "--porcelain=v1", "-uall"]
    if rel:
        status_args += ["--"] + rel
    status_out = git(root, status_args).stdout.decode("utf-8", "replace")
    for line in status_out.splitlines():
        if line.startswith("?? "):
            path = line[3:]
            files.append(make_untracked_entry(root, path))

    files.sort(key=lambda fe: fe.path)
    assign_ids(files)

    for fe in files:
        fe.head_bytes = read_head_bytes(root, fe.path)
        fe.working_bytes = read_working_bytes(root, fe.path)
        if not fe.unparsed():
            fe.head_lines = fe.head_bytes.splitlines(keepends=True) if fe.head_bytes is not None else []
            fe.working_lines = fe.working_bytes.splitlines(keepends=True) if fe.working_bytes is not None else []
    return files


def make_untracked_entry(root, path):
    data = read_working_bytes(root, path)
    is_binary = data is not None and b"\x00" in data[:8000]
    fe = FileEntry(path=path, status="added", binary=is_binary)
    if not is_binary and data is not None:
        n = len(data.splitlines(keepends=True))
        h = Hunk(old_start=0, old_count=0, new_start=1, new_count=n)
        for idx in range(1, n + 1):
            h.lines.append(Line("add", None, idx))
        fe.hunks.append(h)
    return fe


def read_head_bytes(root, path):
    proc = run(["git", "-C", root, "show", "HEAD:%s" % path], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def read_working_bytes(root, path):
    abspath = os.path.join(root, path)
    if not os.path.isfile(abspath):
        return None
    with open(abspath, "rb") as f:
        return f.read()


def get_mode(root, fe):
    proc = run(["git", "-C", root, "ls-tree", "HEAD", "--", fe.path], check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.split()[0].decode()
    abspath = os.path.join(root, fe.path)
    if os.path.exists(abspath):
        st = os.stat(abspath)
        return "100755" if (st.st_mode & 0o111) else "100644"
    return "100644"


# --------------------------------------------------------------------------
# Materialization: reconstruct file content for a given "landed" ID set
# --------------------------------------------------------------------------

def hunk_contribution(hunk, head_lines, working_lines, landed_ids):
    out = []
    for line in hunk.lines:
        if line.kind == "context":
            out.append(head_lines[line.old_no - 1])
        elif line.kind == "remove":
            if line.id not in landed_ids:
                out.append(head_lines[line.old_no - 1])
        elif line.kind == "add":
            if line.id in landed_ids:
                out.append(working_lines[line.new_no - 1])
    return out


def file_content_at(fe, landed_ids):
    head_lines = fe.head_lines or []
    out = []
    pos = 0
    for h in fe.hunks:
        before_end = (h.old_start - 1) if h.old_count > 0 else h.old_start
        assert pos <= before_end, "overlapping hunks in %s" % fe.path
        out.extend(head_lines[pos:before_end])
        pos = before_end + h.old_count
        out.extend(hunk_contribution(h, head_lines, fe.working_lines, landed_ids))
    out.extend(head_lines[pos:])
    return b"".join(out)


def file_state(fe, landed_ids):
    """Return (exists, content_bytes) for `fe` once `landed_ids` have landed."""
    if fe.unparsed():
        whole = "%s:whole" % fe.id
        is_landed = whole in landed_ids
        if fe.status == "deleted":
            return (not is_landed), (fe.head_bytes if not is_landed else b"")
        content = fe.working_bytes if is_landed else fe.head_bytes
        exists = is_landed if fe.status == "added" else True
        return exists, (content if exists and content is not None else b"")

    ids = fe.all_ids()
    if fe.status == "added":
        exists = any(i in landed_ids for i in ids)
    elif fe.status == "deleted":
        exists = any(i not in landed_ids for i in ids)
    else:
        exists = True
    content = file_content_at(fe, landed_ids) if exists else b""
    return exists, content


# --------------------------------------------------------------------------
# `show`
# --------------------------------------------------------------------------

MAX_LINE_DISPLAY = 300


def display_text(raw_line_bytes):
    text = raw_line_bytes.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
    if len(text) > MAX_LINE_DISPLAY:
        text = text[:MAX_LINE_DISPLAY] + "…(truncated)"
    return text


def cmd_show(root, pathspecs):
    files = gather(root, pathspecs)
    head_sha = git(root, ["rev-parse", "--short", "HEAD"]).stdout.decode().strip()
    branch = git(root, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.decode().strip()

    out = []
    out.append("Base commit: HEAD @ %s (%s)" % (head_sha, branch))
    if not files:
        out.append("No pending changes (staged, unstaged, or untracked) against HEAD.")
        print("\n".join(out))
        return

    out.append("%d file(s) changed against HEAD (staged + unstaged + untracked)\n" % len(files))

    for fe in files:
        label = {"modified": "modified", "added": "new file", "deleted": "deleted"}[fe.status]
        tag = " [binary]" if fe.binary else ""
        out.append("=" * 70)
        out.append("FILE %s  %s  [%s]%s" % (fe.id, fe.path, label, tag))
        out.append("=" * 70)
        if fe.unparsed():
            out.append("  %s:whole   (no line-level diff available -- include/exclude as a unit)" % fe.id)
            out.append("")
            continue
        for h in fe.hunks:
            out.append("  HUNK %s  %s" % (h.id, h.header()))
            for line in h.lines:
                if line.kind == "context":
                    text = display_text(fe.head_lines[line.old_no - 1])
                    out.append("           %5s |  %s" % (line.old_no, text))
                elif line.kind == "remove":
                    text = display_text(fe.head_lines[line.old_no - 1])
                    out.append("%-9s- %5s |  %s" % (line.id, line.old_no, text))
                else:
                    text = display_text(fe.working_lines[line.new_no - 1])
                    out.append("%-9s+ %5s |  %s" % (line.id, line.new_no, text))
        out.append("")

    out.append("-" * 70)
    out.append('Every ID above must go into at most one slice\'s "changes" list.')
    out.append('A file ID ("F2") or hunk ID ("F1H1") is shorthand for all of its line IDs.')
    out.append("IDs you deliberately omit from every slice stay as pending working-tree changes.")
    print("\n".join(out))


# --------------------------------------------------------------------------
# `apply-plan`
# --------------------------------------------------------------------------

def ids_for_ref(ref, files_by_id, hunks_by_id, all_ids_set):
    if ref in all_ids_set:
        return [ref]
    if ref in hunks_by_id:
        return hunks_by_id[ref].changed_line_ids()
    if ref in files_by_id:
        return files_by_id[ref].all_ids()
    raise ValueError("unknown change reference: %r" % ref)


def cmd_apply_plan(root, plan_path, dry_run, pathspecs):
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    slices = plan.get("slices")
    if not slices:
        raise RuntimeError('plan has no "slices"')
    for idx, sl in enumerate(slices):
        if not sl.get("subject"):
            raise RuntimeError("slice %d is missing a \"subject\"" % idx)
        if not sl.get("changes"):
            raise RuntimeError('slice %d ("%s") has an empty "changes" list' % (idx, sl["subject"]))

    files = gather(root, pathspecs)
    files_by_id = {fe.id: fe for fe in files}
    hunks_by_id = {h.id: h for fe in files for h in fe.hunks}
    all_ids_set = set()
    for fe in files:
        all_ids_set.update(fe.all_ids())

    expanded = []
    seen_count = {}
    for sl in slices:
        ids = set()
        for ref in sl["changes"]:
            for lid in ids_for_ref(ref, files_by_id, hunks_by_id, all_ids_set):
                ids.add(lid)
        expanded.append(ids)
        for lid in ids:
            seen_count[lid] = seen_count.get(lid, 0) + 1

    duplicates = sorted(k for k, v in seen_count.items() if v > 1)
    if duplicates:
        raise RuntimeError(
            "these IDs are assigned to more than one slice -- fix the plan before retrying:\n  "
            + "\n  ".join(duplicates)
        )

    missing = sorted(all_ids_set - set(seen_count.keys()))
    fully_covered = not missing

    # Simulate every slice boundary and sanity-check reconstruction before touching git state.
    landed = set()
    per_slice_files = []
    for ids in expanded:
        landed |= ids
        touched_file_ids = set()
        for lid in ids:
            touched_file_ids.add(lid.split(":")[0].split("H")[0])
        per_slice_files.append(touched_file_ids)
        for fid in touched_file_ids:
            fe = files_by_id[fid]
            try:
                file_state(fe, landed)
            except Exception as e:
                raise RuntimeError("failed to reconstruct %s at this slice boundary: %s" % (fe.path, e))

    if fully_covered:
        for fe in files:
            exists, content = file_state(fe, all_ids_set)
            expected_exists = fe.working_bytes is not None
            expected_content = fe.working_bytes if expected_exists else b""
            if exists != expected_exists or content != expected_content:
                raise RuntimeError(
                    "internal consistency check failed for %s: reconstructed state does not match "
                    "the working tree. Nothing has been committed. This indicates a bug in the plan "
                    "or the tool -- please report the file path and re-run with --dry-run." % fe.path
                )

    print("Plan: %d slice(s) covering %d/%d change IDs%s" % (
        len(slices), len(seen_count), len(all_ids_set),
        "" if fully_covered else " (%d intentionally left pending)" % len(missing),
    ))
    for i, sl in enumerate(slices, start=1):
        print("  %d. %-60s  [%d change(s) across %d file(s)]" % (
            i, sl["subject"], len(expanded[i - 1]), len(per_slice_files[i - 1])
        ))
    if missing:
        missing_files = sorted({m.split(":")[0].split("H")[0] for m in missing})
        print("Left pending (not part of any slice): %s" % ", ".join(missing_files))
    if fully_covered:
        print("Verified: committing every slice reproduces the working tree exactly.")

    if dry_run:
        print("\n--dry-run: no git state was changed.")
        return

    original_head = git(root, ["rev-parse", "HEAD"]).stdout.decode().strip()
    print("\nOriginal HEAD: %s  (undo any of this with: git reset --mixed %s)" % (original_head, original_head))

    git(root, ["reset"])  # normalize index to HEAD; working tree untouched

    landed = set()
    for i, (sl, ids) in enumerate(zip(slices, expanded), start=1):
        landed |= ids
        touched_file_ids = per_slice_files[i - 1]
        for fid in touched_file_ids:
            fe = files_by_id[fid]
            exists, content = file_state(fe, landed)
            if exists:
                mode = get_mode(root, fe)
                sha = git(root, ["hash-object", "-w", "--stdin"], input_bytes=content).stdout.decode().strip()
                git(root, ["update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, sha, fe.path)])
            else:
                git(root, ["update-index", "--force-remove", "--", fe.path], check=False)
        commit_args = ["commit", "-m", sl["subject"]]
        if sl.get("body"):
            commit_args += ["-m", sl["body"]]
        git(root, commit_args)
        new_sha = git(root, ["rev-parse", "--short", "HEAD"]).stdout.decode().strip()
        print("  [%d/%d] %s  ->  %s" % (i, len(slices), new_sha, sl["subject"]))

    final_diff = git(root, ["diff", "--stat"]).stdout.decode("utf-8", "replace").strip()
    print("\nDone. Remaining working-tree diff (should match only intentionally-pending files):")
    print(final_diff if final_diff else "  (none)")
    print("\nUndo everything from this run: git reset --mixed %s" % original_head)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="Print annotated pending diff with stable IDs")
    p_show.add_argument("pathspecs", nargs="*")

    p_apply = sub.add_parser("apply-plan", help="Validate a slice plan and, only with --execute, commit it")
    p_apply.add_argument("plan", help="Path to plan JSON")
    mode = p_apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and print the plan; change nothing")
    mode.add_argument("--execute", action="store_true", help="Actually stage and commit each slice")
    p_apply.add_argument("pathspecs", nargs="*", help="Must match the pathspec(s) used for `show`")

    args = parser.parse_args()
    root = repo_root()

    try:
        if args.cmd == "show":
            cmd_show(root, args.pathspecs)
        elif args.cmd == "apply-plan":
            cmd_apply_plan(root, args.plan, args.dry_run, args.pathspecs)
    except (RuntimeError, ValueError) as e:
        print("Error: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
