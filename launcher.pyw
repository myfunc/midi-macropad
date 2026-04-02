"""MIDI Macropad Launcher — GUI process manager.

Double-click to launch. Manages backend + frontend, prevents duplicates.
Uses tkinter (built-in, no dependencies) for the launcher UI.
The actual app UI runs in the browser via FastAPI + React.
"""

import sys
import os
import subprocess
import threading
import time
import signal
import webbrowser
import socket
import ctypes
import json
from pathlib import Path

# --- Paths ---
ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
WEB_MAIN = ROOT / "web_main.py"
FRONTEND_DIR = ROOT / "frontend"
PID_FILE = ROOT / ".launcher.pid"
LOCK_FILE = ROOT / ".launcher.lock"

HOST = "127.0.0.1"
PORT = 8741
URL = f"http://{HOST}:{PORT}"

os.environ["PYTHONUTF8"] = "1"


# --- Duplicate prevention ---

def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) == 0


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_process_alive(pid: int) -> bool:
    try:
        # Signal 0 checks if process exists without killing it
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_process(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if _is_process_alive(pid):
            os.kill(pid, 9)
    except (OSError, ProcessLookupError):
        pass


def _check_duplicate() -> bool:
    """Returns True if another instance is already running and user chose to use it."""
    old_pid = _read_pid()
    if old_pid and _is_process_alive(old_pid) and _is_port_in_use(PORT):
        return True
    # Stale PID — clean up
    if old_pid and not _is_process_alive(old_pid):
        PID_FILE.unlink(missing_ok=True)
    return False


def _write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _cleanup_pid() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# --- Frontend build check ---

def _needs_npm_install() -> bool:
    return not (FRONTEND_DIR / "node_modules").is_dir()


def _needs_build() -> bool:
    dist = FRONTEND_DIR / "dist" / "index.html"
    if not dist.exists():
        return True
    # Rebuild if any source file is newer than dist
    dist_mtime = dist.stat().st_mtime
    for f in (FRONTEND_DIR / "src").rglob("*"):
        if f.stat().st_mtime > dist_mtime:
            return True
    return False


# --- tkinter UI ---

def run_launcher():
    import tkinter as tk
    from tkinter import ttk

    # Check duplicate before even showing UI
    if _check_duplicate():
        # Already running — just open browser and exit
        webbrowser.open(URL)
        return

    _write_pid()

    root = tk.Tk()
    root.title("MIDI Macropad Launcher")
    root.geometry("480x380")
    root.resizable(False, False)
    root.configure(bg="#1E1E2E")

    # Try to set icon
    ico = ROOT / "app_icon.ico"
    if ico.exists():
        try:
            root.iconbitmap(str(ico))
        except Exception:
            pass

    # Style
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TFrame", background="#1E1E2E")
    style.configure("TLabel", background="#1E1E2E", foreground="#E8E8F0", font=("Segoe UI", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), foreground="#6EB4FF")
    style.configure("Status.TLabel", font=("Segoe UI", 9), foreground="#A0A0B8")
    style.configure("Green.TLabel", foreground="#5AE68C")
    style.configure("Yellow.TLabel", foreground="#FFC85A")
    style.configure("Red.TLabel", foreground="#FF7878")
    style.configure("TButton", font=("Segoe UI", 10), padding=6)

    main_frame = ttk.Frame(root)
    main_frame.pack(fill="both", expand=True, padx=20, pady=15)

    # Title
    ttk.Label(main_frame, text="MIDI Macropad", style="Title.TLabel").pack(anchor="w")
    ttk.Label(main_frame, text="Web UI Launcher", style="Status.TLabel").pack(anchor="w", pady=(0, 12))

    # Status indicators
    status_frame = ttk.Frame(main_frame)
    status_frame.pack(fill="x", pady=(0, 10))

    backend_status = ttk.Label(status_frame, text="Backend: ...", style="Status.TLabel")
    backend_status.pack(anchor="w")
    frontend_status = ttk.Label(status_frame, text="Frontend: ...", style="Status.TLabel")
    frontend_status.pack(anchor="w")
    port_status = ttk.Label(status_frame, text=f"Port: {PORT}", style="Status.TLabel")
    port_status.pack(anchor="w")

    # Log area
    log_frame = ttk.Frame(main_frame)
    log_frame.pack(fill="both", expand=True, pady=(5, 10))

    log_text = tk.Text(log_frame, height=8, bg="#282838", fg="#A0A0B8",
                       font=("Cascadia Code", 9), relief="flat", bd=0,
                       insertbackground="#6EB4FF", selectbackground="#3A3A52",
                       wrap="word", state="disabled")
    log_text.pack(fill="both", expand=True)

    # Buttons
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x")

    btn_open = tk.Button(btn_frame, text="Open in Browser", command=lambda: webbrowser.open(URL),
                         bg="#323248", fg="#E8E8F0", activebackground="#3A3A52",
                         activeforeground="#6EB4FF", relief="flat", font=("Segoe UI", 10),
                         padx=12, pady=4, state="disabled")
    btn_open.pack(side="left")

    btn_restart = tk.Button(btn_frame, text="Restart", command=lambda: restart_backend(),
                            bg="#323248", fg="#FFC85A", activebackground="#3A3A52",
                            activeforeground="#FFC85A", relief="flat", font=("Segoe UI", 10),
                            padx=12, pady=4, state="disabled")
    btn_restart.pack(side="left", padx=(8, 0))

    btn_stop = tk.Button(btn_frame, text="Stop & Exit", command=lambda: stop_all(),
                         bg="#323248", fg="#FF7878", activebackground="#3A3A52",
                         activeforeground="#FF7878", relief="flat", font=("Segoe UI", 10),
                         padx=12, pady=4)
    btn_stop.pack(side="right")

    # --- Process management ---
    backend_proc: subprocess.Popen | None = None
    stopping = False

    def log(msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        log_text.configure(state="normal")
        log_text.insert("end", f"[{ts}] {msg}\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def update_status(component: str, status: str, style_name: str) -> None:
        if component == "backend":
            backend_status.configure(text=f"Backend: {status}", style=style_name)
        elif component == "frontend":
            frontend_status.configure(text=f"Frontend: {status}", style=style_name)

    def check_and_build_frontend() -> bool:
        if _needs_npm_install():
            log("Installing npm dependencies...")
            update_status("frontend", "npm install...", "Yellow.TLabel")
            result = subprocess.run(
                ["npm", "install"], cwd=str(FRONTEND_DIR),
                capture_output=True, text=True, shell=True,
            )
            if result.returncode != 0:
                log(f"npm install failed: {result.stderr[-200:]}")
                update_status("frontend", "npm install FAILED", "Red.TLabel")
                return False
            log("npm dependencies installed")

        if _needs_build():
            log("Building frontend...")
            update_status("frontend", "Building...", "Yellow.TLabel")
            result = subprocess.run(
                ["npm", "run", "build"], cwd=str(FRONTEND_DIR),
                capture_output=True, text=True, shell=True,
            )
            if result.returncode != 0:
                log(f"Build failed: {result.stderr[-200:]}")
                update_status("frontend", "Build FAILED", "Red.TLabel")
                return False
            log("Frontend built successfully")

        update_status("frontend", "Ready (dist/)", "Green.TLabel")
        return True

    def start_backend() -> None:
        nonlocal backend_proc
        if backend_proc and backend_proc.poll() is None:
            return

        log("Starting backend...")
        update_status("backend", "Starting...", "Yellow.TLabel")

        python = str(VENV_PYTHONW if VENV_PYTHONW.exists() else VENV_PYTHON)
        backend_proc = subprocess.Popen(
            [python, str(WEB_MAIN), "--browser", "--port", str(PORT)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # Poll until server is up
        def wait_for_ready():
            for _ in range(40):  # 8 seconds
                if stopping:
                    return
                if _is_port_in_use(PORT):
                    root.after(0, lambda: on_backend_ready())
                    return
                time.sleep(0.2)
            root.after(0, lambda: on_backend_failed())

        threading.Thread(target=wait_for_ready, daemon=True).start()

    def on_backend_ready() -> None:
        log(f"Backend running on {URL}")
        update_status("backend", f"Running (:{PORT})", "Green.TLabel")
        btn_open.configure(state="normal")
        btn_restart.configure(state="normal")

    def on_backend_failed() -> None:
        log("Backend failed to start!")
        update_status("backend", "FAILED", "Red.TLabel")

    def stop_backend() -> None:
        nonlocal backend_proc
        if backend_proc and backend_proc.poll() is None:
            log("Stopping backend...")
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
            log("Backend stopped")
        backend_proc = None
        update_status("backend", "Stopped", "Red.TLabel")
        btn_open.configure(state="disabled")
        btn_restart.configure(state="disabled")

    def restart_backend() -> None:
        stop_backend()
        time.sleep(0.5)
        start_backend()

    def stop_all() -> None:
        nonlocal stopping
        stopping = True
        stop_backend()
        _cleanup_pid()
        root.destroy()

    def startup_sequence() -> None:
        log("MIDI Macropad Launcher starting...")

        # Check venv
        if not VENV_PYTHON.exists():
            log("ERROR: .venv not found. Run 'MIDI Macropad.bat' first.")
            update_status("backend", "No .venv", "Red.TLabel")
            return

        # Build frontend if needed
        if not check_and_build_frontend():
            return

        # Start backend
        start_backend()

    # Monitor backend process
    def monitor() -> None:
        if stopping:
            return
        if backend_proc and backend_proc.poll() is not None:
            exit_code = backend_proc.returncode
            if not stopping:
                log(f"Backend exited (code {exit_code})")
                update_status("backend", f"Exited ({exit_code})", "Red.TLabel")
                btn_open.configure(state="disabled")
                btn_restart.configure(state="normal")
        root.after(2000, monitor)

    root.after(2000, monitor)

    # Handle window close
    root.protocol("WM_DELETE_WINDOW", stop_all)

    # Run startup in background
    threading.Thread(target=startup_sequence, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    run_launcher()
