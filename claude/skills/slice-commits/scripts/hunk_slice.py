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

Before committing anything for real, the tool re-simulates the whole plan.
Every file whose changes are *all* assigned to some slice is then checked to
reconstruct byte-for-byte to its current working-tree content -- per file, so
a plan that deliberately leaves other things pending is still verified. Files
only partially assigned are named in the output as unverifiable end-states.
If any check fails, nothing is committed.

Each file is staged with its *working-tree* mode, so a `chmod +x` alongside a
content edit is preserved; mode changes are not separately sliceable and are
reported up front.

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
        self.head_mode = None     # mode recorded in HEAD, or None if absent there
        self.mode = None          # mode to stage with; see resolve_modes()

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
    header = lines[i]
    i += 1  # skip "diff --git a/... b/..."
    status = "modified"
    is_binary = False
    a_path = b_path = None
    while i < len(lines):
        l = lines[i]
        if l.startswith(b"diff --git ") or l.startswith(b"@@"):
            break
        if l.startswith(b"--- "):
            a_path = header_path(l[4:])
        elif l.startswith(b"+++ "):
            b_path = header_path(l[4:])
        elif l.startswith(b"new file mode"):
            status = "added"
        elif l.startswith(b"deleted file mode"):
            status = "deleted"
        elif l.startswith(b"Binary files ") or l.startswith(b"GIT binary patch"):
            is_binary = True
        i += 1

    if a_path is None and b_path is None:
        # A binary diff carries no ---/+++ pair at all, only "Binary files a/X
        # and b/X differ", so recover the path from the `diff --git` header.
        # Without this a modified binary file dropped out of the inventory
        # silently -- and a plan could then call itself fully covered and print
        # "Verified" while never committing that file's change.
        recovered = path_from_diff_header(header) if is_binary else None
        if recovered is None:
            # e.g. a pure mode change with no content diff -- nothing to slice
            while i < len(lines) and not lines[i].startswith(b"diff --git "):
                i += 1
            return None, i
        path = strip_prefix(recovered, b"b/").decode("utf-8", "replace")
        fe = FileEntry(path=path, status=status, binary=True)
        while i < len(lines) and not lines[i].startswith(b"diff --git "):
            i += 1
        return fe, i

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


C_ESCAPES = {
    b"a": b"\a", b"b": b"\b", b"f": b"\f", b"n": b"\n",
    b"r": b"\r", b"t": b"\t", b"v": b"\v", b'"': b'"', b"\\": b"\\",
}


def unquote_c_style(raw):
    """Undo git's C-style path quoting (the `"a/we\\"ird"` form)."""
    body = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i:i + 1]
        if c != b"\\":
            out += c
            i += 1
        elif body[i + 1:i + 2] in C_ESCAPES:
            out += C_ESCAPES[body[i + 1:i + 2]]
            i += 2
        elif body[i + 1:i + 2].isdigit():
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            out += body[i + 1:i + 2]
            i += 2
    return bytes(out)


def header_path(raw):
    """Path out of a `--- `/`+++ ` line.

    git appends a TAB after the name when it contains a space -- without
    stripping that, every path with a space in it resolved to nothing and
    crashed on an empty HEAD baseline.
    """
    if raw.startswith(b'"'):
        return unquote_c_style(raw[:raw.rfind(b'"') + 1])
    return raw.split(b"\t", 1)[0]


def path_from_diff_header(line):
    """`diff --git a/P b/P` -> `b/P`.

    Splitting that on whitespace is ambiguous when P contains spaces, but we
    diff with --no-renames, so both halves are always the same path -- which
    makes the split recoverable by length.
    """
    rest = line[len(b"diff --git "):]
    if rest.startswith(b'"'):
        mid = rest.find(b'" "')
        return unquote_c_style(rest[mid + 2:]) if mid != -1 else None
    n = (len(rest) - 5) // 2  # len("a/") + n + len(" ") + len("b/") + n
    if n <= 0:
        return None
    return rest[2 + n + 1:] if rest[2:2 + n] == rest[2 + n + 3:] else None


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

    # -z: NUL-separated and, crucially, *unquoted*. Without it a path containing
    # a space arrives C-quoted ("untracked file.txt"), which then matches nothing
    # on disk and degrades the file to an opaque F<n>:whole blob.
    status_args = ["status", "--porcelain=v1", "-z", "-uall"]
    if rel:
        status_args += ["--"] + rel
    for record in git(root, status_args).stdout.split(b"\x00"):
        if record.startswith(b"?? "):
            files.append(make_untracked_entry(root, record[3:].decode("utf-8", "replace")))

    files.sort(key=lambda fe: fe.path)
    assign_ids(files)

    head_blobs = read_head_bytes_batch(root, [fe.path for fe in files])
    head_mode_map = head_modes(root)
    for fe in files:
        fe.head_bytes = head_blobs.get(fe.path)
        fe.working_bytes = read_working_bytes(root, fe.path)
        resolve_modes(root, fe, head_mode_map)
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


def read_head_bytes_batch(root, paths):
    """HEAD blob for each path, in one `git cat-file --batch` rather than one
    `git show` per file. Missing paths map to None."""
    result = {p: None for p in paths}
    if not paths:
        return result
    payload = b"".join(b"HEAD:" + p.encode("utf-8") + b"\n" for p in paths)
    proc = run(["git", "-C", root, "cat-file", "--batch"], input_bytes=payload, check=False)
    if proc.returncode != 0:
        return result
    out, pos = proc.stdout, 0
    for p in paths:
        nl = out.find(b"\n", pos)
        if nl == -1:
            break
        header, pos = out[pos:nl], nl + 1
        # "<oid> <type> <size>" for a hit; the echoed input + " missing" for a miss.
        if header.endswith(b" missing"):
            continue
        parts = header.rsplit(b" ", 2)
        if len(parts) != 3:
            break
        try:
            size = int(parts[2])
        except ValueError:
            break
        result[p] = out[pos:pos + size]
        pos += size + 1  # skip the LF git writes after the contents
    return result


def read_working_bytes(root, path):
    abspath = os.path.join(root, path)
    if not os.path.isfile(abspath):
        return None
    with open(abspath, "rb") as f:
        return f.read()


def head_modes(root):
    """Every path's mode in HEAD, in one `ls-tree -r`."""
    modes = {}
    proc = git(root, ["ls-tree", "-r", "-z", "HEAD"], check=False)
    if proc.returncode != 0:
        return modes
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        if path:
            modes[path.decode("utf-8")] = meta.split(b" ")[0].decode()
    return modes


def resolve_modes(root, fe, head_mode_map):
    """Decide the mode to stage `fe` with.

    The working tree is the state being reproduced, so its mode wins. Reading
    the mode from HEAD unconditionally silently discarded a `chmod +x` that
    rode along with a content edit -- and the consistency check compared only
    content, so the run still printed that it matched the working tree exactly.

    Symlinks stay on HEAD's mode: they are not specially handled (see SKILL.md),
    and read_working_bytes() resolves them to the target's *content*, so
    claiming 120000 here would stage a blob that is not a link.
    """
    fe.head_mode = head_mode_map.get(fe.path)
    abspath = os.path.join(root, fe.path)
    if os.path.isfile(abspath) and not os.path.islink(abspath):
        fe.mode = "100755" if (os.stat(abspath).st_mode & 0o111) else "100644"
    else:
        fe.mode = fe.head_mode or "100644"


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
        # Not an assert: `python -O` strips those, and this is the guard that
        # stops a corrupt reconstruction being written straight to a blob.
        if pos > before_end:
            raise RuntimeError(
                "overlapping hunks in %s (%s starts before the previous hunk ended)"
                % (fe.path, h.id)
            )
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
        # Surfaced because a mode change is not separately sliceable: it rides
        # along with whichever slice first stages the file.
        if fe.head_mode and fe.mode != fe.head_mode:
            tag += " [mode %s -> %s]" % (fe.head_mode, fe.mode)
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

    # Verify per FILE, not per plan. Leaving IDs unassigned is the documented
    # normal case (SKILL.md step 2), and gating this on whole-plan coverage
    # meant those runs -- the common ones -- committed with no byte-for-byte
    # check at all. A file whose every ID is assigned is verifiable regardless
    # of what the rest of the plan leaves pending.
    covered_ids = set(seen_count.keys())
    verified = []
    unverifiable = []
    for fe in files:
        ids = set(fe.all_ids())
        if not ids or not ids.issubset(covered_ids):
            unverifiable.append(fe.path)
            continue
        exists, content = file_state(fe, ids)
        expected_exists = fe.working_bytes is not None
        expected_content = fe.working_bytes if expected_exists else b""
        if exists != expected_exists or content != expected_content:
            raise RuntimeError(
                "internal consistency check failed for %s: reconstructed state does not match "
                "the working tree. Nothing has been committed. This indicates a bug in the plan "
                "or the tool -- please report the file path and re-run with --dry-run." % fe.path
            )
        verified.append(fe.path)

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

    mode_changes = [fe for fe in files if fe.head_mode and fe.mode != fe.head_mode]
    if mode_changes:
        print("Mode changes (ride along with the file's first slice, not separately sliceable):")
        for fe in mode_changes:
            print("  %s  %s -> %s" % (fe.path, fe.head_mode, fe.mode))

    if verified:
        print("Verified: %d fully-assigned file(s) reconstruct byte-for-byte." % len(verified))
    if unverifiable:
        print("Partially assigned, so not verifiable end-state (%d): %s"
              % (len(unverifiable), ", ".join(unverifiable)))

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
                sha = git(root, ["hash-object", "-w", "--stdin"], input_bytes=content).stdout.decode().strip()
                git(root, ["update-index", "--add", "--cacheinfo", "%s,%s,%s" % (fe.mode, sha, fe.path)])
            else:
                git(root, ["update-index", "--force-remove", "--", fe.path], check=False)
        commit_args = ["commit", "-m", sl["subject"]]
        if sl.get("body"):
            commit_args += ["-m", sl["body"]]
        git(root, commit_args)
        new_sha = git(root, ["rev-parse", "--short", "HEAD"]).stdout.decode().strip()
        print("  [%d/%d] %s  ->  %s" % (i, len(slices), new_sha, sl["subject"]))

    # `status --short`, not `diff --stat`: untracked files are first-class in
    # this tool's model, and diff omits them -- so a plan that left an untracked
    # file pending used to report "(none)" remaining.
    final = git(root, ["status", "--short", "--untracked-files=all"]).stdout.decode("utf-8", "replace").rstrip()
    print("\nDone. Still pending (should be only what you deliberately left out):")
    print(final if final else "  (nothing)")
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
