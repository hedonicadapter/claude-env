{
  description = "Portable Claude Code and OpenCode configuration";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          opencodeConfig = pkgs.writeText "opencode.json" (builtins.readFile ./opencode/opencode.json);
           opencodeUnit = pkgs.writeText "opencode-web@.service" ''
            [Unit]
            Description=OpenCode web server for %i
            Wants=claude-env-refresh@%i.service network-online.target tailscaled.service
            After=claude-env-refresh@%i.service network-online.target tailscaled.service tailscale-online.target

            [Service]
            Type=simple
            User=%i
            Group=%i
            WorkingDirectory=/home/%i/workspace
            Environment=HOME=/home/%i
            Environment=XDG_CONFIG_HOME=/home/%i/.config
            Environment=OPENCODE_CONFIG=${opencodeConfig}
            ExecStart=${pkgs.opencode}/bin/opencode web --hostname 127.0.0.1 --port 8080
            Restart=always
            RestartSec=5
            NoNewPrivileges=true

            [Install]
            WantedBy=multi-user.target
          '';
          refreshUnit = pkgs.writeText "claude-env-refresh@.service" ''
            [Unit]
            Description=Refresh claude-env deployment for %i
            Wants=network-online.target tailscaled.service
            After=network-online.target tailscaled.service tailscale-online.target
            Before=opencode-web@%i.service

            [Service]
            Type=oneshot
            User=%i
            Group=%i
            Environment=HOME=/home/%i
            Environment=XDG_CONFIG_HOME=/home/%i/.config
            ExecStart=/home/%i/.nix-profile/bin/nix --extra-experimental-features 'nix-command flakes' run github:hedonicadapter/claude-env/main#bootstrap %i

            [Install]
            WantedBy=multi-user.target
          '';
        in
        {
          default = pkgs.opencode;

          bootstrap = pkgs.writeShellApplication {
            name = "claude-env-bootstrap";
            runtimeInputs = [ pkgs.coreutils pkgs.systemd ];
            text = ''
              if [ "$#" -gt 1 ]; then
                echo "usage: claude-env-bootstrap [user]" >&2
                exit 64
              fi

              user="''${1:-$USER}"
              if ! id "$user" >/dev/null 2>&1; then
                echo "claude-env-bootstrap: user '$user' does not exist" >&2
                exit 1
              fi

              sudo install -Dm644 ${opencodeUnit} /etc/systemd/system/opencode-web@.service
              sudo install -Dm644 ${refreshUnit} /etc/systemd/system/claude-env-refresh@.service
              sudo systemctl daemon-reload
              sudo systemctl enable --now tailscaled.service
              sudo systemctl enable "claude-env-refresh@$user.service"
              sudo systemctl enable "opencode-web@$user.service"
            '';
          };
        });
    };
}
