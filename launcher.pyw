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

HOST = "10.0.0.27"
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
    dist_mtime = dist.stat().st_mtime
    for f in (FRONTEND_DIR / "src").rglob("*"):
        if f.stat().st_mtime > dist_mtime:
            return True
    return False


# --- tkinter UI ---

def run_launcher():
    import tkinter as tk
    from tkinter import ttk

    if _check_duplicate():
        webbrowser.open(URL)
        return

    _write_pid()

    root = tk.Tk()
    root.title("MIDI Macropad Launcher")
    root.geometry("620x400")
    root.resizable(False, False)
    root.configure(bg="#1E1E2E")

    ico = ROOT / "app_icon.ico"
    if ico.exists():
        try:
            root.iconbitmap(str(ico))
        except Exception:
            pass

    # -- Colors & style --
    BG = "#1E1E2E"
    BG2 = "#282838"
    BG_BTN = "#323248"
    FG = "#E8E8F0"
    FG_MUTED = "#A0A0B8"
    ACCENT = "#6EB4FF"
    GREEN = "#5AE68C"
    YELLOW = "#FFC85A"
    RED = "#FF7878"

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), foreground=ACCENT)
    style.configure("Status.TLabel", font=("Segoe UI", 9), foreground=FG_MUTED)
    style.configure("Green.TLabel", foreground=GREEN)
    style.configure("Yellow.TLabel", foreground=YELLOW)
    style.configure("Red.TLabel", foreground=RED)
    style.configure("Sidebar.TFrame", background=BG2)

    # -- Layout: sidebar (left) + logs (center) --
    outer = ttk.Frame(root)
    outer.pack(fill="both", expand=True)

    # Sidebar
    sidebar = tk.Frame(outer, bg=BG2, width=180)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    # Sidebar header
    tk.Label(sidebar, text="MIDI Macropad", font=("Segoe UI", 12, "bold"),
             bg=BG2, fg=ACCENT, anchor="w").pack(fill="x", padx=12, pady=(14, 2))
    tk.Label(sidebar, text="Web UI Launcher", font=("Segoe UI", 9),
             bg=BG2, fg=FG_MUTED, anchor="w").pack(fill="x", padx=12, pady=(0, 10))

    # Separator
    tk.Frame(sidebar, bg="#3A3A52", height=1).pack(fill="x", padx=8, pady=4)

    # Status indicators in sidebar
    backend_status = tk.Label(sidebar, text="Backend: ...", font=("Segoe UI", 9),
                              bg=BG2, fg=FG_MUTED, anchor="w")
    backend_status.pack(fill="x", padx=12, pady=(6, 0))
    frontend_status = tk.Label(sidebar, text="Frontend: ...", font=("Segoe UI", 9),
                               bg=BG2, fg=FG_MUTED, anchor="w")
    frontend_status.pack(fill="x", padx=12, pady=(2, 0))
    port_label = tk.Label(sidebar, text=f"Port: {PORT}", font=("Segoe UI", 9),
                          bg=BG2, fg=FG_MUTED, anchor="w")
    port_label.pack(fill="x", padx=12, pady=(2, 8))

    # Separator
    tk.Frame(sidebar, bg="#3A3A52", height=1).pack(fill="x", padx=8, pady=4)

    # -- Sidebar buttons --
    def make_btn(parent, text, fg_color, command, state="normal"):
        btn = tk.Button(parent, text=text, command=command,
                        bg=BG_BTN, fg=fg_color, activebackground="#3A3A52",
                        activeforeground=fg_color, relief="flat",
                        font=("Segoe UI", 10), anchor="w", padx=10, pady=5,
                        state=state, cursor="hand2")
        btn.pack(fill="x", padx=8, pady=2)
        # Hover effect
        btn.bind("<Enter>", lambda e: btn.configure(bg="#3A3A60") if btn["state"] != "disabled" else None)
        btn.bind("<Leave>", lambda e: btn.configure(bg=BG_BTN))
        return btn

    btn_open = make_btn(sidebar, "Open in Browser", ACCENT,
                        lambda: webbrowser.open(URL), state="disabled")

    def copy_url():
        root.clipboard_clear()
        root.clipboard_append(URL)
        root.update()
        log(f"Copied: {URL}")

    btn_copy = make_btn(sidebar, "Copy Link", FG, copy_url)

    # Separator
    tk.Frame(sidebar, bg="#3A3A52", height=1).pack(fill="x", padx=8, pady=6)

    btn_rebuild = make_btn(sidebar, "Rebuild UI", GREEN,
                           lambda: threading.Thread(target=rebuild_frontend, daemon=True).start())

    btn_restart = make_btn(sidebar, "Restart Backend", YELLOW,
                           lambda: threading.Thread(target=restart_backend, daemon=True).start(),
                           state="disabled")

    # Spacer
    tk.Frame(sidebar, bg=BG2).pack(fill="both", expand=True)

    btn_stop = make_btn(sidebar, "Stop & Exit", RED, lambda: stop_all())

    # -- Main area: logs --
    main_area = ttk.Frame(outer)
    main_area.pack(side="left", fill="both", expand=True)

    # Log header
    log_header = tk.Frame(main_area, bg=BG)
    log_header.pack(fill="x", padx=12, pady=(10, 4))
    tk.Label(log_header, text="Logs", font=("Segoe UI", 10, "bold"),
             bg=BG, fg=FG_MUTED).pack(side="left")

    # Log area
    log_frame = tk.Frame(main_area, bg=BG)
    log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    log_text = tk.Text(log_frame, bg=BG2, fg=FG_MUTED,
                       font=("Cascadia Code", 9), relief="flat", bd=0,
                       insertbackground=ACCENT, selectbackground="#3A3A52",
                       wrap="word", state="disabled", padx=8, pady=6)
    log_text.pack(fill="both", expand=True)

    # Tag colors for log highlighting
    log_text.tag_configure("error", foreground=RED)
    log_text.tag_configure("ok", foreground=GREEN)
    log_text.tag_configure("warn", foreground=YELLOW)

    # --- Process management ---
    backend_proc: subprocess.Popen | None = None
    stopping = False
    _restart_count = 0
    _MAX_RESTARTS = 3

    def log(msg: str, tag: str = "") -> None:
        ts = time.strftime("%H:%M:%S")
        log_text.configure(state="normal")
        if tag:
            log_text.insert("end", f"[{ts}] {msg}\n", tag)
        else:
            log_text.insert("end", f"[{ts}] {msg}\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def update_status(component: str, status: str, color: str) -> None:
        if component == "backend":
            backend_status.configure(text=f"Backend: {status}", fg=color)
        elif component == "frontend":
            frontend_status.configure(text=f"Frontend: {status}", fg=color)

    def check_and_build_frontend() -> bool:
        if _needs_npm_install():
            log("Installing npm dependencies...", "warn")
            update_status("frontend", "npm install...", YELLOW)
            result = subprocess.run(
                ["npm", "install"], cwd=str(FRONTEND_DIR),
                capture_output=True, text=True, shell=True,
            )
            if result.returncode != 0:
                log(f"npm install failed: {result.stderr[-200:]}", "error")
                update_status("frontend", "npm install FAILED", RED)
                return False
            log("npm dependencies installed", "ok")

        if _needs_build():
            log("Building frontend...")
            update_status("frontend", "Building...", YELLOW)
            result = subprocess.run(
                ["npm", "run", "build"], cwd=str(FRONTEND_DIR),
                capture_output=True, text=True, shell=True,
            )
            if result.returncode != 0:
                log(f"Build failed: {result.stderr[-200:]}", "error")
                update_status("frontend", "Build FAILED", RED)
                return False
            log("Frontend built successfully", "ok")

        update_status("frontend", "Ready", GREEN)
        return True

    def start_backend() -> None:
        nonlocal backend_proc
        if backend_proc and backend_proc.poll() is None:
            return

        log("Starting backend...")
        update_status("backend", "Starting...", YELLOW)

        python = str(VENV_PYTHON)
        log_file = ROOT / "logs" / "web_backend.log"
        log_file.parent.mkdir(exist_ok=True)
        err_handle = open(str(log_file), "w", encoding="utf-8")

        backend_proc = subprocess.Popen(
            [python, str(WEB_MAIN), "--port", str(PORT)],
            cwd=str(ROOT),
            stdout=err_handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        backend_proc._log_handle = err_handle

        def wait_for_ready():
            for _ in range(50):
                if stopping:
                    return
                if backend_proc.poll() is not None:
                    try:
                        err_text = log_file.read_text(encoding="utf-8")[-500:]
                    except Exception:
                        err_text = ""
                    root.after(0, lambda t=err_text: on_backend_crashed(t))
                    return
                if _is_port_in_use(PORT):
                    root.after(0, lambda: on_backend_ready())
                    return
                time.sleep(0.2)
            root.after(0, lambda: on_backend_failed())

        threading.Thread(target=wait_for_ready, daemon=True).start()

    def on_backend_ready() -> None:
        nonlocal _restart_count
        _restart_count = 0
        log(f"Backend running on {URL}", "ok")
        update_status("backend", f"Running (:{PORT})", GREEN)
        btn_open.configure(state="normal")
        btn_restart.configure(state="normal")

    def on_backend_failed() -> None:
        log("Backend failed to start (timeout)!", "error")
        update_status("backend", "FAILED (timeout)", RED)
        btn_restart.configure(state="normal")

    def on_backend_crashed(err_text: str) -> None:
        log("Backend crashed on startup!", "error")
        if err_text:
            for line in err_text.strip().split("\n")[-5:]:
                line = line.strip()
                if line:
                    log(f"  {line}", "error")
        log("Check logs/web_backend.log for full trace")
        update_status("backend", "CRASHED", RED)
        btn_restart.configure(state="normal")

    def stop_backend() -> None:
        nonlocal backend_proc
        if backend_proc and backend_proc.poll() is None:
            log("Stopping backend...")
            if hasattr(backend_proc, '_log_handle'):
                try:
                    backend_proc._log_handle.close()
                except Exception:
                    pass
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
            log("Backend stopped")
        backend_proc = None
        update_status("backend", "Stopped", RED)
        btn_open.configure(state="disabled")
        btn_restart.configure(state="disabled")

    def restart_backend() -> None:
        nonlocal _restart_count
        _restart_count = 0
        stop_backend()
        time.sleep(0.5)
        start_backend()

    def rebuild_frontend() -> None:
        btn_rebuild.configure(state="disabled")
        log("Rebuilding frontend...")
        update_status("frontend", "Building...", YELLOW)
        result = subprocess.run(
            ["npm", "run", "build"], cwd=str(FRONTEND_DIR),
            capture_output=True, text=True, shell=True,
        )
        if result.returncode == 0:
            log("Frontend rebuilt. Refresh browser (F5).", "ok")
            update_status("frontend", "Ready (rebuilt)", GREEN)
        else:
            err = result.stderr[-200:] if result.stderr else "unknown error"
            log(f"Build failed: {err}", "error")
            update_status("frontend", "Build FAILED", RED)
        btn_rebuild.configure(state="normal")

    def stop_all() -> None:
        nonlocal stopping
        stopping = True
        stop_backend()
        _cleanup_pid()
        root.destroy()

    def startup_sequence() -> None:
        log("MIDI Macropad Launcher starting...")

        if not VENV_PYTHON.exists():
            log("ERROR: .venv not found. Run setup first.", "error")
            update_status("backend", "No .venv", RED)
            return

        if not check_and_build_frontend():
            return

        start_backend()

    # Monitor backend process with auto-restart
    def monitor() -> None:
        nonlocal _restart_count
        if stopping:
            return
        if backend_proc and backend_proc.poll() is not None:
            exit_code = backend_proc.returncode
            if not stopping:
                btn_open.configure(state="disabled")
                if _restart_count < _MAX_RESTARTS:
                    _restart_count += 1
                    delay = 2.0 * _restart_count
                    log(f"Backend crashed (code {exit_code}), "
                        f"restarting in {delay:.0f}s ({_restart_count}/{_MAX_RESTARTS})...", "warn")
                    update_status("backend", f"Restarting {_restart_count}/{_MAX_RESTARTS}", YELLOW)
                    root.after(int(delay * 1000),
                               lambda: threading.Thread(target=start_backend, daemon=True).start())
                else:
                    log(f"Backend crashed {_MAX_RESTARTS} times. Manual restart required.", "error")
                    update_status("backend", "CRASHED (max retries)", RED)
                    btn_restart.configure(state="normal")
        root.after(2000, monitor)

    root.after(2000, monitor)
    root.protocol("WM_DELETE_WINDOW", stop_all)
    threading.Thread(target=startup_sequence, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    run_launcher()
