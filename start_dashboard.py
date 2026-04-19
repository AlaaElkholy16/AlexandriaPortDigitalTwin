"""
One-command launcher for the Alexandria Port 2D Dashboard.

Solves the `file://` CORS problem — modern browsers block fetch() from
file:// URLs, so the dashboard can't load alexandria_berths.geojson or
alexandria_live.json until it's served over HTTP.

This script:
    1. Starts a local HTTP server on port 8000 in the cesium-claude dir
    2. Opens the dashboard URL in your default browser
    3. Optionally starts the ShipNext poll loop (refreshes live data every 5 min)
    4. Ctrl-C stops both cleanly

USAGE:
    python start_dashboard.py              # server only
    python start_dashboard.py --poll       # server + live poll loop
    python start_dashboard.py --port 8080  # custom port
"""
import argparse
import http.server
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).parent
DEFAULT_PORT = 8000


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Silence the default per-request logging — too noisy for a dashboard."""
    def log_message(self, fmt, *args):
        pass


def start_server(port: int) -> socketserver.TCPServer:
    handler = lambda *a, **k: QuietHandler(*a, directory=str(BASE), **k)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("localhost", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def start_poll_loop():
    """Run shipnext_ingest.py poll in a subprocess."""
    script = BASE / "shipnext_ingest.py"
    if not script.exists():
        print(f"[warn] {script} not found — skipping poll loop")
        return None
    print("[poll] launching shipnext_ingest.py poll --interval 300")
    return subprocess.Popen(
        [sys.executable, str(script), "poll", "--interval", "300"],
        cwd=str(BASE),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--poll", action="store_true",
                    help="also run shipnext_ingest.py poll every 5 min")
    ap.add_argument("--no-open", action="store_true",
                    help="don't open browser automatically")
    args = ap.parse_args()

    url = f"http://localhost:{args.port}/alexandria-port-2d.html"
    print("=" * 60)
    print(" ALEXANDRIA PORT DT · 2D DASHBOARD")
    print("=" * 60)
    print(f"  Serving {BASE}")
    print(f"  URL: {url}")
    print(f"  Poll loop: {'ENABLED (5 min)' if args.poll else 'disabled (use --poll to enable)'}")
    print("=" * 60)

    # Take a snapshot first so data exists on page load
    live_json = BASE / "alexandria_live.json"
    if not live_json.exists():
        print("[init] alexandria_live.json missing — taking first snapshot")
        subprocess.run([sys.executable, str(BASE / "shipnext_ingest.py"), "snapshot"],
                       cwd=str(BASE))

    srv = start_server(args.port)
    poll_proc = start_poll_loop() if args.poll else None
    time.sleep(0.3)
    if not args.no_open:
        webbrowser.open(url)

    print("\n  Ctrl-C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[stop] shutting down...")
        srv.shutdown()
        srv.server_close()
        if poll_proc and poll_proc.poll() is None:
            poll_proc.terminate()
        print("[stop] done.")


if __name__ == "__main__":
    main()
