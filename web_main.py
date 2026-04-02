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
    parser = argparse.ArgumentParser(description="MIDI Macropad Web UI")
    parser.add_argument("--dev", action="store_true", help="Dev mode (uvicorn only, CORS enabled)")
    parser.add_argument("--browser", action="store_true", help="Open in browser instead of pywebview")
    parser.add_argument("--port", type=int, default=PORT, help=f"Server port (default: {PORT})")
    args = parser.parse_args()

    from backend.core import AppCore
    from backend.app import create_app

    core = AppCore()
    core.bootstrap()
    app = create_app(core)

    base_url = f"http://{HOST}:{args.port}"

    if args.dev:
        # Dev mode: just run uvicorn with reload-friendly config
        import uvicorn
        print(f"[DEV] Backend at {base_url}")
        print(f"[DEV] Run 'cd frontend && npm run dev' for React HMR")
        uvicorn.run(app, host=HOST, port=args.port, log_level="info")
    else:
        # Production: start uvicorn in background thread
        import uvicorn

        server_config = uvicorn.Config(app, host=HOST, port=args.port, log_level="warning")
        server = uvicorn.Server(server_config)

        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        if not _wait_for_server(f"{base_url}/api/midi/status"):
            print("ERROR: Server failed to start", file=sys.stderr)
            sys.exit(1)

        if args.browser:
            webbrowser.open(base_url)
            print(f"Opened {base_url} in browser. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
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
                print("pywebview not installed. Install: pip install pywebview")
                print(f"Falling back to browser: {base_url}")
                webbrowser.open(base_url)
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass

        server.should_exit = True
        core.shutdown()


if __name__ == "__main__":
    main()
