{ modulesPath, ... }:
{
  imports = [
    "${modulesPath}/virtualisation/azure-image.nix"
    ./azure.nix
  ];

  virtualisation.azureImage = {
    vmGeneration = "v2";
  };

  virtualisation.diskSize = "auto";
}
