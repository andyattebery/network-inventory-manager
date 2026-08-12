{
  lib,
  python312Packages,
  makeWrapper,
  _1password-cli,
}:

python312Packages.buildPythonApplication {
  pname = "network-inventory-manager";
  version = "2.0.1";
  pyproject = true;

  src = lib.cleanSourceWith {
    src = ./..;
    filter = path: _type:
      let
        baseName = baseNameOf path;
      in
      !(lib.elem baseName [
        ".git"
        ".github"
        ".claude"
        "docs"
        "nix"
        "config.yaml"
        "network_hosts_inventory.yaml.tpl"
        "Dockerfile"
      ]);
  };

  build-system = [
    python312Packages.setuptools
  ];

  dependencies = [
    python312Packages.requests
    python312Packages.pyyaml
  ];

  nativeBuildInputs = [ makeWrapper ];

  postFixup = ''
    wrapProgram $out/bin/network-inventory-manager \
      --prefix PATH : ${lib.makeBinPath [ _1password-cli ]}
  '';

  # Tests run as part of the build, so a deploy of this package cannot activate
  # a tree that fails them. Consumers pin this flake to a branch, not a tag, so
  # the commit that ships is not necessarily one GitHub CI ran pytest against —
  # this is the only check that always sees the deployed source.
  # Every test must therefore stay hermetic: no real `op`, no network.
  nativeCheckInputs = [
    python312Packages.pytestCheckHook
    python312Packages.responses
  ];

  meta = {
    description = "Syncs network host configuration to AdGuardHome and UniFi controllers";
    mainProgram = "network-inventory-manager";
  };
}
