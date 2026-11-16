import re
import sys
import requests

BASE = "http://127.0.0.1:5010"


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        sys.exit(1)


admin = requests.Session()
r = admin.post(f"{BASE}/login", data={"username": "admin", "password": "adminpass123"})
check("admin logged in", r.url.endswith("/dashboard"))

r = admin.get(f"{BASE}/audit/new")
options = re.findall(r'<option value="([0-9a-f-]{36})"[^>]*>\s*([^<]+)</option>', r.text)
plant_map = {name.split(" - ")[0].strip(): pid for pid, name in options}
alpha_id = plant_map["Plant Alpha"]

# --- Save specs for Plant Alpha ---
r = admin.post(f"{BASE}/plants/{alpha_id}/specs", data={
    "name": "Plant Alpha", "location": "North", "capacity_mw": "5",
    "building_name": "Alpha Building", "roof_type": "RCC", "city": "Pune", "state": "Maharashtra",
    "no_of_blocks": "4", "dc_capacity": "5.5", "ac_capacity": "5.0",
    "central_inverter_type": "ABB-500", "central_inverter_count": "2",
    "transformer_type": "Step-up-1000kVA", "transformer_count": "1",
}, allow_redirects=True)
check("specs saved", "saved" in r.text.lower())

# --- Confirm the specs page reflects the saved values ---
r = admin.get(f"{BASE}/plants/{alpha_id}/specs")
check("building name persisted", 'value="Alpha Building"' in r.text)
check("central inverter type persisted", 'value="ABB-500"' in r.text)

# --- Confirm data-attributes for auto-fill appear on audit/new dropdown ---
r = admin.get(f"{BASE}/audit/new")
check("plant option carries building-name data attribute", 'data-building-name="Alpha Building"' in r.text)
check("plant option carries roof-type data attribute", 'data-roof-type="RCC"' in r.text)

# --- Create a new audit on Plant Alpha and confirm AuditSiteDetail was pre-filled ---
r = admin.post(f"{BASE}/audit/new", data={
    "plant_id": alpha_id, "audit_date": "2026-09-03", "audit_time": "09:00",
    "building_name": "Alpha Building", "capacity_mw": "5", "roof_type": "RCC", "city": "Pune",
    "state": "Maharashtra", "audit_type": "Routine", "auditor_name": "Admin",
    "auditee_name": "Rep", "asset_id_ref": "REF3",
}, allow_redirects=True)
check("new audit created", "/site" in r.url)
new_audit_id = r.url.rstrip("/").split("/")[-2]

r = admin.get(f"{BASE}/audit/{new_audit_id}/site")
check("site-detail page pre-filled central inverter type from plant spec", "ABB-500" in r.text)
check("site-detail page pre-filled transformer type from plant spec", "Step-up-1000kVA" in r.text)

# --- Force-delete: create a throwaway plant with a submitted audit, then force-delete it ---
admin.post(f"{BASE}/plants", data={"name": "Doomed Plant", "location": "X", "capacity_mw": "1"})
r = admin.get(f"{BASE}/audit/new")
options = re.findall(r'<option value="([0-9a-f-]{36})"[^>]*>\s*([^<]+)</option>', r.text)
plant_map2 = {name.split(" - ")[0].strip(): pid for pid, name in options}
doomed_id = plant_map2["Doomed Plant"]

r = admin.post(f"{BASE}/audit/new", data={
    "plant_id": doomed_id, "audit_date": "2026-09-04", "audit_time": "09:00",
    "building_name": "D1", "capacity_mw": "1", "roof_type": "RCC", "city": "Pune",
    "state": "Maharashtra", "audit_type": "Routine", "auditor_name": "Admin",
    "auditee_name": "Rep", "asset_id_ref": "REF4",
}, allow_redirects=True)
doomed_audit_id = r.url.rstrip("/").split("/")[-2]
admin.post(f"{BASE}/audit/{doomed_audit_id}/submit")

# Normal delete should be blocked (plant has a live/submitted audit)
r = admin.post(f"{BASE}/plants/{doomed_id}/delete", allow_redirects=True)
check("normal delete blocked because plant has an audit", "cannot delete" in r.text.lower())

# Force-delete with WRONG confirmation name should be refused
r = admin.post(f"{BASE}/plants/{doomed_id}/force-delete", data={"confirm_name": "Wrong Name"}, allow_redirects=True)
check("force-delete refused with wrong confirmation name", "did not exactly match" in r.text.lower())

# Force-delete with correct name should succeed
r = admin.post(f"{BASE}/plants/{doomed_id}/force-delete", data={"confirm_name": "Doomed Plant"}, allow_redirects=True)
check("force-delete succeeded with correct name", "permanently deleted" in r.text.lower())

r = admin.get(f"{BASE}/plants")
check("Doomed Plant no longer in plants list", "Doomed Plant" not in r.text)

r = admin.get(f"{BASE}/audit/{doomed_audit_id}/site")
check("doomed plant's audit is now gone too", "not found" in r.text.lower())

print("\nALL PLANT-SPECS & FORCE-DELETE TESTS PASSED")
