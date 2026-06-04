{
  description = "Network inventory manager - syncs hosts to AdGuardHome and UniFi";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: nixpkgs.legacyPackages.${system};
    in
    {
      packages = forAllSystems (system: let
        pkgs = pkgsFor system;
      in {
        default = pkgs.callPackage ./nix/package.nix { };
        network-inventory-manager = pkgs.callPackage ./nix/package.nix { };
      });

      overlays.default = final: _prev: {
        network-inventory-manager = final.callPackage ./nix/package.nix { };
      };

      nixosModules.default = import ./nix/module.nix self;

      devShells = forAllSystems (system: let
        pkgs = pkgsFor system;
      in {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python312.withPackages (ps: [
              ps.requests
              ps.pyyaml
              ps.pytest
              ps.responses
              ps.setuptools
            ]))
            pkgs._1password-cli
          ];
        };
      });
    };
}
