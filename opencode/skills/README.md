# OpenCode Skills

Put each declarative OpenCode skill in its own directory with an uppercase
`SKILL.md` file:

```text
opencode/skills/example/SKILL.md
```

The NixOS service copies this directory into the Nix store and registers it in
OpenCode's `skills.paths`. Run `nixos-rebuild switch` after changing a skill;
OpenCode loads skills only when it starts.
