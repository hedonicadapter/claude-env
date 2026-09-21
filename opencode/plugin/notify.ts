// ntfy notifications on session idle / error.
// Ported from claude/scripts/notify.sh (macOS terminal-notifier path dropped —
// Azure container is Linux). Best-effort: never throws into the session.
//
// Topic from NTFY_TOPIC (or legacy CLAUDE_NTFY_TOPIC). Unset -> disabled.
// ntfy topics are unauthenticated; anyone knowing the topic can read/publish.

const TOPIC = process.env.NTFY_TOPIC || process.env.CLAUDE_NTFY_TOPIC || ""
const BASE = process.env.NTFY_BASE || "https://ntfy.sh"

async function push(title: string, message: string) {
  if (!TOPIC) return
  try {
    await fetch(`${BASE}/${TOPIC}`, {
      method: "POST",
      headers: { Title: title },
      body: message,
      signal: AbortSignal.timeout(5000),
    })
  } catch {
    // best-effort; swallow
  }
}

export const Notify = async () => ({
  "session.idle": async () => {
    await push("OpenCode", "Your turn")
  },
  "session.error": async (event: unknown) => {
    const msg = typeof event === "object" && event && "error" in event
      ? String((event as { error: unknown }).error)
      : "session error"
    await push("OpenCode — error", msg.slice(0, 200))
  },
})
