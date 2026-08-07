import argparse
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from network_inventory_manager._types import Settings, SourceCache, SyncStatus
from network_inventory_manager.sync import run_sync

_DEFAULT_CONFIG_PATH = Path("/config/config.yaml")

# A cycle older than twice the interval means the loop is wedged rather than
# merely between runs. The floor keeps a very short interval from flapping.
_STALENESS_MULTIPLIER = 2
_STALENESS_FLOOR_SECONDS = 300


class SyncHandler(BaseHTTPRequestHandler):
    sync_event: threading.Event
    status: SyncStatus
    stale_after: float | None = None

    def _send_json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path == "/sync":
            self.sync_event.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Sync triggered\n")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            healthy, body = self.status.health(self.stale_after)
            self._send_json(200 if healthy else 503, body)
        elif self.path == "/metrics":
            self._send_metrics()
        else:
            self.send_response(404)
            self.end_headers()

    def _send_metrics(self) -> None:
        s = self.status
        snap = s.snapshot()
        healthy, _ = s.health(self.stale_after)
        # Hand-rendered rather than pulling in prometheus_client for six numbers.
        lines = [
            "# HELP nim_last_sync_success Whether the last sync cycle fully applied.",
            "# TYPE nim_last_sync_success gauge",
            f"nim_last_sync_success {1 if snap['last_cycle_applied'] else 0}",
            "# HELP nim_healthy Whether /health currently reports healthy.",
            "# TYPE nim_healthy gauge",
            f"nim_healthy {1 if healthy else 0}",
            "# HELP nim_last_sync_timestamp Unix time of the last completed cycle.",
            "# TYPE nim_last_sync_timestamp gauge",
            f"nim_last_sync_timestamp {snap['last_cycle_at'] or 0}",
            "# HELP nim_rewrites_added_total Rewrites added since start.",
            "# TYPE nim_rewrites_added_total counter",
            f"nim_rewrites_added_total {s.rewrites_added}",
            "# HELP nim_rewrites_removed_total Rewrites removed since start.",
            "# TYPE nim_rewrites_removed_total counter",
            f"nim_rewrites_removed_total {s.rewrites_removed}",
            "# HELP nim_removals_deferred Entries currently inside the grace window.",
            "# TYPE nim_removals_deferred gauge",
            f"nim_removals_deferred {s.removals_deferred}",
            "# HELP nim_inventory_load_failures_total Cycles that could not load the inventory.",
            "# TYPE nim_inventory_load_failures_total counter",
            f"nim_inventory_load_failures_total {s.inventory_failures}",
        ]
        payload = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Homelab network configuration sync")
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG_PATH,
        help="Path to config YAML file (default: %(default)s). Env vars override file values.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Seconds between sync cycles. 0 = run once and exit.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show unchanged/existing entries in addition to changes.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP server port (default: 8080).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log changes without applying them.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    settings = Settings.load(args.config)

    interval = args.interval if args.interval != 0 else settings.sync_interval
    verbose = args.verbose or settings.verbose

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    port = args.port if args.port != 0 else settings.port

    sync_event = threading.Event()
    status = SyncStatus()
    SyncHandler.sync_event = sync_event
    SyncHandler.status = status
    SyncHandler.stale_after = (
        max(_STALENESS_MULTIPLIER * interval, _STALENESS_FLOOR_SECONDS)
        if interval
        else None
    )
    server = HTTPServer(("0.0.0.0", port), SyncHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server listening on port %d", port)

    # Carried across cycles so an input that goes down has its last known-good
    # contribution protected from removal rather than freezing all removals, and
    # so absence counters survive between cycles.
    cache = SourceCache()

    while True:
        logger.info("Starting sync cycle")
        ok = False
        error: str | None = None
        try:
            ok = run_sync(settings, dry_run=args.dry_run, cache=cache, status=status)
            if not ok:
                error = "cycle did not fully apply; see log"
        except Exception as exc:
            logger.error("Sync cycle failed", exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
        status.record(applied=ok, error=error)
        logger.info("Sync cycle complete (applied=%s)", ok)

        if interval == 0:
            # Without this a one-shot run exits 0 even when the inventory never
            # loaded and nothing was synced, which is indistinguishable from
            # success to any caller.
            raise SystemExit(0 if ok else 1)
        logger.info("Sleeping %ds until next cycle (POST /sync to trigger early)", interval)
        sync_event.wait(timeout=interval)
        sync_event.clear()


if __name__ == "__main__":
    main()
