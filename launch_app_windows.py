#!/usr/bin/env python3
"""
Windows launcher for Lead Extractor Pro.

Responsibilities (in order):
  1. Acquire single-instance lock (frozen builds only).
  2. Pre-flight imports (Streamlit, app.config, app.database.db).
  3. Pick a free FastAPI port (prefer 8000, fall back to 8001..8020, then any
     OS-assigned port). Pick a free Streamlit UI port (prefer 8501, same
     fallback strategy).
  4. Export AUTOMATION_SERVER_URL and WEBSOCKET_URL into the environment so the
     Streamlit UI child process talks to the *real* API port. (This is the
     root-cause fix for the WinError 10061 reports: the old launcher could
     bind FastAPI to 8001 while the UI hard-coded 8000.)
  5. Start FastAPI in a background thread; wait for the port to actually
     accept connections. On failure, show a native Windows error dialog with a
     button to open the log folder, and exit with a non-zero code.
  6. Spawn Streamlit as a child process with STDERR captured to a log file;
     wait for the Streamlit port to come up. If the child exits early, tail
     the log into the native error dialog so the user sees *why*.
  7. Open the UI in a native PyWebview window (WebView2 on modern Windows).
     If PyWebview / WebView2 is unavailable, fall back to the user's default
     browser and show a one-shot notice explaining how to get the native
     window back.
  8. On window close (or Streamlit dying), terminate the child cleanly and
     release the lock.

Logs:
  %APPDATA%\\LeadExtractorPro\\error.log            (general events)
  %APPDATA%\\LeadExtractorPro\\crash.log            (top-level uncaught crash)
  %APPDATA%\\LeadExtractorPro\\streamlit_stderr.log (Streamlit child stderr)
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Path setup (frozen vs dev)
# ─────────────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    app_path = base_path / "app"
    main_script = app_path / "main.py"

    # Point Playwright at the bundled Chromium *only* if the executable exists.
    _bundled = base_path / "playwright_browsers"
    _chromium_exe = None
    if _bundled.exists():
        for d in _bundled.iterdir():
            if d.is_dir() and d.name.startswith("chromium-"):
                _exe = (
                    d / "chrome-win" / "chrome.exe"
                    if sys.platform == "win32"
                    else d / "chrome-mac" / "Chromium"
                )
                if _exe.exists():
                    _chromium_exe = _exe
                    break
    if _chromium_exe is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled)
else:
    base_path = Path(__file__).parent
    app_path = base_path / "app"
    main_script = app_path / "main.py"

sys.path.insert(0, str(base_path))


# ─────────────────────────────────────────────────────────────────────────────
# Constants and shared state
# ─────────────────────────────────────────────────────────────────────────────
USER_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LeadExtractorPro"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

ERROR_LOG = USER_DATA_DIR / "error.log"
CRASH_LOG = USER_DATA_DIR / "crash.log"
STREAMLIT_STDERR = USER_DATA_DIR / "streamlit_stderr.log"
LOCK_FILE = USER_DATA_DIR / ".app_lock"

PREFERRED_API_PORT = 8000
PREFERRED_UI_PORT = 8501

# Mutable state shared across threads. Plain dict; only ever written by one
# thread per key (api_* by API thread, streamlit_* by main thread).
SHARED_STATE = {
    "api_port": None,
    "ui_port": None,
    "api_failed": False,
    "api_error": "",
    "streamlit_proc": None,
    "streamlit_failed": False,
    "streamlit_error": "",
}


# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers (never raise)
# ─────────────────────────────────────────────────────────────────────────────
def _log(msg: str, path: Path = ERROR_LOG) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    except Exception:
        pass


def _log_exception(prefix: str, path: Path = ERROR_LOG) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {prefix}\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Single-instance lock
# ─────────────────────────────────────────────────────────────────────────────
def acquire_lock():
    """Return an open file handle on success, or None if another instance holds the lock."""
    try:
        if LOCK_FILE.exists():
            try:
                with open(LOCK_FILE, "r") as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)
                    return None
                except (OSError, ProcessLookupError):
                    LOCK_FILE.unlink()
            except (ValueError, FileNotFoundError):
                if LOCK_FILE.exists():
                    LOCK_FILE.unlink()
        try:
            lock_fd = open(LOCK_FILE, "x")
        except FileExistsError:
            return None
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except Exception as e:
        _log(f"Lock error: {e}")
        return None


def release_lock(lock_fd) -> None:
    try:
        if lock_fd:
            lock_fd.close()
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Port helpers
# ─────────────────────────────────────────────────────────────────────────────
def find_free_port(preferred: int, scan: int = 20) -> int:
    """Try preferred first, then preferred+1..+scan, then any OS-assigned port."""
    for port in [preferred] + [preferred + i for i in range(1, scan + 1)]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            try:
                s.close()
            except Exception:
                pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_port(port: int, timeout: float, host: str = "127.0.0.1") -> bool:
    """Return True once host:port accepts TCP within timeout seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
        time.sleep(0.25)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Native Windows error dialog
# ─────────────────────────────────────────────────────────────────────────────
def show_native_error(title: str, message: str) -> None:
    """
    Show a Windows MessageBox. OK opens the log folder; Cancel just closes.
    On non-Windows or on dialog failure, prints to stderr.
    """
    full = (
        f"{message}\n\n"
        f"Logs: {USER_DATA_DIR}\n\n"
        "Click OK to open the log folder, or Cancel to close."
    )
    if sys.platform != "win32":
        print(f"[{title}] {full}", file=sys.stderr, flush=True)
        return
    try:
        import ctypes

        MB_OKCANCEL = 0x01
        MB_ICONERROR = 0x10
        MB_TOPMOST = 0x40000
        IDOK = 1
        ret = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            0, full, title, MB_OKCANCEL | MB_ICONERROR | MB_TOPMOST
        )
        if ret == IDOK:
            try:
                subprocess.Popen(["explorer", str(USER_DATA_DIR)])
            except Exception:
                pass
    except Exception as e:
        _log(f"show_native_error fallback to stderr: {e}")
        print(f"[{title}] {full}", file=sys.stderr, flush=True)


def show_native_info(title: str, message: str) -> None:
    """Information-style MessageBox. No log-folder shortcut."""
    if sys.platform != "win32":
        print(f"[{title}] {message}", file=sys.stderr, flush=True)
        return
    try:
        import ctypes

        MB_OK = 0x00
        MB_ICONINFORMATION = 0x40
        MB_TOPMOST = 0x40000
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            0, message, title, MB_OK | MB_ICONINFORMATION | MB_TOPMOST
        )
    except Exception as e:
        _log(f"show_native_info fallback to stderr: {e}")
        print(f"[{title}] {message}", file=sys.stderr, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI server (background thread)
# ─────────────────────────────────────────────────────────────────────────────
def start_fastapi_server(port: int) -> bool:
    """Start uvicorn on (127.0.0.1, port) in a daemon thread. Returns True once
    the port accepts connections, False on import/bind/startup failure."""
    try:
        import uvicorn
        from app.server.automation_server import app as fastapi_app

        config = uvicorn.Config(
            fastapi_app, host="127.0.0.1", port=port, log_level="error"
        )
        server = uvicorn.Server(config)

        def _run() -> None:
            try:
                server.run()
            except Exception as e:
                SHARED_STATE["api_failed"] = True
                SHARED_STATE["api_error"] = str(e)
                _log_exception(f"API server crashed: {e}")

        threading.Thread(target=_run, daemon=True, name="fastapi-uvicorn").start()

        if wait_for_port(port, timeout=8.0):
            _log(f"API ready on 127.0.0.1:{port}")
            return True

        if SHARED_STATE["api_failed"]:
            return False

        SHARED_STATE["api_failed"] = True
        SHARED_STATE["api_error"] = (
            f"API never started listening on 127.0.0.1:{port} within 8s"
        )
        _log(SHARED_STATE["api_error"])
        return False
    except Exception as e:
        SHARED_STATE["api_failed"] = True
        SHARED_STATE["api_error"] = f"API import/startup error: {e}"
        _log_exception(f"API import error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit child process
# ─────────────────────────────────────────────────────────────────────────────
def start_streamlit_subprocess(port: int) -> Optional[subprocess.Popen]:
    """Spawn the Streamlit child. STDERR streams to STREAMLIT_STDERR. Returns
    None if Popen itself fails (rare)."""
    try:
        # Truncate previous stderr log so a tail-on-failure shows the *current* run
        try:
            with open(STREAMLIT_STDERR, "w", encoding="utf-8") as _f:
                _f.write(
                    f"# streamlit_stderr.log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
        except Exception:
            pass

        stderr_file = open(STREAMLIT_STDERR, "a", encoding="utf-8", errors="replace")

        streamlit_args = [
            "streamlit", "run", str(main_script),
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
            "--server.headless=true",
            "--global.developmentMode=false",
            "--server.runOnSave=false",
            "--server.fileWatcherType=none",
            "--server.enableCORS=false",
            "--server.enableXsrfProtection=false",
        ]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable] + streamlit_args
        else:
            cmd = [sys.executable, "-m"] + streamlit_args

        env = os.environ.copy()
        env["PYTHONPATH"] = str(base_path)
        if getattr(sys, "frozen", False):
            env["STREAMLIT_CHILD"] = "1"

        creationflags = 0
        if sys.platform == "win32":
            # Don't pop a second console window on top of the launcher
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            cwd=str(base_path),
            env=env,
            creationflags=creationflags,
        )
        SHARED_STATE["streamlit_proc"] = proc
        _log(f"Streamlit child PID {proc.pid} on 127.0.0.1:{port}")
        return proc
    except Exception as e:
        SHARED_STATE["streamlit_failed"] = True
        SHARED_STATE["streamlit_error"] = f"Failed to spawn Streamlit: {e}"
        _log_exception(f"Streamlit spawn error: {e}")
        return None


def _tail_streamlit_stderr(max_chars: int = 1500) -> str:
    try:
        if STREAMLIT_STDERR.exists():
            with open(STREAMLIT_STDERR, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data[-max_chars:] if len(data) > max_chars else data
    except Exception:
        pass
    return ""


def wait_for_streamlit_ready(
    proc: subprocess.Popen, port: int, timeout: float = 45.0
) -> bool:
    """Wait for Streamlit's port to come up. If the process exits before that,
    record the tail of its stderr into SHARED_STATE['streamlit_error']."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            SHARED_STATE["streamlit_failed"] = True
            tail = _tail_streamlit_stderr()
            SHARED_STATE["streamlit_error"] = (
                f"Streamlit exited early (code {proc.returncode}).\n"
                f"Last stderr lines:\n{tail or '(empty)'}"
            )
            _log(f"Streamlit early exit code={proc.returncode}")
            return False
        if wait_for_port(port, timeout=0.5):
            return True
        time.sleep(0.25)

    SHARED_STATE["streamlit_failed"] = True
    SHARED_STATE["streamlit_error"] = (
        f"Streamlit did not start listening on port {port} within {int(timeout)}s.\n"
        f"Last stderr lines:\n{_tail_streamlit_stderr() or '(empty)'}"
    )
    _log(SHARED_STATE["streamlit_error"].splitlines()[0])
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Native window (PyWebview) with browser fallback
# ─────────────────────────────────────────────────────────────────────────────
def open_in_webview(url: str, title: str) -> bool:
    """Open `url` in a native PyWebview window. Blocks until the window closes.
    Returns True if the window opened (and was closed normally), False if
    PyWebview / WebView2 is unavailable so the caller can fall back to a
    browser."""
    try:
        import webview  # pywebview
    except Exception as e:
        _log(f"pywebview import failed: {e}")
        return False
    try:
        webview.create_window(
            title,
            url,
            width=1280,
            height=860,
            min_size=(960, 640),
            confirm_close=False,
        )
        webview.start()
        return True
    except Exception as e:
        _log_exception(f"PyWebview start failed: {e}")
        return False


def open_in_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception as e:
        _log(f"webbrowser.open failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    lock_fd = None
    if getattr(sys, "frozen", False):
        lock_fd = acquire_lock()
        if lock_fd is None:
            show_native_info(
                "Lead Extractor Pro is already running",
                "Another instance is already running.\n\n"
                "Close it from the taskbar first, or delete the lock file:\n"
                "%APPDATA%\\LeadExtractorPro\\.app_lock",
            )
            sys.exit(0)
    else:
        try:
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
        except Exception:
            pass

    proc: Optional[subprocess.Popen] = None
    try:
        # ── Pre-flight imports ──────────────────────────────────────────────
        try:
            import streamlit  # noqa: F401
            from streamlit.web import cli as stcli  # noqa: F401
        except ImportError as e:
            _log_exception(f"Streamlit import error: {e}")
            show_native_error(
                "Lead Extractor Pro could not start",
                f"Streamlit failed to import:\n{e}\n\n"
                "Reinstall the application or run:\n"
                "    pip install -r requirements.txt",
            )
            release_lock(lock_fd)
            sys.exit(1)

        os.environ["PYTHONPATH"] = str(base_path)
        try:
            import app.config  # noqa: F401
            import app.database.db  # noqa: F401
        except Exception as e:
            _log_exception(f"App pre-flight import error: {e}")
            show_native_error(
                "Lead Extractor Pro could not start",
                f"Application module failed to import:\n{e}\n\n"
                "Click OK to open the log folder for the full traceback.",
            )
            release_lock(lock_fd)
            sys.exit(1)

        # ── Pick ports and tell children where to find each other ───────────
        api_port = find_free_port(PREFERRED_API_PORT)
        ui_port = find_free_port(PREFERRED_UI_PORT)
        SHARED_STATE["api_port"] = api_port
        SHARED_STATE["ui_port"] = ui_port

        os.environ["AUTOMATION_SERVER_URL"] = f"http://127.0.0.1:{api_port}"
        os.environ["WEBSOCKET_URL"] = f"ws://127.0.0.1:{api_port}/ws"
        _log(
            f"Startup: api=127.0.0.1:{api_port} ui=127.0.0.1:{ui_port} "
            f"frozen={bool(getattr(sys, 'frozen', False))}"
        )

        # ── FastAPI ─────────────────────────────────────────────────────────
        if not start_fastapi_server(api_port):
            show_native_error(
                "Automation engine failed to start",
                "The background automation server could not start, so searches "
                "and lead extraction will not work.\n\n"
                f"Reason: {SHARED_STATE['api_error'] or 'unknown'}",
            )
            release_lock(lock_fd)
            sys.exit(1)

        # ── Streamlit child ─────────────────────────────────────────────────
        proc = start_streamlit_subprocess(ui_port)
        if proc is None:
            show_native_error(
                "Lead Extractor Pro could not start",
                "The user-interface process failed to launch.\n\n"
                f"Reason: {SHARED_STATE['streamlit_error'] or 'unknown'}",
            )
            release_lock(lock_fd)
            sys.exit(1)

        if not wait_for_streamlit_ready(proc, ui_port, timeout=45.0):
            try:
                proc.terminate()
            except Exception:
                pass
            show_native_error(
                "Lead Extractor Pro user interface failed to start",
                "The user interface did not come up in time.\n\n"
                f"{SHARED_STATE['streamlit_error'][:1200]}",
            )
            release_lock(lock_fd)
            sys.exit(1)

        # ── Open the UI ─────────────────────────────────────────────────────
        url = f"http://127.0.0.1:{ui_port}"
        if open_in_webview(url, title="Lead Extractor Pro"):
            # Native window closed cleanly → quit Streamlit and exit
            pass
        else:
            # PyWebview / WebView2 unavailable → fall back to default browser
            _log("PyWebview unavailable, falling back to default browser.")
            open_in_browser(url)
            show_native_info(
                "Native window unavailable",
                "Could not open the built-in window.\n\n"
                "Microsoft Edge WebView2 Runtime may be missing. "
                "The app has opened in your default browser instead.\n\n"
                "To get the native window back, install "
                "'Microsoft Edge WebView2 Runtime' (free, from Microsoft) "
                "and relaunch Lead Extractor Pro.",
            )
            try:
                while proc.poll() is None:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass

    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log_exception(f"Top-level launcher error: {e}")
        show_native_error(
            "Lead Extractor Pro stopped unexpectedly",
            f"Unexpected error:\n{e}\n\n"
            "Click OK to open the log folder for the full traceback.",
        )
    finally:
        # Stop the Streamlit child if we still own it
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        release_lock(lock_fd)


if __name__ == "__main__":
    # When frozen, the parent EXE re-spawns itself with STREAMLIT_CHILD=1 so
    # the child becomes the Streamlit host. Detect that here and short-circuit
    # everything else.
    if getattr(sys, "frozen", False) and os.environ.get("STREAMLIT_CHILD") == "1":
        os.chdir(str(base_path))
        os.environ["PYTHONPATH"] = str(base_path)
        sys.argv = (
            sys.argv[1:] if len(sys.argv) > 1 else ["streamlit", "run", str(main_script)]
        )
        from streamlit.web import cli as stcli

        stcli.main()
        sys.exit(0)

    try:
        main()
    except Exception as e:
        try:
            CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(str(e))
                f.write("\n" + traceback.format_exc())
        except Exception:
            pass
        if not getattr(sys, "frozen", False):
            print(f"Error: {e}", file=sys.stderr)
        raise
