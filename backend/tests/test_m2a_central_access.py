import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from models import Base, Organization, Site, User
from central_access import (
    InactiveIdentityError, SiteAccessDenied, SiteSelectionRequired,
    resolve_site_context, list_accessible_sites,
)


def setup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(db):
    org = Organization(name="CleanMax", is_active=True)
    db.add(org)
    db.flush()
    a = Site(site_id="MH001", site_name="Site A", organization_id=org.id, is_active=True)
    b = Site(site_id="MH002", site_name="Site B", organization_id=org.id, is_active=True)
    db.add_all([a, b])
    db.flush()
    site_user = User(username="site_user", full_name="Site User", password_hash=generate_password_hash("x"), role="site_admin", is_active=True, site_id="MH001")
    hq = User(username="hq", full_name="HQ Admin", password_hash=generate_password_hash("x"), role="hq_admin", is_active=True, site_id=None, organization_id=org.id)
    disabled = User(username="disabled", full_name="Disabled", password_hash=generate_password_hash("x"), role="site_admin", is_active=False, site_id="MH001")
    db.add_all([site_user, hq, disabled])
    db.flush()
    db.commit()
    return site_user, hq, disabled


def test_site_user_is_bound_to_assigned_site():
    db = setup_db()
    try:
        site_user, _, _ = seed(db)
        ctx = resolve_site_context(db, site_user)
        assert ctx.site_id == "MH001"
        assert not ctx.is_hq_admin
        try:
            resolve_site_context(db, site_user, "MH002")
            assert False, "cross-site selection should be denied"
        except SiteAccessDenied:
            pass
    finally:
        db.close()


def test_hq_admin_can_select_any_active_site_without_individual_grants():
    db = setup_db()
    try:
        _, hq, _ = seed(db)
        try:
            resolve_site_context(db, hq)
            assert False, "site selection should be required"
        except SiteSelectionRequired:
            pass
        assert resolve_site_context(db, hq, "MH001").site_id == "MH001"
        assert resolve_site_context(db, hq, "MH002").site_id == "MH002"
        assert {s.site_id for s in list_accessible_sites(db, hq)} == {"MH001", "MH002"}
    finally:
        db.close()


def test_inactive_user_is_rejected():
    db = setup_db()
    try:
        _, _, disabled = seed(db)
        try:
            resolve_site_context(db, disabled)
            assert False, "inactive user should be denied"
        except InactiveIdentityError:
            pass
    finally:
        db.close()


def test_inactive_site_is_rejected():
    db = setup_db()
    try:
        site_user, _, _ = seed(db)
        db.query(Site).filter_by(site_id="MH001").update({"is_active": False})
        db.commit()
        try:
            resolve_site_context(db, site_user)
            assert False, "inactive site should be denied"
        except SiteAccessDenied:
            pass
    finally:
        db.close()


def test_inactive_organization_is_rejected():
    db = setup_db()
    try:
        site_user, _, _ = seed(db)
        db.query(Organization).update({"is_active": False})
        db.commit()
        try:
            resolve_site_context(db, site_user)
            assert False, "inactive organization should be denied"
        except SiteAccessDenied:
            pass
    finally:
        db.close()


def test_site_user_sees_only_assigned_site_in_site_list():
    db = setup_db()
    try:
        site_user, _, _ = seed(db)
        assert [s.site_id for s in list_accessible_sites(db, site_user)] == ["MH001"]
    finally:
        db.close()


def test_hq_admin_site_list_excludes_inactive_sites_and_organizations():
    db = setup_db()
    try:
        _, hq, _ = seed(db)
        db.query(Site).filter_by(site_id="MH002").update({"is_active": False})
        db.commit()
        assert [s.site_id for s in list_accessible_sites(db, hq)] == ["MH001"]
        db.query(Organization).update({"is_active": False})
        db.commit()
        try:
            list_accessible_sites(db, hq)
            assert False, "HQ access must be denied when its organization is inactive"
        except SiteAccessDenied:
            pass
    finally:
        db.close()
