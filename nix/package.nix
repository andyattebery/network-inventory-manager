{
  lib,
  python312Packages,
  makeWrapper,
  _1password-cli,
}:

python312Packages.buildPythonApplication {
  pname = "network-inventory-manager";
  version = "0.1.3";
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

  doCheck = false;

  meta = {
    description = "Syncs network host configuration to AdGuardHome and UniFi controllers";
    mainProgram = "network-inventory-manager";
  };
}
