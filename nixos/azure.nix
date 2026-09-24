{ config, lib, modulesPath, pkgs, ... }:

let
  rtkPlugin = pkgs.writeText "rtk.ts" (builtins.readFile ../opencode/plugins/rtk.ts);
  terseInstructions = pkgs.writeText "TERSE.md" (builtins.readFile ../opencode/TERSE.md);
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
  }));
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
      ProcSubset = "pid";
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

  environment.systemPackages = [ pkgs.git pkgs.gh pkgs.openssh pkgs.opencode pkgs.rtk ];
}
