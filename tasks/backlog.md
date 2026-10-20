# Backlog

One line per item: date · run · item · source. A line leaves when its item is done or dropped.

## Waits on the user

- 2026-10-20 · uv-uv2nix · Before this change merges: seed `release` at the commit homelab deploys (`git push origin ea985a8:refs/heads/release`), then switch homelab's `nim` input to `github:andyattebery/network-inventory-manager/release` — the package build no longer runs the tests, so until homelab follows `release` nothing gates its deploys · `tasks/2026-10-20-uv-uv2nix.md`, `docs/roadmap.md` item 1
- 2026-10-20 · uv-uv2nix (review I1) · After seeding, add a ruleset on `release` ("Block force pushes", "Restrict deletions") — today nothing on GitHub stops a manual move or force-push of the branch homelab deploys from; CI's own push only fast-forwards, so the ruleset doesn't get in its way · `plans/2026-10-20-uv-uv2nix.md` "Enforced vs. exhortation"
- 2026-10-20 · uv-uv2nix · Not adopted from Astral's `uv-docker-example` (not uv-specific, changes runtime behaviour): a non-root user (the op CLI needs a writable `HOME`) and `PYTHONUNBUFFERED=1` · `plans/2026-10-20-uv-uv2nix.md` non-goals
- 2026-10-20 · uv-uv2nix · The Docker image is built in CI only on tags, so a Dockerfile change (including this run's) is first built by CI at release time · `.github/workflows/ci.yaml` (`docker` job `if:`)
- 2026-10-20 · uv-uv2nix (needletrop comparison, item 6, not taken) · Pin the Debian release in the image tags (`python:3.12-slim-trixie`, as Astral's uv-docker-example does) so a Debian major bump is deliberate · `Dockerfile`
- 2026-10-20 · uv-uv2nix (needletrop comparison, item 6, not taken) · A NixOS VM test (`testers.runNixOSTest`) that starts the service; today `checks.module-imports-twice` only proves the module evaluates · `flake.nix` checks
- 2026-10-20 · uv-uv2nix (review M6, deferred) · `uv_build>=0.13.4,<0.14` is unmet where nix builds (build-system-pkgs uv-build 0.13.3) and by the local uv 0.13.1; it builds only because nix doesn't enforce the bound. uv2nix's template bound `>=0.11.0,<0.14.0` holds on every path — decide which to keep · `pyproject.toml`
- 2026-10-20 · uv-uv2nix (review M7 follow-up, deferred) · An in-build launch check (installCheckPhase running `$out/bin/network-inventory-manager --help`), as the nixpkgs manual recommends (`passthru.chapter.md`, versionCheckHook) — it would run on network-01's own nixpkgs, which CI never tests · `nix/package.nix`
- 2026-10-20 · uv-uv2nix (review M3, deferred) · The devshell's editable package inherits the full package fileset, so any `.py` edit rebuilds it on the next `nix develop`; uv2nix's source-filtering doc suggests `pyproject.toml` + `__init__.py` for editables · `flake.nix` devShells
- 2026-10-20 · uv-uv2nix (review M4, deferred) · `docs/roadmap.md` says "This repo: Nothing outstanding" while this backlog holds items for this repo · `docs/roadmap.md`

## Waits on an event

- 2026-10-20 · uv-uv2nix (review M1) · Release 0.2.3: confirm the `release-branch` job's push lands (`git ls-remote origin refs/heads/release` = the tag's commit). GitHub can refuse GITHUB_TOKEN pushes that change workflow files (reports differ on whether it compares against the default branch or the pushed range); it fails safe — nothing moves. Fallback: push it by hand (`git push origin <tag>:refs/heads/release`) or give the job a token with `workflows` permission · `.github/workflows/ci.yaml` `release-branch`
- 2026-10-20 · uv-uv2nix · uv 0.14 release: bump together the `uv_build` upper bound (`pyproject.toml`), the uv pins (`Dockerfile` `ghcr.io/astral-sh/uv:0.13.4`, `ci.yaml` setup-uv `version` in the test and docker jobs, the test image in `docs/development.md`), and `nix flake update pyproject-build-systems` · `plans/2026-10-20-uv-uv2nix.md` backlog
