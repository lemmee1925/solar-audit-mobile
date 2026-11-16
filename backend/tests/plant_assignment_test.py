"""
Tests the new plant-assignment feature: within a single site, a
technician/engineer assigned to Plant A must not see or be able to create
audits for Plant B, even though both plants belong to the same site. Admin
remains unrestricted and sees everything.
"""
import re
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:5010"


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        sys.exit(1)


admin = requests.Session()
admin.post(f"{BASE}/setup", data={
    "site_id": "MH001", "site_name": "ABC Solar Plant",
    "username": "admin", "full_name": "Admin", "password": "adminpass123",
})
admin.post(f"{BASE}/login", data={"username": "admin", "password": "adminpass123"})

# Create two plants
admin.post(f"{BASE}/plants", data={"name": "Plant Alpha", "location": "North", "capacity_mw": "5"})
admin.post(f"{BASE}/plants", data={"name": "Plant Beta", "location": "South", "capacity_mw": "3"})
r = admin.get(f"{BASE}/plants")
m_alpha = re.search(r"plants/([0-9a-f-]{36})/specs[^>]*>[\s\S]{0,5}Edit", r.text)
# more robust: pull plant IDs from the audit/new dropdown instead
r = admin.get(f"{BASE}/audit/new")
options = re.findall(r'<option value="([0-9a-f-]{36})"[^>]*>\s*([^<]+)</option>', r.text)
plant_map = {name.split(" - ")[0].strip(): pid for pid, name in options}
check("both plants found in admin's dropdown", "Plant Alpha" in plant_map and "Plant Beta" in plant_map)
alpha_id = plant_map["Plant Alpha"]
beta_id = plant_map["Plant Beta"]

# Create or reuse a technician assigned ONLY to Plant Alpha
from models import Session, User, UserPlantAssignment

db = Session()
try:
    existing_tech = db.query(User).filter_by(
        username="tech_alpha"
    ).first()

    if existing_tech:
        db.query(UserPlantAssignment).filter_by(
            user_id=existing_tech.id
        ).delete()
        db.add(UserPlantAssignment(
            user_id=existing_tech.id,
            plant_id=alpha_id,
            site_id=existing_tech.site_id
        ))
        db.commit()
        print("[OK ] existing technician assigned to this run's Plant Alpha")
    else:
        r = admin.post(f"{BASE}/users/new", data={
            "username": "tech_alpha",
            "full_name": "Tech Alpha",
            "password": "techpass123",
            "role": "technician",
            "plant_ids": [alpha_id],
        })
        check(
            "technician created",
            "created successfully" in r.text.lower()
        )
finally:
    db.close()

# Admin creates an audit on Plant Beta (admin is unrestricted)
r = admin.post(f"{BASE}/audit/new", data={
    "plant_id": beta_id, "audit_date": "2026-09-01", "audit_time": "10:00",
    "building_name": "B1", "capacity_mw": "3", "roof_type": "RCC", "city": "Pune",
    "state": "MH", "audit_type": "Routine", "auditor_name": "Admin",
    "auditee_name": "Rep", "asset_id_ref": "REF1",
})
beta_audit_id = r.url.rstrip("/").split("/")[-2]
check("admin created an audit on Plant Beta", "/site" in r.url)

# Admin creates an audit on Plant Alpha too
r = admin.post(f"{BASE}/audit/new", data={
    "plant_id": alpha_id, "audit_date": "2026-09-01", "audit_time": "10:00",
    "building_name": "B1", "capacity_mw": "5", "roof_type": "RCC", "city": "Pune",
    "state": "MH", "audit_type": "Routine", "auditor_name": "Admin",
    "auditee_name": "Rep", "asset_id_ref": "REF1",
})
alpha_audit_id = r.url.rstrip("/").split("/")[-2]
check("admin created an audit on Plant Alpha", "/site" in r.url)

# --- Now log in as tech_alpha (assigned only to Plant Alpha) ---
tech = requests.Session()
r = tech.post(f"{BASE}/login", data={"username": "tech_alpha", "password": "techpass123"})
check("tech_alpha logged in", r.url.endswith("/dashboard"))

# Plants list should show only Plant Alpha
r = tech.get(f"{BASE}/plants")
check("tech sees Plant Alpha", "Plant Alpha" in r.text)
check("tech does NOT see Plant Beta", "Plant Beta" not in r.text)

# Audit/new dropdown should only offer Plant Alpha
r = tech.get(f"{BASE}/audit/new")
check("audit/new dropdown shows Plant Alpha", "Plant Alpha" in r.text)
check("audit/new dropdown hides Plant Beta", "Plant Beta" not in r.text)

# Audits list should show only the Alpha audit, not the Beta one
r = tech.get(f"{BASE}/audits")
check("tech sees the Alpha audit in audits list", alpha_audit_id in r.text)
check("tech does NOT see the Beta audit in audits list", beta_audit_id not in r.text)

# Direct URL access to the Beta audit must be refused (404-equivalent), not shown
r = tech.get(f"{BASE}/audit/{beta_audit_id}/site")
check("direct URL access to Beta's audit is blocked", "not found" in r.text.lower())

# tech CANNOT create an audit for Plant Beta even by posting its plant_id directly
r = tech.post(f"{BASE}/audit/new", data={
    "plant_id": beta_id, "audit_date": "2026-09-01", "audit_time": "10:00",
    "building_name": "B1", "capacity_mw": "3", "roof_type": "RCC", "city": "Pune",
    "state": "MH", "audit_type": "Routine", "auditor_name": "Tech Alpha",
    "auditee_name": "Rep", "asset_id_ref": "REF1",
}, allow_redirects=True)
check("tech blocked from creating an audit on Plant Beta", "not assigned to this plant" in r.text.lower())

# tech CAN create an audit for Plant Alpha (their assigned plant)
r = tech.post(f"{BASE}/audit/new", data={
    "plant_id": alpha_id, "audit_date": "2026-09-02", "audit_time": "11:00",
    "building_name": "B2", "capacity_mw": "5", "roof_type": "RCC", "city": "Pune",
    "state": "MH", "audit_type": "Routine", "auditor_name": "Tech Alpha",
    "auditee_name": "Rep", "asset_id_ref": "REF2",
}, allow_redirects=True)
check("tech successfully created an audit on their own Plant Alpha", "/site" in r.url)

# tech cannot delete audits at all (admin-only now)
r = tech.post(f"{BASE}/audit/{alpha_audit_id}/delete", allow_redirects=True)
check("tech blocked from deleting an audit (admin-only now)", "admin access required" in r.text.lower())

print("\nALL PLANT-ASSIGNMENT TESTS PASSED")
