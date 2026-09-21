// Best-effort rtk (Rust Token Killer) bash rewrite for OpenCode.
//
// Claude Code used a PreToolUse hook running `rtk hook claude`, which returns an
// updatedInput. rtk has no equivalent OpenCode mode, so this plugin approximates
// it: prefix a known set of read-only verbs with `rtk ` so their output is
// token-filtered. If rtk is missing or is the wrong crate, it silently no-ops
// (see AGENTS.md name-collision note).
//
// This is a PARTIAL port. It does not reproduce rtk's full command coverage or
// its output-format guarantees. Verify savings with `rtk gain`. See MIGRATION.md.

// Verbs rtk wraps and that are safe to prefix idempotently.
const WRAP = ["git", "ls", "grep", "rg", "cat", "find", "cargo", "npm", "docker"]

let rtkOk: boolean | null = null

async function haveRtk($: (s: TemplateStringsArray, ...a: unknown[]) => Promise<{ exitCode: number }>): Promise<boolean> {
  if (rtkOk !== null) return rtkOk
  try {
    const r = await $`command -v rtk`
    rtkOk = r.exitCode === 0
  } catch {
    rtkOk = false
  }
  return rtkOk
}

export const Rtk = async ({ $ }: { $: (s: TemplateStringsArray, ...a: unknown[]) => Promise<{ exitCode: number }> }) => ({
  "tool.execute.before": async (
    input: { tool: string },
    output: { args: { command?: string } },
  ) => {
    if (input.tool !== "bash" || !output.args.command) return
    if (!(await haveRtk($))) return

    const cmd = output.args.command
    const first = cmd.trimStart().split(/\s+/)[0]
    // Already proxied, or a pipeline/compound — leave alone.
    if (first === "rtk" || /[|&;<>]/.test(cmd)) return
    if (WRAP.includes(first)) {
      output.args.command = "rtk " + cmd.trimStart()
    }
  },
})
