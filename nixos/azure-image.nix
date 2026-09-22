{ modulesPath, ... }:
{
  imports = [
    "${modulesPath}/virtualisation/azure-image.nix"
    ./azure.nix
  ];

  virtualisation.azureImage = {
    vmGeneration = "v2";
  };

  # The upstream Azure image hook grows partition 1 for auto-sized images,
  # but Gen 2 uses partition 1 as the EFI system partition and root is 2.
  # Azure's guest growpart service expands the root disk after first boot.
  virtualisation.diskSize = 8192;
}
