{ config, lib, modulesPath, pkgs, ... }:

let
  rtkPlugin = pkgs.writeText "rtk.ts" (builtins.readFile ../opencode/plugins/rtk.ts);
  terseInstructions = pkgs.writeText "TERSE.md" (builtins.readFile ../opencode/TERSE.md);
  opencodeSkills = pkgs.runCommand "opencode-skills" { } ''
    mkdir -p "$out"
    cp -r ${../opencode/skills}/. "$out"
  '';
  opencodeGitConfig = pkgs.writeText "opencode-gitconfig" ''
    [credential]
        helper = !${pkgs.gh}/bin/gh auth git-credential
    [url "https://github.com/"]
        insteadOf = git@github.com:
        insteadOf = ssh://git@github.com/
  '';
  baseOpencodeConfig = builtins.fromJSON (builtins.readFile ../opencode/opencode.json);
  opencodeConfig = pkgs.writeText "opencode.json" (builtins.toJSON (baseOpencodeConfig // {
    instructions = baseOpencodeConfig.instructions ++ [ terseInstructions ];
    plugin = [ "file://${rtkPlugin}" ];
    skills.paths = [ opencodeSkills ];
  }));

  # Same ~/.claude tree install.sh overlays elsewhere, plus the OpenCode skills
  # so both agents share one skill set.
  claudeUserConfig = pkgs.runCommand "claude-user-config" { } ''
    mkdir -p "$out/skills"
    cp -r ${../claude}/. "$out"
    cp -r ${../opencode/skills}/. "$out/skills"
    rm -f "$out/skills/README.md"
  '';
  claudeHome = "/var/lib/claude-code";
  claudeTmux = "${pkgs.tmux}/bin/tmux -S /run/claude-code/tmux.sock";
  # Overlay, not replace: credentials, sessions and history live beside it.
  syncClaudeConfig = pkgs.writeShellScript "claude-code-sync-config" ''
    set -eu
    dest="$HOME/.claude"
    mkdir -p "$dest"
    cp -rT --no-preserve=mode,ownership ${claudeUserConfig} "$dest"
    find "$dest/scripts" "$dest/skills" -path '*/scripts/*' -type f -exec chmod u+x {} +
  '';
in
{
  imports = [ "${modulesPath}/virtualisation/azure-common.nix" ];

  system.stateVersion = "26.05";

  services.tailscale = {
    enable = true;
    openFirewall = true;
  };

  # The administrator's public key is provisioned out-of-band by Azure or a
  # private host module. OpenCode runs as a separate, non-admin system user.
  users.users.buster = {
    isNormalUser = true;
    extraGroups = [ "wheel" "workspace" ];
  };
  users.groups.workspace = { };
  security.sudo.wheelNeedsPassword = false;

  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "claude-code";

  users.users.opencode = {
    isSystemUser = true;
    group = "workspace";
    home = "/var/lib/opencode";
    createHome = true;
    shell = pkgs.bashInteractive;
  };

  systemd.tmpfiles.rules = [
    "d /srv/workspace 2770 buster workspace -"
    "a+ /srv/workspace - - - - u::rwx,g::rwx,g:workspace:rwx,o::---,d:u::rwx,d:g::rwx,d:g:workspace:rwx,d:o::---"
    "L+ /home/buster/workspace - - - - /srv/workspace"
    "d /var/lib/opencode/workspace 0770 opencode workspace -"
    "d ${claudeHome}/workspace 0770 claude workspace -"
  ];

  system.activationScripts.opencodeWorkspaceMigration.text = ''
    if [ -L /var/lib/opencode/workspace ]; then
      rm /var/lib/opencode/workspace
    fi
  '';

  systemd.services.opencode-web = {
    description = "OpenCode web server";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" "tailscaled.service" ];
    after = [ "network-online.target" "tailscaled.service" ];
    path = [ pkgs.git pkgs.gh pkgs.openssh pkgs.rtk ];

    serviceConfig = {
      Type = "simple";
      User = "opencode";
      Group = "workspace";
      WorkingDirectory = "/var/lib/opencode/workspace";
      Environment = [
        "HOME=/var/lib/opencode"
        "XDG_CONFIG_HOME=/var/lib/opencode/.config"
        "OPENCODE_CONFIG=${opencodeConfig}"
        "GIT_CONFIG_GLOBAL=${opencodeGitConfig}"
      ];
      ExecStart = "${pkgs.opencode}/bin/opencode serve --hostname 127.0.0.1 --port 8080";
      Restart = "always";
      RestartSec = "5s";

      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      AmbientCapabilities = "";
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      ProtectHostname = true;
      ProtectProc = "invisible";
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      RestrictNamespaces = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = [ "@system-service" "~@privileged" "~@resources" ];
      UMask = "0007";
      StateDirectory = "opencode";
      ReadWritePaths = [ "/srv/workspace" ];
      BindPaths = [ "/srv/workspace:/var/lib/opencode/workspace" ];
    };
  };

  # Claude Code runs as its own non-admin account, mirroring OpenCode. Remote
  # Control needs claude.ai subscription OAuth, which is stored under this home.
  users.users.claude = {
    isSystemUser = true;
    group = "workspace";
    home = claudeHome;
    createHome = true;
    shell = pkgs.bashInteractive;
  };

  # Remote Control server: sessions are driven from claude.ai/code and the
  # Claude app over an outbound connection, so no listener or Tailscale Serve.
  # tmux supplies the TTY its first-run prompts need; attach to answer them.
  systemd.services.claude-code-remote = {
    description = "Claude Code Remote Control server";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    # Skipped until `/login` has stored credentials; restart it afterwards.
    unitConfig.ConditionPathExists = "${claudeHome}/.claude/.credentials.json";
    path = [ pkgs.bash pkgs.coreutils pkgs.findutils pkgs.git pkgs.gh pkgs.openssh pkgs.python3 pkgs.rtk ];

    serviceConfig = {
      Type = "forking";
      User = "claude";
      Group = "workspace";
      WorkingDirectory = "${claudeHome}/workspace";
      Environment = [
        "HOME=${claudeHome}"
        "GIT_CONFIG_GLOBAL=${opencodeGitConfig}"
      ];
      ExecStartPre = syncClaudeConfig;
      # %H: session list in claude.ai/code shows host name.
      ExecStart = "${claudeTmux} new-session -d -s claude -x 200 -y 50 ${pkgs.claude-code}/bin/claude remote-control --name %H";
      ExecStop = "${claudeTmux} kill-server";
      Restart = "always";
      RestartSec = "5s";

      # No MemoryDenyWriteExecute: Bun's JIT needs writable executable pages.
      # No RestrictNamespaces: /sandbox relies on bubblewrap user namespaces.
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      AmbientCapabilities = "";
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      ProtectHostname = true;
      ProtectProc = "invisible";
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      LockPersonality = true;
      SystemCallArchitectures = "native";
      UMask = "0007";
      StateDirectory = "claude-code";
      RuntimeDirectory = "claude-code";
      ReadWritePaths = [ "/srv/workspace" ];
      BindPaths = [ "/srv/workspace:${claudeHome}/workspace" ];
    };
  };

  # Recreates `tailscale serve --bg --https=443 http://127.0.0.1:8080`.
  # The node is authenticated separately, either interactively or with an
  # age-encrypted services.tailscale.authKeyFile outside this public repository.
  systemd.services.tailscale-opencode-serve = {
    description = "Expose OpenCode with Tailscale Serve";
    wantedBy = [ "multi-user.target" ];
    wants = [ "tailscaled.service" "opencode-web.service" ];
    after = [ "tailscaled.service" "opencode-web.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      Restart = "on-failure";
      RestartSec = "10s";
      ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --https=443 http://127.0.0.1:8080";
    };
  };

  environment.systemPackages = [ pkgs.claude-code pkgs.git pkgs.gh pkgs.openssh pkgs.opencode pkgs.rtk pkgs.tmux ];
}
