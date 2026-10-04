# The 1Password CLI release pinned in op-cli.toml, which the Dockerfile reads too.
#
# The version has to come from the pin, not from nixpkgs: the overlay builds this against
# the consumer's nixpkgs, so taking `_1password-cli` as-is ships whatever `op` that nixpkgs
# happens to carry -- network-01 ran 2.34.0 while the Dockerfile pinned 2.40.0. Overriding
# nixpkgs' package keeps its install steps and its versionCheckHook, which fails the build
# unless the binary reports the pinned version.
{
  lib,
  stdenv,
  fetchurl,
  unzip,
  _1password-cli,
}:

let
  pin = builtins.fromTOML (builtins.readFile ../op-cli.toml);
  # Every file the pin names, not just this platform's. Exposed in passthru so the flake's
  # op-cli-pin check can fetch all of them from one Linux runner.
  sources = lib.mapAttrs (
    key: sha256:
    fetchurl {
      url = "https://cache.agilebits.com/dist/1P/op2/pkg/v${pin.version}/op_${key}_v${pin.version}.${
        if key == "apple_universal" then "pkg" else "zip"
      }";
      inherit sha256;
    }
  ) pin.sha256;
  inherit (stdenv.hostPlatform) system;
  platform =
    {
      x86_64-linux = "linux_amd64";
      aarch64-linux = "linux_arm64";
      aarch64-darwin = "apple_universal";
    }
    .${system} or (throw "op-cli.toml pins no 1Password CLI for ${system}");
in
_1password-cli.overrideAttrs (
  old:
  {
    inherit (pin) version;
    src = sources.${platform};
    passthru = (old.passthru or { }) // {
      inherit sources;
    };
  }
  # nixpkgs fetches the zips with fetchzip, whose hash covers the unpacked files. The pin
  # hashes the zip itself so the Dockerfile can check the same value with sha256sum, which
  # leaves unpacking to this build. The zip has no top-level directory, hence sourceRoot.
  // lib.optionalAttrs stdenv.hostPlatform.isLinux {
    nativeBuildInputs = old.nativeBuildInputs ++ [ unzip ];
    sourceRoot = ".";
  }
)
