"""
Site identity for this installation.

Every installed copy of Solar Audit belongs to exactly one site. That
identity lives in config/site_config.json (inside the writable data
directory — see paths.py) and, once set, is NOT changeable from inside the
running application by an ordinary user. Reassigning a site requires
deleting/renaming the config file directly on disk (an administrator /
reinstall-level action), which matches requirement: "Do NOT allow ordinary
users to simply change Site ID ... If site reassignment is needed, require
an administrator/reinstallation process."

If no config exists yet, the app runs in "unconfigured" mode and every
request is redirected to the one-time /setup wizard (see app.py) until an
administrator enters the site ID and name for this install.
"""
import json
import re
import uuid
from dataclasses import dataclass, asdict
from typing import Optional

from paths import site_config_path

SITE_ID_PATTERN = re.compile(r'^[A-Z0-9]{2,20}$')


@dataclass
class SiteConfig:
    site_id: str
    site_name: str
    install_id: str          # random UUID, unique per physical install, for backup provenance
    schema_version: str = "1.0"


def is_valid_site_id(site_id: str) -> bool:
    return bool(SITE_ID_PATTERN.match((site_id or '').strip().upper()))


def load_site_config() -> Optional[SiteConfig]:
    p = site_config_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
        return SiteConfig(
            site_id=raw['site_id'],
            site_name=raw['site_name'],
            install_id=raw.get('install_id') or str(uuid.uuid4()),
            schema_version=raw.get('schema_version', '1.0'),
        )
    except Exception:
        # Corrupt config — treat as unconfigured rather than crash. The
        # setup wizard will refuse to overwrite silently; see app.py /setup.
        return None


def save_site_config(site_id: str, site_name: str) -> SiteConfig:
    site_id = site_id.strip().upper()
    if not is_valid_site_id(site_id):
        raise ValueError('Site ID must be 2-20 uppercase letters/numbers, e.g. MH001.')
    site_name = site_name.strip()
    if not site_name:
        raise ValueError('Site name is required.')

    cfg = SiteConfig(site_id=site_id, site_name=site_name, install_id=str(uuid.uuid4()))
    p = site_config_path()
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding='utf-8')
    return cfg


def config_exists() -> bool:
    return site_config_path().exists()
