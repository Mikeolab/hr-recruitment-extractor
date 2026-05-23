#!/usr/bin/env python3
"""
Windows launcher for HR Recruitment Extractor.

Starts:
  1. FastAPI / uvicorn server  → port 8001  (automation_server.py)
  2. Streamlit UI              → port 8502  (app/main.py)
  3. Opens default browser to  http://localhost:8502

Works both as a plain Python script (.py) and as a frozen PyInstaller EXE.
No licence checking, no PyWebview dependency — just the two servers.

Logs:
  %APPDATA%\\HRExtractor\\error.log
  %APPDATA%\\HRExtractor\\streamlit_stderr.log
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Path setup (frozen EXE vs dev)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # Running as PyInstaller EXE — sys._MEIPASS is the temp extraction dir
    BASE = Path(sys._MEIPASS)  # type: ignore[attr-defined]

    # Point Playwright at the bundled Chromium if present
    _bundled = BASE / "playwright_browsers"
    if _bundled.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
else:
    BASE = Path(__file__).parent

sys.path.insert(0, str(BASE))

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
_LOG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "HRExtractor"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE  = _LOG_DIR / "error.log"
_ST_LOG    = _LOG_DIR / "streamlit_stderr.log"

def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait_for_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# FastAPI server (uvicorn, runs in background thread)
# ---------------------------------------------------------------------------
_API_PORT = 8001
_ST_PORT  = 8502

def _run_api_server():
    """Start uvicorn in-process on port 8001."""
    try:
        import uvicorn
        from app.server.automation_server import app as fastapi_app
        uvicorn.run(fastapi_app, host="127.0.0.1", port=_API_PORT, log_level="warning")
    except Exception:
        _log(f"[API] CRASHED:\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Streamlit child process
# ---------------------------------------------------------------------------
def _streamlit_cmd() -> list[str]:
    """Build the Streamlit command list."""
    if getattr(sys, "frozen", False):
        # Frozen: run the bundled main.py through the frozen Python
        main_py = str(BASE / "app" / "main.py")
        return [
            sys.executable,
            "-m", "streamlit", "run",
            main_py,
            "--server.port", str(_ST_PORT),
            "--server.headless", "true",
            "--server.fileWatcherType", "none",
            "--browser.gatherUsageStats", "false",
            "--logger.level", "warning",
        ]
    else:
        # Dev: use the venv Python directly
        venv_python = str(BASE / ".venv" / "Scripts" / "python.exe")
        if not Path(venv_python).exists():
            venv_python = sys.executable
        return [
            venv_python,
            "-m", "streamlit", "run",
            str(BASE / "app" / "main.py"),
            "--server.port", str(_ST_PORT),
            "--server.headless", "true",
            "--server.fileWatcherType", "none",
            "--browser.gatherUsageStats", "false",
            "--logger.level", "warning",
        ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    _log("=" * 60)
    _log("HR Recruitment Extractor — starting")
    _log(f"BASE path: {BASE}")
    _log(f"Log dir:   {_LOG_DIR}")

    # Check ports available
    if not _port_free(_API_PORT):
        _log(f"WARNING: port {_API_PORT} already in use — API server may be running already")
    if not _port_free(_ST_PORT):
        _log(f"WARNING: port {_ST_PORT} already in use — opening browser anyway")
        webbrowser.open(f"http://localhost:{_ST_PORT}")
        input("Press Enter to exit...")
        return

    # ── Start FastAPI in background thread ────────────────────────────────────
    _log(f"Starting API server on port {_API_PORT}...")
    api_thread = threading.Thread(target=_run_api_server, daemon=True, name="api-server")
    api_thread.start()

    if not _wait_for_port(_API_PORT, timeout=20):
        _log(f"WARNING: API server did not start within 20 s on port {_API_PORT}")
    else:
        _log(f"API server ready on port {_API_PORT}")

    # ── Start Streamlit as child process ─────────────────────────────────────
    cmd = _streamlit_cmd()
    _log(f"Starting Streamlit: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE)
    env["AUTOMATION_SERVER_URL"] = f"http://localhost:{_API_PORT}"
    env["WEBSOCKET_URL"] = f"ws://localhost:{_API_PORT}/ws"

    st_log_handle = open(_ST_LOG, "w", encoding="utf-8")
    st_proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=st_log_handle,
        stderr=subprocess.STDOUT,
        cwd=str(BASE),
    )
    _log(f"Streamlit PID: {st_proc.pid}")

    # Wait for Streamlit to be ready
    if not _wait_for_port(_ST_PORT, timeout=45):
        _log("ERROR: Streamlit did not start within 45 s")
        try:
            with open(_ST_LOG, encoding="utf-8") as f:
                tail = f.read()[-2000:]
            _log(f"Streamlit stderr tail:\n{tail}")
        except Exception:
            pass
        input("Streamlit failed to start. Press Enter to exit.")
        st_proc.terminate()
        return

    _log(f"Streamlit ready on port {_ST_PORT}")

    # ── Open browser ──────────────────────────────────────────────────────────
    url = f"http://localhost:{_ST_PORT}"
    _log(f"Opening browser: {url}")
    # Small delay so Streamlit finishes initialising
    time.sleep(1.2)
    webbrowser.open(url)

    print()
    print("=" * 60)
    print("  HR Recruitment Extractor is running!")
    print(f"  UI  → {url}")
    print(f"  API → http://localhost:{_API_PORT}")
    print()
    print("  Close this window (or press Ctrl+C) to stop.")
    print("=" * 60)

    # ── Keep alive until Streamlit exits ─────────────────────────────────────
    try:
        st_proc.wait()
    except KeyboardInterrupt:
        _log("Ctrl+C — shutting down")
    finally:
        _log("Terminating Streamlit...")
        try:
            st_proc.terminate()
            st_proc.wait(timeout=5)
        except Exception:
            try:
                st_proc.kill()
            except Exception:
                pass
        st_log_handle.close()
        _log("Shutdown complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log(f"UNHANDLED CRASH:\n{traceback.format_exc()}")
        input("Fatal error — see log. Press Enter to exit.")
        sys.exit(1)
