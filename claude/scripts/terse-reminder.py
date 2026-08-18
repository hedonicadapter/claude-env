#!/usr/bin/env python3
"""UserPromptSubmit hook — re-inject terse rules as additionalContext each turn.

The Terse output style loads once, near the top of a system prompt that keeps
growing (harness guidance, remote-env instructions, org instructions, skill
listings). Everything downstream of it rewards explanatory prose, so the style
loses on recency and replies drift back to full grammar.

Delivering rules through UserPromptSubmit puts them last in context, right
before generation. The CLI's normalizeMessagesForAPI / shouldUseMidConvSystem
promotes additionalContext to a real {role:"system"} turn on models accepting
mid-conversation system messages, and falls back to a <system-reminder> wrap
elsewhere. That gate is the CLI's; not re-implemented here.

Body comes from ~/.claude/TERSE.md so style file, CLAUDE.md import, and this
reminder never drift apart. No-op unless resolved outputStyle is Terse.
"""

import json
import os
import sys

FALLBACK = """Terse output style is active — apply before writing:
drop articles (a/an/the) in every sentence including long explanations; no
preamble ("I'll…", "Let me…") and no closing recap; state facts, cut hedging.
Exceptions: user-facing strings, commit messages, external docs, search
queries. Thoroughness governs what you do, never word count."""


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def resolved_output_style(cwd):
    """Style per settings precedence: local > project > user. First hit wins."""
    candidates = [
        os.path.join(cwd, ".claude", "settings.local.json"),
        os.path.join(cwd, ".claude", "settings.json"),
        os.path.join(config_dir(), "settings.json"),
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                style = json.load(f).get("outputStyle")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        if style:
            return str(style)
    return ""


def body():
    try:
        with open(os.path.join(config_dir(), "TERSE.md"), encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return FALLBACK
    return text or FALLBACK


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    cwd = payload.get("cwd") or os.getcwd()
    if resolved_output_style(cwd).strip().lower() != "terse":
        sys.exit(0)

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": body(),
            }
        },
        sys.stdout,
    )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block a turn on hook failure.
        sys.exit(0)
