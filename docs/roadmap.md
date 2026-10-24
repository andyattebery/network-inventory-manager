# Roadmap

Deferred work, mostly surfaced by the 2026-08-06 incident in which an unresolvable
1Password reference caused NIM to delete every DNS rewrite in AdGuardHome. The
hardening that shipped in response is described in
[user-guide.md → Removal safety](user-guide.md#removal-safety); this file is what
was consciously left undone.

---

Nothing outstanding. Items 1–4 of the previous list — `/health` reporting the last
cycle result, recording NIM-owned entries in AdGuardHome, Prometheus `/metrics`,
and pinning the 1Password CLI — all shipped, along with a removal grace window
that replaced the removal-volume bound. See
[user-guide.md → Removal safety](user-guide.md#removal-safety).

One thing deliberately not done: **AdGuardHome duplicate rewrites are not
deduplicated.** `current_set` is a set comprehension over the rewrite list, so
duplicate rows collapse to one tuple and `to_remove` computes zero for them. If an
instance accumulates duplicates they persist until removed by hand.
