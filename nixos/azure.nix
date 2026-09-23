{ config, lib, modulesPath, pkgs, ... }:

let
  opencodeConfig = pkgs.writeText "opencode.json" (builtins.readFile ../opencode/opencode.json);
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
    extraGroups = [ "wheel" ];
  };
  security.sudo.wheelNeedsPassword = false;

  users.groups.opencode = { };
  users.users.opencode = {
    isSystemUser = true;
    group = "opencode";
    home = "/var/lib/opencode";
    createHome = true;
  };

  systemd.tmpfiles.rules = [
    "d /srv/workspace 0750 opencode opencode -"
  ];

  systemd.services.opencode-web = {
    description = "OpenCode web server";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" "tailscaled.service" ];
    after = [ "network-online.target" "tailscaled.service" ];

    serviceConfig = {
      Type = "simple";
      User = "opencode";
      Group = "opencode";
      WorkingDirectory = "/srv/workspace";
      Environment = [
        "HOME=/var/lib/opencode"
        "XDG_CONFIG_HOME=/var/lib/opencode/.config"
        "OPENCODE_CONFIG=${opencodeConfig}"
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
      UMask = "0077";
      StateDirectory = "opencode";
      ReadWritePaths = [ "/srv/workspace" ];
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

  environment.systemPackages = [ pkgs.git pkgs.opencode ];
}
