{
  description = "Network inventory manager - syncs hosts to AdGuardHome and UniFi";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      # Not legacyPackages: nix/package.nix wraps the 1Password CLI onto the program's PATH,
      # and that package is unfree, so nixpkgs refuses to evaluate it under the default
      # config. Without this the flake's own packages.default cannot be built at all -- which
      # went unnoticed because the only consumer builds it through an overlay, inside a NixOS
      # config that declares its own allowUnfreePredicate. A narrow predicate rather than
      # allowUnfree, so nothing else slips through.
      pkgsFor = system: import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = pkg:
          builtins.elem (nixpkgs.lib.getName pkg) [ "1password-cli" ];
      };
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

      # `key` is what makes this module safe to import from more than one place. NixOS dedups
      # imports by key, and a path's key is its path -- but `import ./nix/module.nix self`
      # evaluates to a lambda, and a lambda gets a fresh key per import site. Without the key,
      # a config importing it twice -- say a host module and a role module that both want it --
      # declares services.network-inventory-manager.* twice and evaluation fails with
      # "The option `services.network-inventory-manager.package' ... is already declared".
      # lib.setDefaultModuleLocation does NOT fix this: it sets _file, which only affects
      # error messages, not the dedup key.
      nixosModules.default = {
        key = "network-inventory-manager";
        imports = [ (import ./nix/module.nix self) ];
      };

      # Regression test for the `key` above: this module must survive being imported twice.
      #
      # It forces services.network-inventory-manager.package specifically. That is the option
      # the duplicate declaration reports, and forcing something cheaper -- `.enable`, say --
      # succeeds even when the module IS broken, so it would be a check that can never fail.
      #
      # It also has to live in `checks` to be worth anything: `nix flake check` does not
      # evaluate nixosModules at all. nix's checkModule only calls forceValue on the
      # attribute, which for a lambda yields the lambda unapplied, so a broken module passes
      # it. Only `checks.<system>.<name>` is really built.
      #
      # Linux only: supportedSystems above includes darwin, and nixosSystem cannot evaluate
      # for those.
      checks = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system: {
        module-imports-twice =
          let
            sys = nixpkgs.lib.nixosSystem {
              modules = [
                self.nixosModules.default
                self.nixosModules.default
                {
                  nixpkgs.hostPlatform = system;
                  system.stateVersion = nixpkgs.lib.trivial.release;
                  services.network-inventory-manager = {
                    enable = true;
                    settings = {
                      dsmUrl = "http://dsm.invalid";
                      adguardhomeUrl = "http://adguard.invalid";
                      adguardhomeUsername = "check";
                      unifiUrl = "https://unifi.invalid";
                      unifiUsername = "check";
                      configRepo = "owner/repo";
                      repoConfigPath = "inventory.yaml";
                    };
                  };
                }
              ];
            };
          in
          (pkgsFor system).writeText "nim-imports-twice-ok"
            sys.config.services.network-inventory-manager.package.pname;
      });

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
