# Keep the 1Password CLI version in sync between the flake and the Dockerfile

## Status: Done
Started: 2026-10-04 12:36 CDT · Updated: 2026-10-04 13:03 CDT
Pin committed by the user as `bf7ac65` (12:57, local, ahead of origin by 1). Follow-up
`op-cli-pin` check done and verified; the user staged it, not committed. No git writes by the
agent at any point.

## Goal
User (2026-10-04): "the flake and dockerfile need to keep the 1password dependency in sync".

## Decisions
- Classified bounded (brainstorming skill): short in-chat design, no spec file.
- Chosen (user, 12:45 "go with the pin"): one pin file read by both builds. `nix/op-cli.nix`
  overrides nixpkgs' `_1password-cli` from it; the Dockerfile drops apt and installs the same
  release from cache.agilebits.com, checking the same sha256. Why: the overlay builds against
  the consumer's nixpkgs, so only a pin carried inside the package reaches network-01.
- Rejected: keep apt + add a drift check. Unsatisfiable today: apt carries only 2.40.0-1, no
  nixpkgs branch has 2.40.0.
- Rejected: Docker follows this flake's lock + a `checks` drift test. Only syncs this flake's own
  `packages.*`, which network-01 does not use.
- Pin is TOML (`op-cli.toml`), not the `op-cli.json` named in the design: the bump procedure has
  to live in comments on the pin file, and JSON has none. Nix reads it with `builtins.fromTOML`,
  the image with Python 3.12's `tomllib`.
- Hashes are flat sha256 of the zip/pkg (nix `fetchurl` + unzip + `sourceRoot = "."`), not
  nixpkgs' `fetchzip` NAR hash, so one value per platform serves both builds. Do not "simplify"
  back to fetchzip — the Dockerfile's `sha256sum -c` would then need a second set of hashes.
- Platform map covers exactly the 3 pin keys: x86_64-linux, aarch64-linux, aarch64-darwin.
  x86_64-darwin left out: no consumer, and this flake's nixpkgs (unstable) already errors
  "Nixpkgs 26.11 has dropped support for x86_64-darwin" before reaching op.
- Docker side: kept `curl` installed (status quo for the runtime image), dropped `gnupg` (its only
  use was the apt key). Arch from `dpkg --print-architecture` as before, not `TARGETARCH`, so it
  doesn't depend on BuildKit.
- Devlog: added a new 2026-10-04 entry and a "Superseded" pointer on the 0.2.0 bullet, rather
  than rewriting the 0.2.0 text (design said "rewrite"; a devlog is history).
- Follow-up check (approved 12:58): `checks.<linux>.op-cli-pin` = linkFarm of every pinned file.
  `nix/op-cli.nix` builds them as `sources` (one fetchurl per pin key, exposed in passthru) and
  takes its own `src` from that set, so the URL scheme exists once in nix (plus the Dockerfile's
  copy). Extension now keyed on the pin key (`apple_universal` → pkg), not on the host.
- Provenance of the pinned 2.40.0 hashes checked once: both linux zips' `op.sig` verify against
  1Password key 3FEF9748469ADBE15DA7CA80AC2D62742012EA22 (from 1password.asc, in a throwaway
  debian container); the .pkg is AgileBits Developer ID–signed and notarized (`pkgutil`).

## Progress
- 2026-10-04 12:36 CDT — Versions in play right now:
  - Dockerfile: `2.38.1-1` committed, `2.40.0-1` staged (user's bump, not mine).
  - This flake's lock (nixpkgs `b7c2ada9`, unstable ~2026-08): `_1password-cli` 2.34.1.
  - nixos-unstable head: 2.39.0. nixos-26.05 head: 2.34.0.
  - network-01: homelab-infrastructure `nix/flake.nix` has `nim.inputs.nixpkgs.follows =
    "nixpkgs"` (nixos-26.05, locked `825e2028`) and `hosts/network-01/default.nix:114` uses
    `pkgs.network-inventory-manager` from the overlay (`final.callPackage`) → runs op 2.34.0.
- apt repo `downloads.1password.com/linux/debian/amd64` Packages: only `2.40.0-1` (confirms the
  Dockerfile comment). `cache.agilebits.com/dist/1P/op2/pkg/v<ver>/op_linux_{amd64,arm64}_v<ver>.zip`
  returns 200 for 2.34.1, 2.38.1, 2.40.0 — the archive keeps old versions.
- No local nix; verification must go through the `nixos/nix` Docker image.
- 2026-10-04 12:45 CDT — Approved. nixpkgs' `_1password-cli` build logic is identical at
  `b7c2ada9`, `825e2028` and unstable head (only version/hashes differ; 26.05 also maps
  x86_64-darwin), so one override works for this flake and for homelab's 26.05.
- ~2026-10-04 12:47 CDT — RED (eval in nixos/nix container): flake packages on all 3 systems and
  the darwin devshell wrap 1password-cli-2.34.1; overlay on homelab 26.05 wraps 2.34.0.
- ~2026-10-04 12:50 CDT — Wrote `op-cli.toml`, `nix/op-cli.nix`; edited `nix/package.nix`,
  `flake.nix` (devshell), `Dockerfile`, `.dockerignore` comment. GREEN eval: all outputs 2.40.0,
  including the overlay on 26.05. Docker build linux/arm64 and linux/amd64: `sha256sum` OK,
  `op --version` = 2.40.0, curl present, gpg absent. aarch64-linux nix build: versionCheckHook
  found 2.40.0, 180 tests passed, wrapper op = 2.40.0; `nix flake check` passed.
- ~2026-10-04 12:52 CDT — Darwin hash verified via nix `fetchurl` on linux; darwin op derivation
  evaluates with the .pkg URL, nixpkgs' xar/cpio unpack, name 1password-cli-2.40.0. Tamper test
  (arm64 hash first digit 0→1): Docker fails at `sha256sum` (FAILED, exit 1); nix fails with
  "hash mismatch in fixed-output derivation ... op_linux_arm64_v2.40.0.zip". x86_64-linux
  (emulated): flake package and overlay-on-26.05 (network-01's exact build) both versionCheck
  2.40.0, 180 tests passed, wrapper op 2.40.0.
- ~2026-10-04 12:53 CDT — `nix flake check` on x86_64-linux (CI's system) passed; x86_64-linux
  and aarch64-linux devshells carry 1password-cli-2.40.0.
- 2026-10-04 12:55 CDT — Review (slow read) fixed wording only: pin readers named as
  `nix/op-cli.nix` not `nix/package.nix` (op-cli.toml:1, Dockerfile:3); devlog universals
  ("never", "every version") narrowed to what was checked. Re-eval after the comment edits gives
  the same aarch64-linux outPath as the build (`i1pvjl98…`). Removed test images and the three
  nix-store volumes; left `nixos/nix:2.35.2` and `python:3.12-slim`, which predate this task.

- 2026-10-04 12:58 CDT — Follow-up RED: with linux_amd64 and apple_universal hashes both
  corrupted, `nix flake check` on aarch64-linux exited 0 (it builds checks, not packages, and
  no check touched the pin).
- ~2026-10-04 13:00 CDT — Added `sources`/passthru to `nix/op-cli.nix`, `op-cli-pin` to flake
  checks, comments in ci.yaml:45-46 and op-cli.toml:12-13. op outPaths unchanged by the
  refactor (aarch64-linux `6727a0ja`, x86_64-linux `g1v1lwxh`, overlay-26.05 `n8gijabp`). NIM
  outPath did change (`i1pvjl98` → `z5qz0d0y`) because its src filter includes flake.nix (and
  flake.lock, README.md, .dockerignore, .gitignore) — pre-existing, left alone. GREEN:
  `nix flake check` (aarch64-linux) passes, fetching all 3 files; corrupting only linux_amd64,
  then only apple_universal, each fails with "hash mismatch" naming that file.
- 2026-10-04 13:01 CDT — CI nix job replayed on x86_64-linux (emulated): `nix flake check`
  passes and runs `checks.x86_64-linux.op-cli-pin`; `nix build .#packages.x86_64-linux.default`
  passes, 180 tests, wrapper op still `g1v1lwxh…-1password-cli-2.40.0`.
- 2026-10-04 13:03 CDT — Removed this round's nix-store volumes and `nixos/nix:latest`. Found
  `bf7ac65` (user's commit of round one, 12:57:10) and round two fully staged by the user.

## Open / blocked
- Not verified: an actual darwin build (no darwin builder) — only its hash and evaluation.
- Not verified: versionCheckHook *failing* on a version mismatch (only the passing case seen);
  that is nixpkgs' hook behaviour, not this change.
- `tasks/` is still not gitignored in this repo (shows as `??`).
- Round two is staged, not committed: .github/workflows/ci.yaml, flake.nix, nix/op-cli.nix,
  op-cli.toml, this file (this last edit is unstaged on top).
