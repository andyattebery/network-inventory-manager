# User guide

## How it works

Each sync cycle:

1. **Load inventory** — fetch the host YAML template (from a local file or GitHub), resolve 1Password `{{ op://... }}` references for MAC addresses via `op inject`
2. **Fetch DSM services** — query Dashboard Services Manager for running services and their hostnames
3. **Discover UniFi clients** — query UniFi for active and configured clients (phones, IoT devices, etc.)
4. **Build desired state** — combine all sources into lists of DNS rewrites, AdGuardHome clients, and DHCP reservations
5. **Sync to AdGuardHome** — diff desired rewrites and clients against current state, add/remove/update
6. **Sync to UniFi** — diff desired DHCP reservations against current static leases by MAC, create/update

If any input source is unavailable, the sync proceeds with degraded data and blocks removals to prevent deleting entries that should still exist.

## Inventory file format

The inventory is a YAML template with three sections:

```yaml
homelab_domain: example.com

homelab_hosts:
  server-01:
    ip: 192.168.1.10
    mac: {{ op://Home Lab/server-01/hardware/mac address }}
  server-02:
    ip: 192.168.1.11
  alias-for-server-01:
    ip: 192.168.1.10
    mac: {{ op://Home Lab/server-01/hardware/mac address }}
    skip_dhcp: true

other_hosts:
  - hostname: offsite-nas.tailnet.ts.net
    ip: 100.1.2.3

services:
  grafana:
    hostname: server-01
```

### `homelab_hosts`

Each host gets:
- A DNS rewrite: `{name}.{homelab_domain}` pointing to `ip`
- An AdGuardHome client entry (first host at each IP wins the name)
- A DHCP reservation in UniFi (if `mac` is present and `skip_dhcp` is not `true`)

Fields:
- `ip` (required) — the host's IP address
- `mac` (optional) — MAC address, typically a 1Password `{{ op://... }}` reference
- `skip_dhcp` (optional, default `false`) — if `true`, the host gets DNS and client entries but no DHCP reservation (useful for aliases that share a MAC with another host)

### `other_hosts`

Hosts on a different domain (e.g., Tailscale). Each gets a DNS rewrite and AdGuardHome client. No DHCP.

### `services`

Services that should have DNS rewrites but aren't discovered by DSM. Each entry creates a rewrite `{service_name}.{homelab_domain}` pointing to the IP of the referenced `hostname`.

Inventory services take priority over DSM services on conflict.

## Settings

Settings can come from a YAML file, environment variables, or both. Environment variables override YAML values.

The YAML file defaults to `/config/config.yaml` and can be changed with `--config`.

| YAML key | Env var | Required | Default | Description |
|----------|---------|----------|---------|-------------|
| `local_config_path` | `LOCAL_CONFIG_PATH` | * | | Path to the inventory YAML template (local file) |
| `config_repo` | `CONFIG_REPO` | * | | GitHub repo (e.g., `user/repo`) |
| `repo_config_path` | `CONFIG_PATH` | * | | Path within the repo to the inventory template |
| `config_branch` | `CONFIG_BRANCH` | | `main` | Git branch |
| `github_token` | `GITHUB_TOKEN` | | | GitHub token (for private repos or rate limiting) |
| `op_service_account_token` | `OP_SERVICE_ACCOUNT_TOKEN` | | | 1Password service account token for resolving MAC addresses |
| `dsm_url` | `DSM_URL` | yes | | Dashboard Services Manager URL |
| `adguardhome_url` | `ADGUARDHOME_URL` | yes | | AdGuardHome URL |
| `adguardhome_username` | `ADGUARDHOME_USERNAME` | yes | | AdGuardHome admin username |
| `adguardhome_password` | `ADGUARDHOME_PASSWORD` | yes | | AdGuardHome admin password |
| `unifi_url` | `UNIFI_URL` | yes | | UniFi controller URL |
| `unifi_username` | `UNIFI_USERNAME` | yes | | UniFi admin username |
| `unifi_password` | `UNIFI_PASSWORD` | yes | | UniFi admin password |
| `unifi_site` | `UNIFI_SITE` | | `default` | UniFi site name |
| `outputs` | `OUTPUTS` | | `adguardhome,unifi` | Comma-separated list of enabled outputs |

*Either `local_config_path` or both `config_repo` + `repo_config_path` must be set.

## 1Password integration

MAC addresses in the inventory use 1Password secret references: `{{ op://Vault/Item/Section/Field }}`.

At load time, the template is written to a temp file and resolved via `op inject`. This requires the `OP_SERVICE_ACCOUNT_TOKEN` setting.

If `op inject` fails (token invalid, item missing, 1Password unreachable):
- MAC fields remain unresolved and are treated as `None`
- DNS rewrites and AdGuardHome clients still sync (they don't use MACs)
- DHCP reservations are skipped (no valid MACs)
- Existing DHCP reservations in UniFi are not removed

`op inject` fails atomically — if any single reference can't resolve, none of them do.

## Running

### Docker (recommended)

```bash
docker run -d \
  --name network-inventory-manager \
  -v ./config.yaml:/config/config.yaml:ro \
  -v ./network_hosts_inventory.yaml.tpl:/config/network_hosts_inventory.yaml.tpl:ro \
  -p 8080:8080 \
  ghcr.io/andyattebery/network-inventory-manager
```

The container syncs every 30 minutes by default (`--interval 1800`).

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | `/config/config.yaml` | Path to config YAML |
| `--interval SECONDS` | `0` | Seconds between syncs. `0` = run once and exit |
| `--verbose` / `-v` | off | Show unchanged entries in addition to changes |
| `--dry-run` | off | Log changes without applying them |

### One-shot mode

```bash
docker run --rm \
  -v ./config.yaml:/config/config.yaml:ro \
  -v ./inventory.yaml.tpl:/config/network_hosts_inventory.yaml.tpl:ro \
  ghcr.io/andyattebery/network-inventory-manager --interval 0
```

## HTTP endpoints

The container exposes port 8080 with two endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sync` | Trigger an immediate sync cycle |
| `GET` | `/health` | Returns `200 OK` (for Docker healthcheck) |

Triggering a sync during an active cycle queues it — the next cycle starts immediately after the current one finishes. No concurrent syncs.

## Dry run

Pass `--dry-run` on the command line. All reads proceed normally, but writes are logged with `[DRY RUN]` prefix instead of executed.

## Error handling

| Failure | Effect |
|---------|--------|
| `op inject` fails | MACs unresolved, DHCP skipped, DNS/clients still sync |
| DSM unreachable | No service-derived rewrites. Stale service rewrites kept in AdGuardHome (removals blocked) |
| UniFi login fails | Client discovery skipped, DHCP skipped. Stale discovered clients kept (removals blocked) |
| UniFi discovery fails (login OK) | Same as login failure for clients, but DHCP still syncs |
| AdGuardHome unreachable | AdGuardHome skipped, UniFi DHCP still runs |
| Individual write fails | That item skipped, remaining items still processed |

Removals are only applied when all inputs (DSM + UniFi discovery) succeed. This prevents temporary outages from deleting entries that should still exist.
