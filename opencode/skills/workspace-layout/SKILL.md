---
name: workspace-layout
description: Use when working with repositories or files on the NixOS OpenCode VM to identify the shared workspace and its ownership model.
---

# Shared Workspace

Work from `~/workspace`, which is the OpenCode service view of `/srv/workspace`.
The SSH administrator sees the same directory at `~/workspace`.

Repositories cloned or created here are shared between the `buster` and
`opencode` accounts. Keep service credentials under their account homes rather
than in this workspace.
