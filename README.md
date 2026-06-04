# network-inventory-manager

Declarative network configuration sync for homelabs. Reads a YAML host inventory (with 1Password-templated MAC addresses) and service discovery from [Dashboard Services Manager](https://github.com/andyattebery/dashboard-services-manager), then pushes desired state to AdGuardHome (DNS rewrites + clients) and UniFi (DHCP reservations).

Runs as a Docker container on a configurable interval, with an HTTP endpoint to trigger syncs on demand.

## Quick start

1. Create a `config.yaml`:

```yaml
local_config_path: /config/network_hosts_inventory.yaml.tpl
dsm_url: https://dashboard-services-manager.example.com
adguardhome_url: https://adguardhome.example.com
adguardhome_username: admin
adguardhome_password: your-password
unifi_url: https://192.168.1.1
unifi_username: admin
unifi_password: your-password
op_service_account_token: ops_...
```

2. Run:

```bash
docker run -d \
  -v ./config.yaml:/config/config.yaml:ro \
  -v ./network_hosts_inventory.yaml.tpl:/config/network_hosts_inventory.yaml.tpl:ro \
  -p 8080:8080 \
  ghcr.io/andyattebery/network-inventory-manager
```

3. Trigger a sync manually:

```bash
curl -X POST http://localhost:8080/sync
```

## Documentation

- [User guide](docs/user-guide.md) — inventory format, settings, running, error handling
- [Development](docs/development.md) — project structure, tests, CI
