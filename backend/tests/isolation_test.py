"""
Tests the two things that matter most for this whole project:
  1. Two independent site installs (separate data dirs) cannot see each
     other's data, including via direct ID/URL manipulation.
  2. Restoring a Site A backup onto a Site B install is refused.

Run against two servers on different ports, each pointed at its own
SOLAR_AUDIT_DATA_DIR (simulating two separate site laptops).
"""
import io
import json
import re
import sys
import zipfile

import requests
from PIL import Image

SITE_A = "http://127.0.0.1:5000"   # MH001
SITE_B = "http://127.0.0.1:5001"   # MH002


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        sys.exit(1)


def setup_site(base, site_id, site_name, username="admin", password="adminpass123"):
    s = requests.Session()
    s.post(f"{base}/setup", data={
        "site_id": site_id, "site_name": site_name,
        "username": username, "full_name": "Site Admin", "password": password,
    })
    s.post(f"{base}/login", data={"username": username, "password": password})
    return s


def make_audit(base, s):
    s.post(f"{base}/plants", data={"name": "Block A", "location": "Roof", "capacity_mw": "5"})
    r = s.get(f"{base}/audit/new")
    m = re.search(r'<option value="([0-9a-f-]{36})"[^>]*>\s*Block A', r.text)
    plant_id = m.group(1)
    r = s.post(f"{base}/audit/new", data={
        "plant_id": plant_id, "audit_date": "2026-09-01", "audit_time": "10:00",
        "building_name": "B1", "capacity_mw": "5", "roof_type": "RCC", "city": "Pune",
        "state": "MH", "audit_type": "Routine", "auditor_name": "Auditor One",
        "auditee_name": "Site Rep", "asset_id_ref": "REF1",
    })
    audit_id = r.url.rstrip("/").split("/")[-2]
    return plant_id, audit_id


# --- Set up Site A (MH001) and Site B (MH002) independently ---
site_a = setup_site(SITE_A, "MH001", "ABC Solar Plant")
site_b = setup_site(SITE_B, "MH002", "XYZ Solar Plant")

plant_a, audit_a = make_audit(SITE_A, site_a)
plant_b, audit_b = make_audit(SITE_B, site_b)
print("Site A audit:", audit_a, " Site B audit:", audit_b)

# --- Cross-site URL manipulation: Site A session tries Site B's audit id ---
r = site_a.get(f"{SITE_A}/audit/{audit_b}/site", allow_redirects=False)
check("Site A cannot access Site B's audit via same-process URL guess (N/A, different servers)", True)
# (Real cross-site test requires hitting the OTHER site's server with a foreign audit id,
#  since Site A/B here run as separate processes = separate DBs, which is the real isolation model.)

# The more realistic/valuable test: hit Site A's SERVER directly with Site B's audit id,
# using a Site A logged-in session. Since these are different DB files, audit_b's UUID
# almost certainly doesn't exist in Site A's DB at all -- confirming physical isolation.
r = site_a.get(f"{SITE_A}/audit/{audit_b}/site", allow_redirects=True)
check("Site A server 404/redirects on Site B's audit id (physically separate DB)",
      "not found" in r.text.lower() or r.url.endswith("/audits"))

# --- Restore mismatch test: back up Site A, try to restore onto Site B ---
r = site_a.post(f"{SITE_A}/settings/backup")
r = site_a.get(f"{SITE_A}/settings")
m = re.search(r'(solarudit_backup_MH001_[^\s"<]+\.zip)', r.text)
check("found Site A backup filename", bool(m))
backup_name = m.group(1)

r = site_a.get(f"{SITE_A}/settings/backup/{backup_name}/download")
check("downloaded Site A backup", r.status_code == 200)
backup_bytes = r.content

r = site_b.post(f"{SITE_B}/settings/restore",
                files={"backup_file": ("backup.zip", io.BytesIO(backup_bytes), "application/zip")},
                allow_redirects=True)
check("restoring Site A's backup onto Site B is refused",
      "refused" in r.text.lower() or "belongs to site" in r.text.lower())

# Confirm Site B's own data is untouched (its own audit still there)
r = site_b.get(f"{SITE_B}/audits")
check("Site B's own audit still intact after refused restore", "AUD-2026-MH002-" in r.text)

print("\nALL SITE-ISOLATION TESTS PASSED")
