"""
Central path resolution for the Solar Audit app.

Two run modes:
  1. Dev mode (plain `python app.py`) — everything lives next to this file,
     exactly like the original project.
  2. Frozen mode (PyInstaller .exe, installed under
     C:\\Program Files\\SolarAudit\\) — the install directory is treated as
     read-only application code. All *mutable* data (database, photos,
     reports, site config, backups) is written instead to a per-machine data
     directory under %PROGRAMDATA%, so the app works fine even when installed
     under Program Files without admin rights on every launch, and multiple
     Windows user accounts on the same machine see the same site data.

Nothing here talks to the network. All paths are resolved with pathlib so
they work correctly on Windows (backslashes, drive letters) and Linux/dev.
"""
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory containing the application code (read-only once installed)."""
    if is_frozen():
        # PyInstaller sets sys.executable to the .exe path.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _default_data_root() -> Path:
    if is_frozen():
        # Shared, writable-by-standard-users location on Windows.
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            return Path(program_data) / "SolarAudit"
        # Fallback for non-Windows or unusual environments.
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "SolarAudit"
        return app_dir()
    # Dev mode: keep everything next to the source, as before.
    return app_dir()


def data_dir() -> Path:
    """Root directory for all mutable, site-specific data."""
    override = os.environ.get("SOLAR_AUDIT_DATA_DIR")
    d = Path(override) if override else _default_data_root()
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    d = data_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    d = data_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "solar_audit.db"


def photos_dir() -> Path:
    d = data_dir() / "photos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backups_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def site_config_path() -> Path:
    return config_dir() / "site_config.json"


def static_dir() -> Path:
    """Bundled, read-only application assets (logo, fonts, etc.)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "frontend" / "static"
    return app_dir() / "frontend" / "static"


def templates_dir() -> Path:
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "frontend" / "templates"
    return app_dir() / "frontend" / "templates"
