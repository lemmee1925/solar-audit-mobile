import io
import re
import sys
import requests

SITE_A = "http://127.0.0.1:5000"    # MH001, original laptop, has real data
SITE_A2 = "http://127.0.0.1:5002"   # MH001, replacement laptop, fresh/empty


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        sys.exit(1)


# Log into the ORIGINAL Site A install (already configured from the earlier test)
site_a = requests.Session()
r = site_a.post(f"{SITE_A}/login", data={"username": "admin", "password": "adminpass123"})
check("logged into original Site A", r.url.endswith("/dashboard"))

# Get its most recent backup
r = site_a.get(f"{SITE_A}/settings")
m = re.search(r'(solarudit_backup_MH001_[^\s"<]+\.zip)', r.text)
check("found a Site A backup", bool(m))
backup_name = m.group(1)
r = site_a.get(f"{SITE_A}/settings/backup/{backup_name}/download")
backup_bytes = r.content

# Replacement laptop: run through /setup with the SAME site id, temp admin
site_a2 = requests.Session()
r = site_a2.post(f"{SITE_A2}/setup", data={
    "site_id": "MH001", "site_name": "ABC Solar Plant (replacement laptop)",
    "username": "temp_admin", "full_name": "Temp Admin", "password": "temppass123",
})
r = site_a2.post(f"{SITE_A2}/login", data={"username": "temp_admin", "password": "temppass123"})
check("logged into fresh replacement install as temp_admin", r.url.endswith("/dashboard"))

# Confirm it's empty before restore
r = site_a2.get(f"{SITE_A2}/audits")
check("replacement install has no audits yet", "AUD-2026-MH001-" not in r.text)

# Restore the matching-site backup
r = site_a2.post(f"{SITE_A2}/settings/restore",
                 files={"backup_file": ("backup.zip", io.BytesIO(backup_bytes), "application/zip")},
                 allow_redirects=True)
# The restore invalidates temp_admin's session (that user no longer exists in the
# restored DB), so the browser may bounce to /login -- that's expected. The success
# flash (now shown on login.html too) confirms it, but the functional check below
# (original admin's data now present) is the real proof.
check("restore did not error (redirected, not a 500)", r.status_code == 200)
check("post-restore flash mentions restore completing", "restore complete" in r.text.lower())

# After restore, the DB is now Site A's original DB (temp_admin doesn't exist in it,
# but the ORIGINAL admin does) -- log in fresh as the original admin.
site_a2_after = requests.Session()
r = site_a2_after.post(f"{SITE_A2}/login", data={"username": "admin", "password": "adminpass123"})
check("original Site A admin credentials now work on replacement laptop", r.url.endswith("/dashboard"))

r = site_a2_after.get(f"{SITE_A2}/audits")
check("restored data (audit) now visible on replacement laptop", "AUD-2026-MH001-" in r.text)

print("\nRESTORE POSITIVE-PATH TEST PASSED")
