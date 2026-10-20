{
  lib,
  stdenv,
  callPackage,
  callPackages,
  python312,
  makeWrapper,
  # From this flake's inputs; flake.nix passes them in.
  workspace,
  pyproject-nix,
  pyproject-build-systems,
}:

let
  op = callPackage ./op-cli.nix { };

  # The Python dependencies come from uv.lock through uv2nix, not from nixpkgs' python3Packages, so
  # the overlay ships the versions the Docker image and CI install, whatever nixpkgs the consumer
  # builds it against. Only the interpreter, and `op` above, still come from that nixpkgs.
  pythonSet =
    (callPackage pyproject-nix.build.packages {
      python = python312;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
          overrides
        ]
      );

  overrides = final: prev: {
    network-inventory-manager = prev.network-inventory-manager.overrideAttrs (old: {
      # Only what the wheel is built from, so editing the tests or docs doesn't rebuild it.
      src = lib.fileset.toSource {
        root = ./..;
        fileset = lib.fileset.unions [
          ../pyproject.toml
          ../network_inventory_manager
        ];
      };

      # The tests are their own derivation: runtime and test dependencies aren't available while
      # a package builds. The flake exposes this one as checks.<system>.pytest, CI builds it, and
      # CI moves the `release` branch consumers deploy from only when it passes.
      # Every test must stay hermetic: no real `op`, no network.
      passthru = old.passthru // {
        tests = (old.passthru.tests or { }) // {
          pytest = stdenv.mkDerivation {
            name = "${final.network-inventory-manager.name}-pytest";
            # No network_inventory_manager/ here, so the tests import the installed wheel. With it,
            # pytest's default import mode puts the source root first on sys.path (tests/ is a
            # package), and a file the wheel leaves out would still pass.
            src = lib.fileset.toSource {
              root = ./..;
              fileset = lib.fileset.unions [
                ../pyproject.toml
                ../tests
              ];
            };
            nativeBuildInputs = [
              (final.mkVirtualEnv "network-inventory-manager-pytest-env" {
                network-inventory-manager = [ "dev" ];
              })
            ];
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              pytest tests
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              touch $out
              runHook postInstall
            '';
          };
        };
      };
    });
  };

  venv = pythonSet.mkVirtualEnv "network-inventory-manager-env" workspace.deps.default;

  inherit (callPackages pyproject-nix.build.util { }) mkApplication;
in
(mkApplication {
  inherit venv;
  package = pythonSet.network-inventory-manager;
}).overrideAttrs
  (old: {
    nativeBuildInputs = old.nativeBuildInputs ++ [ makeWrapper ];

    # makeWrapper around the venv's script, not wrapProgram: wrapProgram renames the script to
    # .network-inventory-manager-wrapped, and Python takes sys.argv[0] (argparse's prog in --help
    # and usage errors) from the file it runs.
    postFixup = ''
      rm $out/bin/network-inventory-manager
      makeWrapper ${venv}/bin/network-inventory-manager $out/bin/network-inventory-manager \
        --prefix PATH : ${lib.makeBinPath [ op ]}
    '';

    passthru = old.passthru // {
      inherit pythonSet;
    };

    meta = old.meta // {
      description = "Syncs network host configuration to AdGuardHome and UniFi controllers";
      mainProgram = "network-inventory-manager";
    };
  })
