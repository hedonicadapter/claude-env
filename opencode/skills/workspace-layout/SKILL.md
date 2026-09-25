---
name: workspace-layout
description: Use when working with repositories or files on the NixOS OpenCode or Claude Code VM to identify the shared workspace and its ownership model.
---

# Shared Workspace

Work from `~/workspace`, which is the service view of `/srv/workspace` for
both the OpenCode and Claude Code services. The SSH administrator sees the same
directory at `~/workspace`.

Repositories cloned or created here are shared between the `buster`,
`opencode`, and `claude` accounts. Keep service credentials under their account
homes rather than in this workspace.
