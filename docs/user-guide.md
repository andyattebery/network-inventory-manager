# User guide

## How it works

Each sync cycle:

1. **Load inventory** — fetch the host YAML template (from a local file or GitHub), resolve 1Password `{{ op://... }}` references for MAC addresses via `op inject`
2. **Fetch DSM services** — query Dashboard Services Manager for running services and their hostnames
3. **Discover UniFi clients** — query UniFi for active and configured clients (phones, IoT devices, etc.)
4. **Build desired state** — combine all sources into lists of DNS rewrites, AdGuardHome clients, and DHCP reservations
5. **Sync to AdGuardHome** — diff desired rewrites and clients against current state, add/remove/update
6. **Sync to UniFi** — diff desired DHCP reservations against current static leases by MAC, create/update

The inventory is the desired state, so if it cannot be loaded, resolved, or validated the cycle is skipped entirely — nothing is read from or written to AdGuardHome or UniFi. If a *contributing* source (DSM, UniFi discovery) is unavailable, the sync proceeds and that source's last known-good entries are protected from removal. See [Removal safety](#removal-safety).

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

Hosts whose names sit outside `homelab_domain` — a Tailscale `*.ts.net` machine, or anything else already fully qualified. `hostname` is used verbatim as the rewrite domain rather than having `homelab_domain` appended, which is exactly why these cannot be expressed as `homelab_hosts`. Each entry gets a DNS rewrite and an AdGuardHome client. No DHCP.

The section is optional and may be absent.

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
| `removal_grace_cycles` | `REMOVAL_GRACE_CYCLES` | | `8` | Consecutive cycles an entry must be absent from the desired state before it is deleted. `0` deletes immediately |

*Either `local_config_path` or both `config_repo` + `repo_config_path` must be set.

## 1Password integration

MAC addresses in the inventory use 1Password secret references: `{{ op://Vault/Item/Section/Field }}`.

At load time, the template is written to a temp file and resolved via `op inject`. This requires the `OP_SERVICE_ACCOUNT_TOKEN` setting.

`op inject` fails atomically — if any single reference can't resolve, none of them do. **Any of the following aborts the whole sync cycle**, with no reads or writes to AdGuardHome or UniFi:

- `op inject` exits non-zero (token invalid, item missing, vault not visible, 1Password unreachable)
- the `op` binary is not on `PATH`
- `op inject` exceeds its 60-second timeout
- `op inject` succeeds but its output still contains `{{ op://... }}`
- any resolved value fails validation (see below)

The next cycle retries. Nothing is deleted in the meantime.

> **Service accounts cannot read Personal, Private, or Employee vaults**, nor the default Shared vault. A reference into one of those fails with `"<vault>" isn't a vault in this account` even though the path is correct for a human signin. Keep everything the inventory references in a shared vault the service account is granted.

### Validation

Every resolved value is checked before it can reach an output. Failures are collected and reported together, so one pass fixes them all:

| Field | Rule | On failure |
|-------|------|------------|
| `homelab_domain` | valid DNS name | **fatal** |
| `homelab_hosts` keys | valid DNS name | **fatal** |
| `homelab_hosts[].ip` | valid IPv4/IPv6 | **fatal** |
| `homelab_hosts[].mac` | 12 hex digits, optional `:` `-` `.` | warning; MAC dropped, host keeps DNS |
| `other_hosts[].hostname` / `.ip` | valid DNS name / IP | **fatal** |
| `services` keys and `.hostname` | valid DNS name | **fatal** |

A bad IP is fatal because AdGuardHome diffs rewrites on the `(domain, answer)` pair — a wrong answer invalidates the entire current set exactly as a wrong domain does. A bad MAC is not, because the UniFi output only creates and updates reservations and never deletes: the cost is one missing reservation.

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

One-shot mode (`--interval 0`) exits `1` if the cycle did not fully apply, `0` otherwise.

### One-shot mode

```bash
docker run --rm \
  -v ./config.yaml:/config/config.yaml:ro \
  -v ./inventory.yaml.tpl:/config/network_hosts_inventory.yaml.tpl:ro \
  ghcr.io/andyattebery/network-inventory-manager --interval 0
```

## HTTP endpoints

The container exposes port 8080:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sync` | Trigger an immediate sync cycle |
| `GET` | `/health` | `200` when the last cycle applied, `503` otherwise. JSON body |
| `GET` | `/metrics` | Prometheus text exposition |

### `/health`

Returns **503** — not 200 — when any of these hold:

- no cycle has completed yet
- the last cycle did not fully apply (the body carries `last_error`)
- the last cycle finished longer ago than twice the sync interval

That last case is the one worth having. Reporting only the last *outcome* leaves a
**hung** cycle looking healthy indefinitely, and a hang is the failure mode nothing
else here detects. Every outbound HTTP call sets a timeout for the same reason.

Body:

```json
{
  "last_cycle_at": 1754500000.0,
  "last_success": 1754500000.0,
  "last_cycle_applied": true,
  "last_error": null
}
```

Detection still lags by up to one interval — that is inherent to polling, and
`/health` is detection, not prevention. The fail-closed inventory handling is what
prevents damage.

### `/metrics`

`nim_healthy`, `nim_last_sync_success`, `nim_last_sync_timestamp`,
`nim_rewrites_added_total`, `nim_rewrites_removed_total`, `nim_removals_deferred`
(entries currently inside the grace window), `nim_inventory_load_failures_total`.

Triggering a sync during an active cycle queues it — the next cycle starts immediately after the current one finishes. No concurrent syncs.

## Dry run

Pass `--dry-run` on the command line. All reads proceed normally, but writes are logged with `[DRY RUN]` prefix instead of executed.

## Error handling

| Failure | Effect |
|---------|--------|
| Inventory unreachable, unparseable, or invalid | **Entire cycle skipped.** No reads, no writes, no removals |
| `op inject` fails / `op` missing / times out / leaves refs unresolved | Same — the inventory failed to load |
| DSM unreachable | No service-derived rewrites. DSM's last known-good rewrites are protected from removal; everything else still reconciles |
| DSM unreachable and never cached | Removals blocked entirely for this cycle |
| UniFi login or discovery fails | Client discovery skipped, DHCP skipped. Discovered clients' last known-good names are protected from removal |
| UniFi discovery fails (login OK) | Same as login failure for clients, but DHCP still syncs |
| A rewrite NIM does not own is absent from desired | Left alone, indefinitely |
| An owned entry is absent from desired | Removed only after `removal_grace_cycles` consecutive absences |
| AdGuardHome unreachable | AdGuardHome skipped, UniFi DHCP still runs |
| Individual write fails | That item skipped, remaining items still processed |

## Removal safety

Four independent mechanisms keep a bad input from emptying AdGuardHome. None of them refuses a sync outright — an earlier version did, and a legitimately large cleanup then wedged the service with no way for it to recover.

**1. The inventory is all-or-nothing.** It *is* the desired state, so a failure to load it skips the cycle rather than proceeding with an empty desired state — which would make every existing entry look stale.

**2. Per-source protection.** DSM and UniFi discovery only *contribute* entries. When one is unreachable, its last known-good contribution is protected from removal for that cycle while every other source reconciles normally. This is held in memory, so the first cycle after a restart has nothing cached; in that case removals are blocked globally until the source succeeds once.

**3. Ownership.** NIM only removes rewrites it recorded creating. Anything you add by hand is never a removal candidate, however long it sits there.

The record is a single comment line at the top of AdGuardHome's **custom filtering rules**:

```
! nim-owned {"v":1,"domains":["docker-01.example.com", ...]}
```

It lives there rather than on NIM's disk so it survives restarts, reaches the replicas through `adguardhome-sync`, and leaves NIM with no state of its own. `!` is a filter-syntax comment, so the rules engine ignores it. Every other line in that box is preserved in its original order — add your blocklist overrides below it as usual.

On the very first cycle there is no record, so NIM claims the entries that are both present and desired. That can *under*-claim — an entry NIM created before the inventory changed looks hand-made — but it can never over-claim, so the worst case is a stale entry surviving, not one of yours being deleted.

**4. A grace window.** An owned entry that disappears from the desired state is not deleted until it has been absent for `removal_grace_cycles` consecutive cycles (default `8` — four hours at the default interval). Reappearing resets the counter. This is what stops a service you stopped for debugging from losing its DNS.

The counters are in memory, so a restart resets them and *delays* removals rather than performing them. They advance only when a removal would actually be applied — never during a dry run, and never while removals are blocked by mechanism 2.

An unusually large removal is logged as an ERROR and then applied. The information is worth having; refusing was not.
