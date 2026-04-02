"""Web UI entry point — FastAPI backend + pywebview desktop shell.

Usage:
    python web_main.py              # pywebview window (desktop app)
    python web_main.py --dev        # uvicorn only (for frontend dev with Vite)
    python web_main.py --browser    # open in system browser instead of pywebview
"""
import sys
import os
import argparse
import threading
import time
import webbrowser

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, os.path.dirname(__file__))

HOST = "127.0.0.1"
PORT = 8741


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    import traceback
    import logging

    parser = argparse.ArgumentParser(description="MIDI Macropad Web UI")
    parser.add_argument("--dev", action="store_true", help="Dev mode (uvicorn only, CORS enabled)")
    parser.add_argument("--browser", action="store_true", help="Open in browser instead of pywebview")
    parser.add_argument("--port", type=int, default=PORT, help=f"Server port (default: {PORT})")
    args = parser.parse_args()

    # Setup logging so errors go to file
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(log_dir, "web_backend.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("web_main")

    try:
        from backend.core import AppCore
        from backend.app import create_app

        core = AppCore()
        core.bootstrap()
        app = create_app(core)
    except Exception:
        log.error("Bootstrap failed:\n%s", traceback.format_exc())
        sys.exit(1)

    base_url = f"http://{HOST}:{args.port}"

    if args.dev:
        import uvicorn
        log.info(f"[DEV] Backend at {base_url}")
        log.info(f"[DEV] Run 'cd frontend && npm run dev' for React HMR")
        uvicorn.run(app, host=HOST, port=args.port, log_level="info")
    else:
        import uvicorn

        server_config = uvicorn.Config(app, host=HOST, port=args.port, log_level="info")
        server = uvicorn.Server(server_config)

        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        if not _wait_for_server(f"{base_url}/api/midi/status"):
            log.error("Server failed to start within timeout")
            sys.exit(1)

        log.info(f"Server ready at {base_url}")

        if args.browser:
            webbrowser.open(base_url)
            log.info(f"Opened browser at {base_url}")
            try:
                # Keep alive — check server thread health
                while server_thread.is_alive():
                    time.sleep(1)
                log.warning("Server thread stopped unexpectedly")
            except KeyboardInterrupt:
                log.info("Ctrl+C received")
        else:
            try:
                import webview
                window = webview.create_window(
                    "MIDI Macropad",
                    url=base_url,
                    width=1280, height=820,
                    min_size=(900, 600),
                )
                webview.start()
            except ImportError:
                log.warning("pywebview not installed, falling back to browser")
                webbrowser.open(base_url)
                try:
                    while server_thread.is_alive():
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass

        server.should_exit = True
        try:
            core.shutdown()
        except Exception:
            log.error("Shutdown error:\n%s", traceback.format_exc())


if __name__ == "__main__":
    main()
