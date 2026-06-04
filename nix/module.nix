flake:

{ config, lib, pkgs, ... }:

let
  cfg = config.services.network-inventory-manager;

  settingsFormat = pkgs.formats.yaml { };

  settingsAttrs = lib.filterAttrs (_: v: v != null && v != "") {
    dsm_url = cfg.settings.dsmUrl;
    adguardhome_url = cfg.settings.adguardhomeUrl;
    adguardhome_username = cfg.settings.adguardhomeUsername;
    unifi_url = cfg.settings.unifiUrl;
    unifi_username = cfg.settings.unifiUsername;
    unifi_site = cfg.settings.unifiSite;
    local_config_path = cfg.settings.localConfigPath;
    config_repo = cfg.settings.configRepo;
    repo_config_path = cfg.settings.repoConfigPath;
    config_branch = cfg.settings.configBranch;
    outputs = lib.concatStringsSep "," cfg.settings.outputs;
    port = cfg.settings.port;
  };

  generatedConfig = settingsFormat.generate "network-inventory-manager-config.yaml" settingsAttrs;

  configFile =
    if cfg.settingsFile != null
    then cfg.settingsFile
    else generatedConfig;
in
{
  options.services.network-inventory-manager = {
    enable = lib.mkEnableOption "network-inventory-manager";

    package = lib.mkOption {
      type = lib.types.package;
      default = flake.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "flake.packages.\${pkgs.stdenv.hostPlatform.system}.default";
      description = "The network-inventory-manager package to use.";
    };

    settingsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Path to a pre-existing settings YAML file.
        When set, individual settings options are not used.
      '';
    };

    settings = {
      dsmUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Dashboard Services Manager URL.";
      };

      adguardhomeUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "AdGuard Home API URL.";
      };

      adguardhomeUsername = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "AdGuard Home username.";
      };

      unifiUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "UniFi controller URL.";
      };

      unifiUsername = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "UniFi controller username.";
      };

      unifiSite = lib.mkOption {
        type = lib.types.str;
        default = "default";
        description = "UniFi site name.";
      };

      localConfigPath = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Path to local inventory YAML template file.";
      };

      configRepo = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "GitHub repo (owner/name) containing the inventory file.";
      };

      repoConfigPath = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Path within the GitHub repo to the inventory file.";
      };

      configBranch = lib.mkOption {
        type = lib.types.str;
        default = "main";
        description = "Git branch to fetch inventory from.";
      };

      outputs = lib.mkOption {
        type = lib.types.listOf (lib.types.enum [ "adguardhome" "unifi" ]);
        default = [ "adguardhome" "unifi" ];
        description = "Which output targets to sync to.";
      };

      syncInterval = lib.mkOption {
        type = lib.types.int;
        default = 1800;
        description = "Seconds between sync cycles. 0 = run once and exit.";
      };

      verbose = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Enable verbose/debug logging.";
      };

      port = lib.mkOption {
        type = lib.types.port;
        default = 8080;
        description = "HTTP server port for /sync and /health endpoints.";
      };
    };

    dryRun = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Log changes without applying them.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Path to an environment file containing secrets as KEY=VALUE lines.
        Expected keys: ADGUARDHOME_PASSWORD, UNIFI_PASSWORD.
        Optional: OP_SERVICE_ACCOUNT_TOKEN, GITHUB_TOKEN.
      '';
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the HTTP server port in the firewall.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.settingsFile != null
          || cfg.settings.localConfigPath != null
          || (cfg.settings.configRepo != null && cfg.settings.repoConfigPath != null);
        message = ''
          services.network-inventory-manager: must set one of:
            - settingsFile
            - settings.localConfigPath
            - settings.configRepo + settings.repoConfigPath
        '';
      }
      {
        assertion = cfg.settingsFile != null || (
          cfg.settings.dsmUrl != ""
          && cfg.settings.adguardhomeUrl != ""
          && cfg.settings.adguardhomeUsername != ""
          && cfg.settings.unifiUrl != ""
          && cfg.settings.unifiUsername != ""
        );
        message = ''
          services.network-inventory-manager: when settingsFile is not used,
          dsmUrl, adguardhomeUrl, adguardhomeUsername, unifiUrl, and
          unifiUsername are required.
        '';
      }
    ];

    systemd.services.network-inventory-manager = {
      description = "Network Inventory Manager";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        DynamicUser = true;
        StateDirectory = "network-inventory-manager";

        ExecStart = lib.concatStringsSep " " (
          [
            (lib.getExe cfg.package)
            "--config"
            "${configFile}"
            "--interval"
            (toString cfg.settings.syncInterval)
            "--port"
            (toString cfg.settings.port)
          ]
          ++ lib.optional cfg.settings.verbose "--verbose"
          ++ lib.optional cfg.dryRun "--dry-run"
        );

        Restart = "on-failure";
        RestartSec = 30;

        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictNamespaces = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
      }
      // lib.optionalAttrs (cfg.environmentFile != null) {
        EnvironmentFile = cfg.environmentFile;
      }
      // lib.optionalAttrs (cfg.settings.localConfigPath != null) {
        ReadOnlyPaths = [ cfg.settings.localConfigPath ];
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.settings.port ];
  };
}
