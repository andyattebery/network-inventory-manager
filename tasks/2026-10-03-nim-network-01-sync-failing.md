# NIM on network-01: sync failing on an unresolvable 1Password reference

## Status: In progress
Started: 2026-10-03 16:04 CDT · Updated: 2026-10-03 16:21 CDT
Inventory error fixed upstream by the user (homelab-infrastructure `8f67285`); first good sync
16:08:12. Two follow-on problems found in that sync (client add 400, DHCP flip-flop). Design for
"a better way to handle this kind of error" is waiting on the user's choice of direction.

## Goal
1. Find why `network-inventory-manager.service` on network-01 is failing.
2. (User, 16:0x) "this needs a better way to handle this kind of error" — design, not yet built.

## Decisions
- Diagnosed from the journal, the deployed config, and `/var/lib/AdGuardHome/AdGuardHome.yaml`
  (sudo awk over client `name`/`ids` only). Did not read `/run/secrets/rendered/nim-env` values
  (only its key names) and did not call the AdGuardHome/UniFi APIs, so no credential was used.
- Did not trigger `POST /sync` — the user was driving syncs by hand during the incident.
- No code changed yet: brainstorming gate — a design must be approved first.
- 16:21 — Alert design: one rule, `nim_healthy == 0 or absent(nim_healthy{job=...})`, `for: 45m`,
  severity warning. Why: `nim_healthy` is the same `health()` as /health (`__main__.py:55,63`),
  so it already covers failed cycles AND a hung loop (staleness); `absent` covers NIM down,
  which nothing fleet-wide alerts on (no `up == 0` rule exists); idiom copied from
  `rules-ipmi.yaml:66`. 45m not 30m because a single transient failure holds 0 for ~27-30 min
  (backtest). Warning not critical: NIM fails closed, so failing = changes stop, not an outage.
  New file `rules-nim.yaml` rather than appending to `rules-dns.yaml`: one file per subsystem is
  the existing convention, and the playbook syncs the whole dir (`playbook-docker-01.yaml:113`).

## Progress
- 2026-10-03 16:04 CDT — Unit is `active (running)` since 2026-09-24; every 30-min cycle logs
  `op inject failed (rc=1): "Personal" isn't a vault in this account` and skips (fail-closed path
  from 0.2.0, `sync.py:132-140`). Nothing applied, nothing removed during the outage.
- 2026-10-03 16:04 CDT — Template is fetched at runtime from
  `andyattebery/homelab-infrastructure@main:network-inventory/network_hosts_inventory.yaml.tpl`
  (config `config_repo`/`repo_config_path`), not from this repo.
- 2026-10-03 16:07 CDT — Root cause: homelab-infrastructure `7818eb8` (2026-10-02 00:05:32 CDT,
  "updated turingpi cluster") added `mac: {{ op://Personal/turingpi/hardware/mac address }}`,
  the only `op://Personal` ref among 37. Service accounts can't see Personal vaults. Last good
  sync 2026-10-02 00:06:26 (raw.githubusercontent.com `max-age=300` still served the old file);
  first failure 00:36:27. ~40 h of skipped cycles (80 vault errors 10-02/10-03).
- Same mistake as 2026-08-06: `fd100c4` added `op://Personal/Home Lab/domains/internal`,
  `78ef9ba` fixed it. That incident is the one in docs/devlog.md (0.2.0).
- 2026-10-03 16:08 CDT — User pushed `8f67285` "network: fixed 1p reference" (commit time
  16:05:56). Sync at 16:06:34 still failed with the Personal error — most likely raw's 5-min
  cache (push time not confirmed; events API didn't list the push yet). Sync at 16:08:09 loaded
  the inventory (48 hosts, 19 services) and finished `applied=True`.
- 2026-10-03 16:11 CDT — 16:08 sync had two `400` on `/control/clients/add`:
  - `turingpi-jetson-01`: AdGuardHome client `turingpi-cm4-02` already holds ID 192.168.1.219.
    Clients are matched by name (`adguardhome.py:295-296`), so a rename is remove+add; the
    remove is deferred by the 8-cycle grace window and AGH rejects a duplicate ID. Removals run
    before adds (`:322` vs `:342`); `seen >= grace_cycles` (`:69`) → old client removed and new
    one added on the 8th absent cycle, ~19:38 CDT if no restart. Manual fix: delete
    `turingpi-cm4-02` in AGH, then `POST /sync`.
  - `turing-pi-outlet` (UniFi-discovered, not in template): pre-existing, intermittent —
    10 failures 2026-09-27 15:50 → 10-03 16:08. Cause not found: NIM doesn't log AGH's response
    body, and the unifi-network MCP failed to connect this session.
- 2026-10-03 16:13 CDT — DHCP: 16:08 sync logged `Updated DHCP reservation: jetson-01,
  3c:6d:66:1e:a8:6d, 192.168.1.193 | name: turingpi-jetson-01 → jetson-01, ip: .219 → .193`.
  Template has both `jetson-01` (line 23, .193) and `turingpi-jetson-01` (line 119, .219); both
  1Password items evidently hold that MAC ("created 0" for the second). `outputs/unifi.py:18-40`
  snapshots reservations once, so the second entry compares against the stale snapshot →
  predicted to flip .193 ↔ .219 every cycle. NOT yet observed — check the ~16:38 cycle.
- 2026-10-03 16:15 CDT — Why 40 h went unnoticed: Prometheus on docker-01 scrapes NIM
  (`ansible/files/prometheus/prometheus.yml:84-87`, job `network-inventory-manager`), but no
  Grafana rule in `ansible/files/docker-01/grafana/provisioning/alerting/` references `nim_*`.
  `/health` was 503 throughout, but its reason is the generic
  `"cycle did not fully apply; see log"` (`__main__.py:163-164`), never the InventoryError text.
  `/health` now 200, `last_cycle_applied: true`.
- 2026-10-03 16:21 CDT — User chose "Alert on it" only (not per-ref degradation, not pinpointed
  errors, not fetch-by-SHA). Scope is now a Grafana rule in homelab-infrastructure, no NIM code.
  Backtest via https://prometheus.<domain>/api/v1/query_range, 2026-09-18 06:15Z → 21:20Z today
  (15d retention, step 150s):
  - `nim_healthy == 0`: 2026-10-01 11:05Z→11:32Z (27 min; raw.githubusercontent.com read
    timeout at 06:02 CDT, recovered 06:32) and 2026-10-02 05:37Z→2026-10-03 21:07Z (2370 min,
    this incident).
  - `absent(nim_healthy)`: no runs. `up{job="network-inventory-manager"} == 0`: no runs.
  - So `for: 45m` (two consecutive failed cycles) → fires only for the incident, at ~01:22 CDT
    10-02. Design posted in chat; waiting for yes.

## Open / blocked
- Waiting on user: yes/no on the alert design (posted in chat 16:21). Then, separately: commit/
  push to homelab-infrastructure and the deploy (`--tags monitoring`, restarts Grafana) each
  need the user's own words.
- Verify the DHCP flip-flop prediction at the ~16:38 cycle; the user decides whether `jetson-01`
  should leave the template.
- `tasks/` and `plans/` are NOT gitignored in this repo (global CLAUDE.md assumes they are).
  Flagged to the user; not changed.
