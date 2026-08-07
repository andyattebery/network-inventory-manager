# Devlog

Why changes were made, for changes whose reasoning does not survive in the diff.
Newest first. Not a changelog — a release that only adds a feature does not need
an entry here.

---

## 2026-08-07 — fail closed on inventory errors, and gate removals (`ab3777a`, 0.2.0)

### What happened

At 09:26 on 2026-08-06 a 1Password item was moved between vaults, so the
`op://` reference in the inventory template no longer resolved. `op inject`
failed. NIM did not.

`_resolve_template` returned the template's own text on failure, so
`homelab_domain` became the literal string `{{ op://... }}` and every hostname
built from it became `<host>.{{ op://... }}`. The desired state was still 64
well-formed entries — they just described a domain that does not exist. The
reconciler compared them against the 117 real rewrites in AdGuardHome, found no
overlap, and did what it was told: `added 64, removed 117`. Every DNS rewrite in
the homelab was deleted, and `adguardhome-sync` replicated the deletion to the
two secondaries.

It recovered on its own at 10:42 when the reference was fixed. It then happened
a second time at 16:13, when the service-account token was revoked during
credential rotation — same fail-open path, same wipe, same numbers.

The bug is not that `op inject` can fail. It is that a failure to *read* the
desired state was indistinguishable from a desired state that is *empty*.

### The fix, in four parts

**Fail closed.** `network_hosts_inventory.load()` raises `InventoryError` on a
non-zero `op` exit, a missing binary, a timeout, or unresolved `op://` references
surviving in the output. `run_sync` catches it, logs, and skips the entire cycle.
There is no longer a code path that syncs a partially-resolved inventory. Host
field validation collects every error and raises once, so a bad inventory reports
all of its problems rather than the first.

`op inject`'s stdout is never logged. It contains resolved secrets.

**Ownership.** Removals now apply only to entries NIM recorded creating. The
record lives in AdGuardHome, not on NIM's disk — the service is stateless by
design and gaining a state file would have made a restart-with-empty-state its
own version of this bug. AdGuardHome's `RewriteEntry` has no metadata field, but
`/filtering/status` → `user_rules` is a list of strings stored verbatim, and both
`!` and `#` are comment characters, so one comment line at index 0 carries the
set:

```
! nim-owned {"v":1,"domains":["a.<domain_name>", ...]}
```

Domains only — recording the IP too would orphan the record the first time an
address changed. `owned = (previous ∪ desired) − removed_this_cycle`, so an entry
that legitimately leaves the desired state stays removable next cycle. On first
run with no record it claims `current ∩ desired`, which can under-claim but never
over-claim: the failure mode is a manual entry NIM declines to touch, not a
manual entry it deletes. `adguardhome-sync` replicates user rules, so the record
propagates with the data it describes.

**Grace window.** An entry must be absent from the desired state for
`removal_grace_cycles` consecutive cycles before it is removed — eight by
default, four hours at the 1800s interval. The counters live in memory, which is
deliberate: a restart resets them, and resetting them *delays* removals rather
than performing them. This is the gate that covers a source that is reachable but
wrong, which the protection mechanism below cannot see.

**Per-source protection.** When an input fails, its last known-good contribution
is protected from removal instead of the whole cycle aborting. A DSM outage
therefore cannot delete DSM-discovered names, and does not block the host
inventory from reconciling. This does not apply to the host inventory itself —
that one *is* the desired state, so its failure aborts the cycle.

### What was removed, and why

`max_removal_fraction` and `--allow-bulk-removal` are gone. The bound refused the
whole resource when a cycle wanted to remove more than a fraction of it, which
sounds like the right response to this incident and is not: the removal it would
have blocked was 117 of 117, and the removal it *did* block during the manual
repair was the legitimate one that would have fixed it. A guard that fires
identically on the catastrophe and the cleanup, and that can only be cleared by
changing the unit's arguments, is a guard that turns an outage into a longer
outage.

Large removals now log an ERROR and proceed. The three gates above stop the wipe
at its cause instead of at its volume.

### Everything else in the release

- `/health` returns 503 before the first cycle, after a failed cycle, and when
  the last cycle is older than `max(2 × interval, 300s)`. The staleness term is
  the one that matters: reporting only the last outcome leaves a *hung* cycle
  looking healthy forever.
- `/metrics` renders Prometheus text by hand — counters for adds, removals,
  deferred removals, and inventory load failures. No new dependency.
- Every HTTP call has a timeout, set on the session rather than at each call
  site so a new request cannot forget one. `requests` waits forever by default,
  there is one sync thread, and an untimed call therefore wedges every later
  cycle while `/health` keeps serving the last good result.
- The 1Password CLI is pinned in the `Dockerfile`. Their apt repo carries only
  the current release, so a new upstream version breaks the build until the ARG
  is bumped. That is the intended behaviour, not an oversight — an unpinned CLI
  is what makes an image unreproducible in the first place.
- `nix/package.nix` sets `nativeCheckInputs` and runs pytest during the build.
  Consumers pin this flake to a branch rather than a tag, so the commit that
  ships is not necessarily one GitHub CI tested; the build-time check is the only
  one that always sees the deployed source. Every test must stay hermetic — no
  real `op`, no network.

### Known and deliberate

Duplicate rewrites in AdGuardHome are not cleaned up. `current_set` is a set
comprehension, so duplicate rows collapse to one tuple and `to_remove` computes
zero for them. See [roadmap.md](roadmap.md).
