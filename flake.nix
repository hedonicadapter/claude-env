{
  description = "Portable Claude Code and OpenCode configuration";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      nixosConfigurations = {
        azure-aarch64 = nixpkgs.lib.nixosSystem {
          system = "aarch64-linux";
          modules = [ ./nixos/azure-image.nix ];
        };
        azure-x86_64 = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [ ./nixos/azure-image.nix ];
        };
      };

      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          azureImage = nixpkgs.lib.nixosSystem {
            inherit system;
            modules = [ ./nixos/azure-image.nix ];
          };
          opencodeConfig = pkgs.writeText "opencode.json" (builtins.readFile ./opencode/opencode.json);
          opencodeUnit = pkgs.writeText "opencode-web@.service" ''
            [Unit]
            Description=OpenCode web server for %i
            Wants=network-online.target tailscaled.service
            After=network-online.target tailscaled.service tailscale-online.target

            [Service]
            Type=simple
            User=%i
            Group=%i
            WorkingDirectory=/home/%i/workspace
            Environment=HOME=/home/%i
            Environment=XDG_CONFIG_HOME=/home/%i/.config
            ExecStart=/home/%i/.nix-profile/bin/nix --extra-experimental-features 'nix-command flakes' run github:hedonicadapter/claude-env/main#serve
            Restart=always
            RestartSec=5
            NoNewPrivileges=true
            CapabilityBoundingSet=
            AmbientCapabilities=
            PrivateTmp=true
            ProtectKernelTunables=true
            ProtectKernelModules=true
            ProtectControlGroups=true
            RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
            LockPersonality=true

            [Install]
            WantedBy=multi-user.target
          '';
        in
        {
          default = pkgs.opencode;

          serve = pkgs.writeShellApplication {
            name = "claude-env-serve";
            runtimeInputs = [ pkgs.opencode ];
            text = ''
              export OPENCODE_CONFIG=${opencodeConfig}
              exec opencode web --hostname 127.0.0.1 --port 8080
            '';
          };

          systemd-unit = opencodeUnit;
          azure-image = azureImage.config.system.build.azureImage;
        });
    };
}
