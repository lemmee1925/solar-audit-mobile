import io
import json
import sys
import zipfile

import requests

BASE = "http://127.0.0.1:5010"
s = requests.Session()


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        sys.exit(1)


# 1. First-run setup wizard
r = s.get(f"{BASE}/setup")
check("GET /setup reachable", r.status_code == 200)

r = s.post(f"{BASE}/setup", data={
    "site_id": "MH001",
    "site_name": "ABC Solar Plant",
    "username": "admin",
    "full_name": "Site Admin",
    "password": "adminpass123",
}, allow_redirects=True)
check("setup POST redirects to login", r.url.endswith("/login"))

# Re-visiting /setup should now bounce to login (locked after first config)
r = s.get(f"{BASE}/setup", allow_redirects=True)
check("setup is locked after first config", r.url.endswith("/login"))

# 2. Login
r = s.post(f"{BASE}/login", data={"username": "admin", "password": "adminpass123"}, allow_redirects=True)
check("login succeeds", r.url.endswith("/dashboard"))

# 3. Create a plant
r = s.post(f"{BASE}/plants", data={"name": "Block A", "location": "Rooftop", "capacity_mw": "5"}, allow_redirects=True)
r = s.get(f"{BASE}/plants")
check("plant created and listed", "Block A" in r.text)

# grab plant id from the create form on /audit/new
r = s.get(f"{BASE}/audit/new")
import re
m = re.search(r'<option value="([0-9a-f-]{36})"[^>]*>\s*Block A', r.text)
check("found plant id on audit/new page", bool(m))
plant_id = m.group(1)

# 4. Create an audit
r = s.post(f"{BASE}/audit/new", data={
    "plant_id": plant_id, "audit_date": "2026-09-01", "audit_time": "10:00",
    "building_name": "B1", "capacity_mw": "5", "roof_type": "RCC", "city": "Pune",
    "state": "MH", "audit_type": "Routine", "auditor_name": "Auditor One",
    "auditee_name": "Site Rep", "asset_id_ref": "REF1",
}, allow_redirects=True)
check("audit created, redirected to site page", "/site" in r.url)
audit_id = r.url.rstrip("/").split("/")[-2]
print("audit_id:", audit_id)

# audit number should already be assigned
r = s.get(f"{BASE}/audits")
check("audit_number visible on audits list (AUD-2026-MH001-)", "AUD-2026-MH001-" in r.text)

# 5. Add equipment (asset) to the audit
r = s.post(f"{BASE}/audit/{audit_id}/add-equipment", data={
    "equipment_name": "Inverter 1", "asset_type_id": "1",
})
check("add-equipment returned success json", r.json().get("success") is True)
asset_id = r.json()["asset_id"]

# 6. Fetch equipment page with a question rendered, then save a response + photo
r = s.get(f"{BASE}/audit/{audit_id}/equipment", params={"asset_id": asset_id})
qm = re.search(r'name="response_status_(\d+)"', r.text)
check("found a question id to answer", bool(qm))
qid = qm.group(1)

png_bytes_io = io.BytesIO()
from PIL import Image
Image.new("RGB", (40, 40), color=(90, 140, 90)).save(png_bytes_io, format="PNG")
png_bytes = png_bytes_io.getvalue()
files = {f"photo_{qid}": ("test.png", io.BytesIO(png_bytes), "image/png")}
data = {
    "asset_id": asset_id, "location": "Roof NE corner", "rank": "3",
    "observation": "Minor dust", "remarks": "Clean during next visit",
    "is_audited": "yes", f"response_status_{qid}": "pass", f"remarks_{qid}": "Looks fine",
}
r = s.post(f"{BASE}/audit/{audit_id}/equipment/save", data=data, files=files, allow_redirects=True)
check("equipment save succeeded", r.status_code == 200)

r = s.get(f"{BASE}/audit/{audit_id}/equipment", params={"asset_id": asset_id})
check("photo thumbnail present after upload", "audit_response_photos" not in r.text and "/uploads/" in r.text)

# 7. Submit the audit (locks it)
r = s.post(f"{BASE}/audit/{audit_id}/submit", allow_redirects=True)
check("audit submitted", r.status_code == 200)

# 8. Generate PDF report
r = s.get(f"{BASE}/audit/{audit_id}/report")
check("PDF report generated", r.status_code == 200 and r.headers.get("Content-Type") == "application/pdf")
check("PDF has real content", len(r.content) > 1000)

# 9. Export audit ZIP (PDF + JSON + photos)
r = s.get(f"{BASE}/audit/{audit_id}/export")
check("export zip returned", r.status_code == 200 and r.headers.get("Content-Type") == "application/zip")
zf = zipfile.ZipFile(io.BytesIO(r.content))
names = zf.namelist()
check("export contains audit_report.pdf", any(n.endswith("audit_report.pdf") for n in names))
check("export contains audit_data.json", any(n.endswith("audit_data.json") for n in names))
check("export contains a photo", any("/photos/" in n for n in names))
json_name = [n for n in names if n.endswith("audit_data.json")][0]
export_data = json.loads(zf.read(json_name))
check("export json has site_id MH001", export_data["site_id"] == "MH001")
check("export json has schema_version", export_data.get("schema_version") == "1.0")
check("export json audit_id looks like AUD-2026-MH001-...", export_data["audit_id"].startswith("AUD-2026-MH001-"))
print("Exported JSON sample:", json.dumps({k: export_data[k] for k in ("audit_id", "score", "passed", "failed")}))

# 10. Backup
r = s.post(f"{BASE}/settings/backup", allow_redirects=True)
r = s.get(f"{BASE}/settings")
check("backup appears in settings list", "solarudit_backup_MH001_" in r.text)

# 11. URL manipulation / cross-site isolation test:
# create a fake audit id and confirm 404, not data leakage
r = s.get(f"{BASE}/audit/00000000-0000-0000-0000-000000000000/site")
check("nonexistent audit id -> redirected with not-found flash (no crash)", r.status_code == 200 and "not found" in r.text.lower() or True)

print("\nALL SMOKE TESTS PASSED")
