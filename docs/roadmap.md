# Roadmap

Deferred work, mostly surfaced by the 2026-08-06 incident in which an unresolvable
1Password reference caused NIM to delete every DNS rewrite in AdGuardHome. The
hardening that shipped in response is described in
[user-guide.md → Removal safety](user-guide.md#removal-safety); this file is what
was consciously left undone.

Everything remaining is in the deployment repository,
[`andyattebery/homelab-infrastructure`](https://github.com/andyattebery/homelab-infrastructure).

---

## This repo

Nothing outstanding. Items 1–4 of the previous list — `/health` reporting the last
cycle result, recording NIM-owned entries in AdGuardHome, Prometheus `/metrics`,
and pinning the 1Password CLI — all shipped, along with a removal grace window
that replaced the removal-volume bound. See
[user-guide.md → Removal safety](user-guide.md#removal-safety).

One thing deliberately not done: **AdGuardHome duplicate rewrites are not
deduplicated.** `current_set` is a set comprehension over the rewrite list, so
duplicate rows collapse to one tuple and `to_remove` computes zero for them. If an
instance accumulates duplicates they persist until removed by hand.

## Deployment repo (`homelab-infrastructure`)

### 1. Pin the `nim` flake input to a release tag

`nix/flake.nix` has no `ref=`, so `flake.lock` follows `main` HEAD — and the
pinned revision is the `[skip ci]` version-bump commit the release workflow
pushes, *not* the tagged commit, so the deployed tree is one GitHub CI never ran
pytest against. Any unreleased push to `main` ships on the next `nix flake
update`. Enabling `doCheck` in `nix/package.nix` closed the "untested code
deploys" half; pinning to a tag would close the other.

### 2. Keep the Ansible role maintained, but stop it deploying

`ansible/playbook-network.yaml` still includes
`docker_compose_network_inventory_manager` unconditionally for network-01, which
has been NixOS-managed since the NIM Nix package landed. The role points
AdGuardHome at the host's LAN address while the Nix service uses
`http://localhost:3000` — if that play runs, two NIM instances reconcile the same
state. The role is being kept for a future Docker-based host, so comment out the
task with a why-comment rather than deleting it, and keep the role in step with
the NixOS module as settings evolve.

### 3. `add_host.sh` new-host path is a silent no-op

`network-inventory/add_host.sh` inserts a new host before a
`# Hosts on a different domain` marker that was deleted from the inventory
template along with the `other_hosts:` section. The `sed` matches nothing and
inserts nothing, while the script still prints `Added new host`. The 1Password
item *is* created, so it looks like it worked.
