import argparse
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from network_inventory_manager._types import Settings
from network_inventory_manager.sync import run_sync

_DEFAULT_CONFIG_PATH = Path("/config/settings.yaml")


class SyncHandler(BaseHTTPRequestHandler):
    sync_event: threading.Event

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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Homelab network configuration sync")
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG_PATH,
        help="Path to settings YAML file (default: %(default)s). Env vars override file values.",
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

    sync_event = threading.Event()
    SyncHandler.sync_event = sync_event
    server = HTTPServer(("0.0.0.0", 8080), SyncHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server listening on port 8080")

    while True:
        logger.info("Starting sync cycle")
        try:
            run_sync(settings)
        except Exception:
            logger.error("Sync cycle failed", exc_info=True)
        logger.info("Sync cycle complete")

        if interval == 0:
            break
        logger.info("Sleeping %ds until next cycle (POST /sync to trigger early)", interval)
        sync_event.wait(timeout=interval)
        sync_event.clear()


main()
