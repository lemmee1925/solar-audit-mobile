"""
Entry point for the packaged desktop application (this is what
SolarAuditSetup.exe's shortcut actually runs, via PyInstaller).

Responsibilities (requirement #20):
  - Start the local Flask server (no "python app.py" / "flask run" needed).
  - Automatically open the user's default browser to it.
  - Avoid accidentally launching two servers over the same SQLite file if
    the user double-clicks the shortcut twice (or it's already running).

Nothing here touches the network except binding to 127.0.0.1 — the app is
fully local. See paths.py for how the writable data directory is chosen in
frozen (installed) vs dev mode.
"""
import os
import socket
import sys
import threading
import time
import webbrowser

from paths import data_dir


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', port)) != 0


def _pick_port(preferred: int = 5000, tries: int = 20) -> int:
    for offset in range(tries):
        candidate = preferred + offset
        if _port_is_free(candidate):
            return candidate
    raise RuntimeError('No free port found for the local server.')


def _lock_file_path():
    return data_dir() / '.solarudit.lock'


def _acquire_single_instance_lock():
    """
    Best-effort single-instance guard: if a previous instance's lock file
    points at a port that's still alive, open the browser to it instead of
    starting a second server against the same SQLite file.
    """
    lock_path = _lock_file_path()
    if lock_path.exists():
        try:
            existing_port = int(lock_path.read_text().strip())
            if not _port_is_free(existing_port):
                # Something is already listening on that port -- assume
                # it's our own already-running instance and just reuse it.
                return existing_port
        except (ValueError, OSError):
            pass
    return None


def main():
    already_running_port = _acquire_single_instance_lock()
    if already_running_port is not None:
        try:
            webbrowser.open(f'http://127.0.0.1:{already_running_port}')
        except Exception:
            pass
        return

    port = _pick_port()
    _lock_file_path().write_text(str(port))

    # Import app AFTER path/env setup above so paths.py's frozen-mode
    # detection (sys.frozen) is already meaningful.
    from app import app

    def run_server():
        # Prefer a production WSGI server so slow photo uploads do not monopolize
        # the development server. Fall back to Flask's threaded server when the
        # optional Waitress dependency has not yet been installed.
        try:
            from waitress import serve
        except ImportError:
            app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)
        else:
            serve(app, host='127.0.0.1', port=port, threads=8, channel_timeout=180)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Give the server a moment to bind before opening the browser.
    for _ in range(40):
        if not _port_is_free(port):
            break
        time.sleep(0.1)

    try:
        webbrowser.open(f'http://127.0.0.1:{port}')
    except Exception:
        pass  # headless environment or no default browser configured; server still runs

    try:
        while server_thread.is_alive():
            server_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _lock_file_path().unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == '__main__':
    main()
