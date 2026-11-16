"""Central-mode multi-site authorization regression test.

Runs against one Flask process + one shared DB in CENTRAL_MODE. It creates:
  Site A -> Alice
  Site B -> Bob
and verifies that IDs from the other site cannot be used to read or mutate
plants, audits, observations, photos, reports, or offline sync payloads.

The test disables CSRF only for this isolated authorization harness; production
CSRF remains enabled by default.
"""
import io
import os
import shutil
import tempfile
import uuid
from pathlib import Path

# Environment must be configured before importing backend.app/models.
DATA_DIR = Path(tempfile.mkdtemp(prefix="solaraudit-central-isolation-"))
os.environ["SOLAR_AUDIT_DATA_DIR"] = str(DATA_DIR)
os.environ["CENTRAL_MODE"] = "1"
os.environ["SECRET_KEY"] = "central-isolation-test-secret"
os.environ["CSRF_ENABLED"] = "0"

from werkzeug.security import generate_password_hash  # noqa: E402
from PIL import Image  # noqa: E402

from models import (  # noqa: E402
    Session, Organization, Site, User, Plant, Audit, AuditSiteDetail,
    AuditAsset, AuditResponse, AuditResponsePhoto, Observation, AuditNumberCounter,
    Asset, AssetType, AuditQuestion, AuditSection,
)
from app import app, UPLOAD_FOLDER  # noqa: E402


def check(label, condition):
    print(f"[{'OK ' if condition else 'FAIL'}] {label}")
    if not condition:
        raise AssertionError(label)


def make_image_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(buf, format="JPEG")
    buf.seek(0)
    return buf


def login(client, username, password):
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    check(f"login succeeds for {username}", response.status_code == 302)


def main():
    try:
        app.config.update(TESTING=True, CSRF_ENABLED=False)

        session = Session()
        try:
            org = Organization(name="CleanMax Test Org")
            session.add(org)
            session.flush()

            site_a = Site(site_id="MH001", site_name="Site A", organization_id=org.id)
            site_b = Site(site_id="MH002", site_name="Site B", organization_id=org.id)
            session.add_all([site_a, site_b])
            session.flush()

            alice = User(username="alice", full_name="Alice", password_hash=generate_password_hash("alice-pass"), role="admin", is_active=True, site_id="MH001")
            bob = User(username="bob", full_name="Bob", password_hash=generate_password_hash("bob-pass"), role="admin", is_active=True, site_id="MH002")
            session.add_all([alice, bob])

            plant_a = Plant(name="Plant A", location="Pune", capacity_mw=5, site_id="MH001")
            plant_b = Plant(name="Plant B", location="Bikaner", capacity_mw=10, site_id="MH002")
            session.add_all([plant_a, plant_b])
            session.flush()

            # Shared checklist rows are not site-owned, as in production.
            section = AuditSection(name="Central Test Section", section_order=999)
            at = AssetType(name="Central Test Asset")
            session.add_all([section, at])
            session.flush()
            question = AuditQuestion(asset_type_id=at.id, section_id=section.id, question_text="Test checkpoint", question_order=1)
            session.add(question)
            session.flush()

            asset_a = Asset(plant_id=plant_a.id, asset_type_id=at.id, asset_name="Inverter A", is_active=True, site_id="MH001")
            asset_b = Asset(plant_id=plant_b.id, asset_type_id=at.id, asset_name="Inverter B", is_active=True, site_id="MH002")
            session.add_all([asset_a, asset_b])
            session.flush()

            audit_a = Audit(id=str(uuid.uuid4()), audit_number="AUD-2026-MH001-000001", plant_id=plant_a.id, site_id="MH001", audit_date="2026-10-07", audit_type="Routine", auditor_name="Alice")
            audit_b = Audit(id=str(uuid.uuid4()), audit_number="AUD-2026-MH002-000001", plant_id=plant_b.id, site_id="MH002", audit_date="2026-10-07", audit_type="Routine", auditor_name="Bob")
            session.add_all([audit_a, audit_b])
            session.flush()
            session.add_all([AuditSiteDetail(audit_id=audit_a.id), AuditSiteDetail(audit_id=audit_b.id)])
            # Keep the DB-safe audit-number counter consistent with the
            # manually seeded audits above. Otherwise the first legitimate
            # offline sync would allocate 000001 again and hit the unique
            # audit_number constraint.
            session.add_all([
                AuditNumberCounter(site_id="MH001", year=2026, last_sequence=1),
                AuditNumberCounter(site_id="MH002", year=2026, last_sequence=1),
            ])

            aa_a = AuditAsset(audit_id=audit_a.id, asset_id=asset_a.id, is_audited=True)
            aa_b = AuditAsset(audit_id=audit_b.id, asset_id=asset_b.id, is_audited=True)
            session.add_all([aa_a, aa_b])
            session.flush()

            resp_a = AuditResponse(audit_id=audit_a.id, asset_id=asset_a.id, question_id=question.id, response_status="not_complied", responded_by="Alice")
            resp_b = AuditResponse(audit_id=audit_b.id, asset_id=asset_b.id, question_id=question.id, response_status="not_complied", responded_by="Bob")
            session.add_all([resp_a, resp_b])
            session.flush()

            photo_a_path = f"{audit_a.id}/{asset_a.id}/alice.jpg"
            photo_b_path = f"{audit_b.id}/{asset_b.id}/bob.jpg"
            session.add_all([
                AuditResponsePhoto(audit_response_id=resp_a.id, file_path=photo_a_path),
                AuditResponsePhoto(audit_response_id=resp_b.id, file_path=photo_b_path),
            ])

            obs_a = Observation(id=str(uuid.uuid4()), audit_id=audit_a.id, audit_response_id=resp_a.id, site_id="MH001", observation="A finding", severity="major", status="open")
            obs_b = Observation(id=str(uuid.uuid4()), audit_id=audit_b.id, audit_response_id=resp_b.id, site_id="MH002", observation="B finding", severity="major", status="open")
            session.add_all([obs_a, obs_b])
            session.commit()
            ids = {
                "plant_a": str(plant_a.id), "plant_b": str(plant_b.id),
                "audit_a": str(audit_a.id), "audit_b": str(audit_b.id),
                "obs_a": str(obs_a.id), "obs_b": str(obs_b.id),
                "photo_a": photo_a_path, "photo_b": photo_b_path,
            }
        finally:
            session.close()

        uploads = Path(UPLOAD_FOLDER)
        for rel in (ids["photo_a"], ids["photo_b"]):
            path = uploads / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(make_image_bytes().read())

        alice_client = app.test_client()
        bob_client = app.test_client()
        login(alice_client, "alice", "alice-pass")
        login(bob_client, "bob", "bob-pass")

        # Own-site reads work.
        check("Alice sees Site A plants", alice_client.get("/plants").status_code == 200 and b"Plant A" in alice_client.get("/plants").data)
        check("Alice sees Site A audits", alice_client.get("/audits").status_code == 200 and b"AUD-2026-MH001-000001" in alice_client.get("/audits").data)
        check("Bob sees Site B audits", bob_client.get("/audits").status_code == 200 and b"AUD-2026-MH002-000001" in bob_client.get("/audits").data)

        # Direct ID manipulation across sites must fail.
        cross_gets = [
            ("Alice cannot open Site B audit", alice_client.get(f"/audit/{ids['audit_b']}/site").status_code in (302, 404)),
            ("Alice cannot open Site B audit equipment", alice_client.get(f"/audit/{ids['audit_b']}/equipment").status_code in (302, 404)),
            ("Alice cannot open Site B audit safety", alice_client.get(f"/audit/{ids['audit_b']}/safety").status_code in (302, 404)),
            ("Alice cannot open Site B plant specs", alice_client.get(f"/plants/{ids['plant_b']}/specs").status_code in (302, 404)),
            ("Alice cannot generate Site B report", alice_client.get(f"/audit/{ids['audit_b']}/report").status_code in (302, 404)),
            ("Alice cannot open Site B observation", alice_client.get(f"/observation/{ids['obs_b']}").status_code in (302, 404)),
            ("Bob cannot open Site A audit", bob_client.get(f"/audit/{ids['audit_a']}/site").status_code in (302, 404)),
            ("Bob cannot open Site A observation", bob_client.get(f"/observation/{ids['obs_a']}").status_code in (302, 404)),
            ("Alice cannot fetch Site B photo", alice_client.get(f"/uploads/{ids['photo_b']}").status_code == 404),
            ("Bob cannot fetch Site A photo", bob_client.get(f"/uploads/{ids['photo_a']}").status_code == 404),
            ("Alice cannot delete Site B audit", alice_client.post(f"/audit/{ids['audit_b']}/delete").status_code in (302, 404)),
        ]
        for label, ok in cross_gets:
            check(label, ok)

        # Offline sync must reject a foreign plant even when the attacker knows its UUID.
        foreign_sync = alice_client.post("/api/offline/sync/audit", json={
            "local_uuid": str(uuid.uuid4()),
            "plant_id": ids["plant_b"],
            "audit_date": "2026-10-07",
            "plant_name": "Plant B",
        })
        check("Alice offline sync cannot claim Site B plant", foreign_sync.status_code == 400 and b"could not be resolved" in foreign_sync.data.lower())

        # Own-site offline sync still works, proving the rejection isn't a blanket block.
        own_uuid = str(uuid.uuid4())
        own_sync = alice_client.post("/api/offline/sync/audit", json={
            "local_uuid": own_uuid,
            "plant_id": ids["plant_a"],
            "audit_date": "2026-10-07",
            "audit_time": "10:00",
            "auditor_name": "Alice",
            "audit_type": "Routine",
        })
        check("Alice offline sync can create Site A audit", own_sync.status_code == 200 and own_sync.json.get("ok") is True)

        # Verify no foreign audit was created under Alice's site by the attack.
        session = Session()
        try:
            foreign_created = session.query(Audit).filter_by(id=own_uuid, site_id="MH002").first()
            check("No audit is created under Site B by Alice's sync", foreign_created is None)
        finally:
            session.close()

        print("\nALL CENTRAL MULTI-SITE ISOLATION TESTS PASSED")
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
