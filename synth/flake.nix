{
  description = "Hot-reloadable MIDI synth (engine + live patch.py)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
      # numpy for DSP, sounddevice (pulls PortAudio), mido + python-rtmidi for MIDI.
      pyFor = pkgs: pkgs.python3.withPackages (ps: with ps; [
        numpy sounddevice mido python-rtmidi
      ]);
    in
    {
      packages = forAll (pkgs: {
        default = pkgs.writeShellApplication {
          name = "synth";
          runtimeInputs = [ (pyFor pkgs) ];
          # Whole synth dir goes to the store so a store-only run still finds a
          # fallback patch.py; run from your checkout to hot-reload the writable one.
          text = ''exec python ${./.}/engine.py "$@"'';
        };
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.system}.default}/bin/synth";
        };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pyFor pkgs) ];
          shellHook = ''
            echo "synth dev shell. Run:  python engine.py          (uses ./patch.py)"
            echo "                        python engine.py --list   (devices)"
            echo "                        python offline_test.py    (no hardware)"
          '';
        };
      });
    };
}
