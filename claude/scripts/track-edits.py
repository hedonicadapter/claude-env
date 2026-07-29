import difflib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

# Files whose *content* must never be copied anywhere. Everything below this
# module writes a full plaintext snapshot plus the literal patch text into the
# edit-groups store, so tracking a credential file means two more plaintext
# copies of it on disk. The store now lives outside the work tree (see
# store_dir), which makes those copies un-committable — but un-committable is
# not the same as safe to hold a private key. Mirrors permissions.deny in
# settings.json: that stops Claude reading these, this stops an edit made by
# anything else from being copied.
SENSITIVE_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "*_rsa", "*_dsa", "*_ecdsa", "*_ed25519", "*.tfvars",
)
SENSITIVE_DIRS = ("secrets", ".ssh", ".aws", ".gnupg")

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "description": "kebab-case feature/vertical-slice slug, 2-4 words",
        },
        "description": {
            "type": "string",
            "description": "short (<12 word) summary of the feature/intention",
        },
    },
    "required": ["slug", "description"],
}


def store_dir(cwd):
    """Where this work tree's grouping hints and snapshots live.

    Deliberately OUTSIDE the work tree. This directory holds a full plaintext
    copy of every tracked file plus verbatim conversation text; while it sat at
    .claude/edit-groups/ the only thing keeping that out of a commit was a
    generated .gitignore containing `*`, which one `git add -f`, one `tar` of
    the project directory, or one tool that ignores gitignores defeats.

    Named after the work-tree path so the store stays greppable, with a hash
    suffix because the flattening alone collides: /a/b-c and /a/b/c both render
    as -a-b-c, and a collision silently merges two repos' snapshots.
    """
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    # realpath, not abspath: the hook gets its cwd from the event while
    # --print-store gets it from os.getcwd(), and on macOS those disagree the
    # moment anything sits behind a symlink (/tmp vs /private/tmp). Two spellings
    # of one work tree would mean two stores, and the reader would find neither
    # the writer's hints nor its snapshots.
    cwd = os.path.realpath(cwd)
    digest = hashlib.sha256(cwd.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    key = cwd.replace(os.sep, "-") + "-" + digest
    return os.path.join(config, "edit-groups", key)


def is_sensitive(rel_path):
    parts = rel_path.split(os.sep)
    if any(p in SENSITIVE_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], pat) for pat in SENSITIVE_NAMES)


def clean_description(text):
    """Flatten model-authored text before it is stored.

    This string is replayed into every later classifier prompt and read by
    /commit-slices to seed commit subjects shown to the main session. It is
    derived from `intention`, which is verbatim conversation text and can carry
    content from a web fetch or a file — so it is untrusted, and it does not
    get to smuggle newlines or control characters into any of those places.
    """
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:100]


def snapshot_path(groups_dir, rel_path):
    safe = rel_path.replace(os.sep, "__")
    return os.path.join(groups_dir, ".snapshots", safe)


def read_baseline(cwd, groups_dir, rel_path):
    snap = snapshot_path(groups_dir, rel_path)
    if os.path.exists(snap):
        with open(snap) as f:
            return f.read()
    try:
        result = subprocess.run(
            # "HEAD:./<path>" — the leading ./ is what makes git resolve the path
            # relative to -C's cwd. A bare "HEAD:<path>" is rooted at the REPO
            # TOP, so from a subdirectory it reads some other file as the
            # baseline (or nothing, making the whole file look newly added).
            ["git", "-C", cwd, "show", f"HEAD:./{rel_path}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return ""


def write_snapshot(groups_dir, rel_path, content):
    snap = snapshot_path(groups_dir, rel_path)
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    tmp = snap + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, snap)


def diff_hunks(old_text, new_text):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, n=0, lineterm=""))
    hunks = []
    current = None
    for line in diff:
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                continue
            if current is not None:
                hunks.append(current)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            current = {
                "start_line": new_start,
                "line_count": new_count,
                "old_start_line": old_start,
                "old_line_count": old_count,
                "patch_lines": [line],
            }
        elif line.startswith("---") or line.startswith("+++"):
            continue
        elif current is not None:
            current["patch_lines"].append(line)
    if current is not None:
        hunks.append(current)
    for hunk in hunks:
        hunk["patch"] = "\n".join(hunk.pop("patch_lines")) + "\n"
    return hunks


def get_hunks(cwd, groups_dir, rel_path):
    lock_path = snapshot_path(groups_dir, rel_path) + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            baseline = read_baseline(cwd, groups_dir, rel_path)
            try:
                with open(os.path.join(cwd, rel_path)) as f:
                    current_content = f.read()
            except Exception:
                current_content = ""
            hunks = diff_hunks(baseline, current_content)
            write_snapshot(groups_dir, rel_path, current_content)
            return hunks
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def extract_intention(transcript_path, max_messages=2):
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    texts = []
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") != "user":
                continue
            content = entry.get("message", {}).get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for item in content:
                    text = item.get("text") if isinstance(item, dict) else None
                    if text and item.get("type") == "text" and not text.startswith("<"):
                        texts.append(text)
            if len(texts) >= max_messages:
                break
    except Exception:
        pass
    return " / ".join(reversed(texts))[:1000]


def load_categories(groups_dir):
    categories = []
    for name in os.listdir(groups_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(groups_dir, name)) as f:
                data = json.load(f)
            categories.append({"slug": name[:-5], "description": data.get("description", "")})
        except Exception:
            continue
    return categories


def classify(categories, file_path, tool_name, hunks, intention):
    cat_list = "\n".join(f"- {c['slug']}: {c['description']}" for c in categories) or "(none yet)"
    hunk_desc = ", ".join(f"line {h['start_line']} (+{h['line_count']})" for h in hunks) or "whole file"
    prompt = (
        "You are grouping code edits into short-lived \"vertical slice\" categories "
        "so they can later be split into separate commits by feature/intention.\n\n"
        f"Existing categories:\n{cat_list}\n\n"
        "New edit:\n"
        f"- file: {file_path}\n"
        f"- tool: {tool_name}\n"
        f"- changed: {hunk_desc}\n"
        f"- recent conversation context: {intention or '(none)'}\n\n"
        "Pick the best existing category if this edit clearly belongs to it "
        "(reuse its slug and description exactly), otherwise invent a new slug."
    )
    try:
        proc = subprocess.run(
            [
                "claude", "-p", prompt,
                "--safe-mode",
                "--tools", "",
                "--no-session-persistence",
                "--model", "haiku",
                "--effort", "low",
                "--output-format", "json",
                "--json-schema", json.dumps(CLASSIFY_SCHEMA),
            ],
            capture_output=True, text=True, timeout=60,
            # This child is a full Claude Code session: it reads the same
            # settings.json and fires its own SessionStart and Stop hooks — a
            # complete ~/.claude reinstall plus a notification, per edit.
            # --tools "" stops it editing; only this stops its hooks.
            env={**os.environ, "CLAUDE_ENV_NESTED": "1"},
        )
    except Exception as exc:
        sys.stderr.write(f"claude-track-edits: classifier did not run: {exc}\n")
        return "uncategorized", "Edits not yet classified"

    # Report rather than swallow: an unsupported CLI flag silently degraded
    # every edit to "uncategorized" forever, with no way to notice.
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:300]
        sys.stderr.write(
            f"claude-track-edits: classifier exited {proc.returncode}: {detail}\n"
        )
        return "uncategorized", "Edits not yet classified"

    try:
        data = json.loads(proc.stdout)
        out = data.get("structured_output") or {}
        slug = re.sub(r"[^a-z0-9-]+", "-", out.get("slug", "").lower()).strip("-")
        if slug:
            return slug, clean_description(out.get("description", ""))
        sys.stderr.write("claude-track-edits: classifier returned no usable slug\n")
    except Exception as exc:
        sys.stderr.write(f"claude-track-edits: unparseable classifier output: {exc}\n")
    return "uncategorized", "Edits not yet classified"


def append_record(groups_dir, category, description, record):
    path = os.path.join(groups_dir, f"{category}.json")
    lock_path = path + ".lock"
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
            else:
                data = {"category": category, "description": description, "edits": []}
            if description and not data.get("description"):
                data["description"] = clean_description(description)
            data["edits"].append(record)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def main():
    # Re-entry guard: classify() spawns `claude -p`, which is a full session and
    # fires this very PostToolUse hook again.
    if os.environ.get("CLAUDE_ENV_NESTED"):
        return

    event = json.load(sys.stdin)
    session_id = event.get("session_id", "unknown")
    transcript_path = event.get("transcript_path", "")
    cwd = event.get("cwd") or os.getcwd()
    tool_name = event.get("tool_name", "")
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not file_path:
        return

    rel_path = os.path.relpath(file_path, cwd) if os.path.isabs(file_path) else file_path
    # Track only files inside repo work tree. Out-of-cwd edits (memory,
    # scratchpad, absolute paths) produced junk ".." entries, empty baselines.
    if rel_path == ".." or rel_path.startswith(".." + os.sep):
        return
    # The store is out of tree now, so this only catches leftovers from before
    # the move. Cheap, and stops an old in-repo store being tracked as source.
    if rel_path.startswith(".claude/edit-groups"):
        return
    # Never snapshot a credential file. Losing the grouping hint for one is the
    # cheap side of this trade.
    if is_sensitive(rel_path):
        return

    # Skip non-git cwd: no HEAD to diff against, nothing downstream to consume.
    try:
        inside = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return
    except Exception:
        return

    # Out of tree, so there is no longer a .gitignore to write and no way for
    # this scratch state to contaminate the diff /commit-slices is meant to
    # slice. That self-ignore file used to be the only thing standing between a
    # plaintext snapshot of every edited file and a commit.
    groups_dir = store_dir(cwd)
    try:
        os.makedirs(groups_dir, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(
            f"claude-track-edits: cannot create {groups_dir}, not tracking this edit: {exc}\n"
        )
        return
    # 0700 on the store root rather than just this work tree's dir: the root
    # aggregates plaintext from every repo on the box. Best-effort — worth
    # tightening a loose pre-existing dir, not worth dropping the edit over.
    try:
        os.chmod(os.path.dirname(groups_dir), 0o700)
    except OSError:
        pass

    hunks = get_hunks(cwd, groups_dir, rel_path)
    if not hunks:
        return
    intention = extract_intention(transcript_path)
    categories = load_categories(groups_dir)
    category, description = classify(categories, rel_path, tool_name, hunks, intention)

    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "transcript_path": transcript_path,
        "tool": tool_name,
        "file": rel_path,
        "hunks": hunks,
        "intention": intention,
    }
    append_record(groups_dir, category, description, record)


if __name__ == "__main__":
    # /commit-slices has to locate the store. Exposing it here keeps the key
    # derivation in one place — duplicating it in a markdown file guarantees the
    # two drift, and a reader looking in the wrong directory finds no hints and
    # silently slices everything as uncategorized.
    if len(sys.argv) > 1 and sys.argv[1] == "--print-store":
        print(store_dir(sys.argv[2] if len(sys.argv) > 2 else os.getcwd()))
        sys.exit(0)
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"claude-track-edits: {exc}\n")
    sys.exit(0)
