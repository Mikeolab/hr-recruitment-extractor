#!/usr/bin/env python3
"""
Windows launcher for HR Recruitment Extractor.

How frozen-EXE Streamlit works (matches lead-extractor pattern):
  1. Parent  : HRExtractor.exe  (no env STREAMLIT_CHILD)
               → starts FastAPI thread
               → spawns itself again as child with STREAMLIT_CHILD=1
               → waits for port 8502, opens browser
  2. Child   : HRExtractor.exe  (STREAMLIT_CHILD=1 in env)
               → detects flag at __name__=="__main__"
               → calls stcli.main() with correct sys.argv → Streamlit runs

Logs: %APPDATA%\HRExtractor\error.log
      %APPDATA%\HRExtractor\streamlit_stderr.log
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
    BASE = Path(sys._MEIPASS)          # type: ignore[attr-defined]

    # Point Playwright at the bundled Chromium if present
    _bundled = BASE / "playwright_browsers"
    if _bundled.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
else:
    BASE = Path(__file__).parent

sys.path.insert(0, str(BASE))

_MAIN_SCRIPT = BASE / "app" / "main.py"


# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
_LOG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "HRExtractor"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE  = _LOG_DIR / "error.log"
_ST_LOG    = _LOG_DIR / "streamlit_stderr.log"


def _log(msg: str):
    ts   = time.strftime("%Y-%m-%d %H:%M:%S")
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


def _wait_for_port(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# FastAPI server (background thread)
# ---------------------------------------------------------------------------
_API_PORT = 8001
_ST_PORT  = 8502
_api_state: dict = {"failed": False, "error": ""}


def _run_api_server():
    """
    Start uvicorn in-process.
    log_config=None  — prevents 'Unable to configure formatter default'
    in frozen (PyInstaller) builds where uvicorn's default logging
    config references missing formatter classes.
    """
    try:
        import uvicorn
        from app.server.automation_server import app as fastapi_app

        config = uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=_API_PORT,
            log_level="error",
            log_config=None,
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception:
        err = traceback.format_exc()
        _api_state["failed"] = True
        _api_state["error"]  = err
        _log(f"[API] CRASHED:\n{err}")


# ---------------------------------------------------------------------------
# Streamlit child process
# ---------------------------------------------------------------------------
def _spawn_streamlit() -> "subprocess.Popen[bytes]":
    """
    Spawn this same EXE (or Python in dev) as a child process with
    STREAMLIT_CHILD=1 so the __main__ guard below routes it to stcli.main().
    """
    if getattr(sys, "frozen", False):
        # Frozen: re-spawn ourselves; __main__ detects STREAMLIT_CHILD and runs stcli
        cmd = [
            sys.executable,
            "streamlit", "run", str(_MAIN_SCRIPT),
            f"--server.port={_ST_PORT}",
            "--server.address=127.0.0.1",
            "--server.headless=true",
            "--server.fileWatcherType=none",
            "--browser.gatherUsageStats=false",
            "--server.enableCORS=false",
            "--server.enableXsrfProtection=false",
            "--server.runOnSave=false",
            "--global.developmentMode=false",
        ]
    else:
        # Dev: use the venv Python with -m streamlit
        venv_py = BASE / ".venv" / "Scripts" / "python.exe"
        py = str(venv_py) if venv_py.exists() else sys.executable
        cmd = [
            py, "-m", "streamlit", "run", str(_MAIN_SCRIPT),
            f"--server.port={_ST_PORT}",
            "--server.address=127.0.0.1",
            "--server.headless=true",
            "--server.fileWatcherType=none",
            "--browser.gatherUsageStats=false",
        ]

    env = os.environ.copy()
    env["PYTHONPATH"]      = str(BASE)
    env["STREAMLIT_CHILD"] = "1"
    env["AUTOMATION_SERVER_URL"] = f"http://localhost:{_API_PORT}"
    env["WEBSOCKET_URL"]         = f"ws://localhost:{_API_PORT}/ws"

    # Truncate previous stderr log
    try:
        with open(_ST_LOG, "w", encoding="utf-8") as f:
            f.write(f"# streamlit_stderr.log {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception:
        pass

    stderr_fh = open(_ST_LOG, "a", encoding="utf-8", errors="replace")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
        cwd=str(BASE),
        env=env,
        creationflags=creationflags,
    )
    return proc


def _tail_log(path: Path, max_chars: int = 2000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-max_chars:]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main launcher logic
# ---------------------------------------------------------------------------
def _main():
    _log("=" * 60)
    _log("HR Recruitment Extractor starting")
    _log(f"BASE : {BASE}")
    _log(f"Logs : {_LOG_DIR}")

    # ── Start FastAPI ────────────────────────────────────────────────────────
    _log(f"Starting API server on port {_API_PORT}...")
    threading.Thread(target=_run_api_server, daemon=True, name="api-server").start()

    if not _wait_for_port(_API_PORT, timeout=20):
        if _api_state["failed"]:
            _log(f"ERROR: API server crashed: {_api_state['error'][:300]}")
        else:
            _log(f"WARNING: API server not ready after 20 s (may still be starting)")
    else:
        _log(f"API server ready on port {_API_PORT}")

    # ── Start Streamlit child ────────────────────────────────────────────────
    _log(f"Spawning Streamlit child on port {_ST_PORT}...")
    proc = _spawn_streamlit()
    _log(f"Streamlit PID: {proc.pid}")

    if not _wait_for_port(_ST_PORT, timeout=60):
        tail = _tail_log(_ST_LOG)
        _log(f"ERROR: Streamlit did not start within 60 s\nStreamlit stderr:\n{tail or '(empty)'}")
        print()
        print("=" * 60)
        print("  Streamlit failed to start.")
        print(f"  See log: {_ST_LOG}")
        if tail:
            print(f"\n  Last output:\n{tail[-500:]}")
        print("=" * 60)
        input("Press Enter to exit.")
        proc.terminate()
        return

    _log(f"Streamlit ready on port {_ST_PORT}")

    # ── Open browser ─────────────────────────────────────────────────────────
    url = f"http://localhost:{_ST_PORT}"
    time.sleep(1.0)
    webbrowser.open(url)
    _log(f"Browser opened: {url}")

    print()
    print("=" * 60)
    print("  HR Recruitment Extractor is running!")
    print(f"  UI  -> {url}")
    print(f"  API -> http://localhost:{_API_PORT}")
    print()
    print("  If the browser shows an error, check:")
    print(f"    {_ST_LOG}")
    print()
    print("  Close this window (or press Ctrl+C) to stop.")
    print("=" * 60)

    try:
        proc.wait()
    except KeyboardInterrupt:
        _log("Ctrl+C — shutting down")
    else:
        # Streamlit exited on its own — check why
        rc = proc.returncode
        if rc not in (0, None):
            tail = _tail_log(_ST_LOG)
            _log(f"WARNING: Streamlit exited with code {rc}")
            print()
            print("=" * 60)
            print(f"  ⚠  Streamlit crashed (exit code {rc})")
            print(f"  Log: {_ST_LOG}")
            if tail:
                print(f"\n  Last output:\n{tail[-1500:]}")
            print("=" * 60)
            input("Press Enter to exit.")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _log("Shutdown complete.")


# ---------------------------------------------------------------------------
# Entry point — STREAMLIT_CHILD detection MUST be first
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # When frozen, the parent EXE re-spawns itself with STREAMLIT_CHILD=1 so
    # the child becomes the Streamlit host. Detect that and short-circuit.
    if getattr(sys, "frozen", False) and os.environ.get("STREAMLIT_CHILD") == "1":
        os.chdir(str(BASE))
        os.environ["PYTHONPATH"] = str(BASE)
        # sys.argv from parent: [exe, "streamlit", "run", "app/main.py", ...]
        # stcli.main() expects argv starting at "streamlit"
        sys.argv = sys.argv[1:] if len(sys.argv) > 1 else ["streamlit", "run", str(_MAIN_SCRIPT)]
        from streamlit.web import cli as stcli
        stcli.main()
        sys.exit(0)

    # Normal parent launch
    try:
        _main()
    except Exception:
        _log(f"UNHANDLED CRASH:\n{traceback.format_exc()}")
        input("Fatal error. Press Enter to exit.")
        sys.exit(1)
