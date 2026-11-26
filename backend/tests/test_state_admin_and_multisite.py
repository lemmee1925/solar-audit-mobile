import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from models import Base, Organization, Site, User, UserSiteAccess
from central_access import (
    SiteAccessDenied,
    SiteSelectionRequired,
    list_accessible_sites,
    resolve_site_context,
)


def _setup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_state_admin_only_sees_and_resolves_assigned_state():
    db = _setup_db()
    try:
        org = Organization(name="CleanMax", is_active=True)
        db.add(org)
        db.flush()
        mh1 = Site(site_id="AMT01", site_name="Amravati 01", state="Maharashtra", organization_id=org.id, is_active=True)
        mh2 = Site(site_id="PUN01", site_name="Pune 01", state="Maharashtra", organization_id=org.id, is_active=True)
        gj1 = Site(site_id="KUT01", site_name="Kutch 01", state="Gujarat", organization_id=org.id, is_active=True)
        db.add_all([mh1, mh2, gj1])
        st_admin = User(
            username="mh_state_admin",
            full_name="MH State Admin",
            password_hash=generate_password_hash("secret123"),
            role="state_admin",
            is_active=True,
            site_id=None,
            organization_id=org.id,
            assigned_state="Maharashtra",
        )
        db.add(st_admin)
        db.commit()

        sites = list_accessible_sites(db, st_admin)
        assert [s.site_id for s in sites] == ["AMT01", "PUN01"]

        with pytest.raises(SiteSelectionRequired):
            resolve_site_context(db, st_admin)

        ctx = resolve_site_context(db, st_admin, "AMT01")
        assert ctx.site_id == "AMT01"
        assert ctx.is_hq_admin is True

        with pytest.raises(SiteAccessDenied):
            resolve_site_context(db, st_admin, "KUT01")
    finally:
        db.close()


def test_engineer_multisite_access_via_user_site_access():
    db = _setup_db()
    try:
        org = Organization(name="CleanMax", is_active=True)
        db.add(org)
        db.flush()
        s1 = Site(site_id="AMT01", site_name="Amravati 01", state="Maharashtra", organization_id=org.id, is_active=True)
        s2 = Site(site_id="PUN01", site_name="Pune 01", state="Maharashtra", organization_id=org.id, is_active=True)
        s3 = Site(site_id="KUT01", site_name="Kutch 01", state="Gujarat", organization_id=org.id, is_active=True)
        db.add_all([s1, s2, s3])
        eng = User(
            username="roving_eng",
            full_name="Roving Engineer",
            password_hash=generate_password_hash("secret123"),
            role="engineer",
            is_active=True,
            site_id="AMT01",
            organization_id=org.id,
        )
        db.add(eng)
        db.flush()
        db.add(UserSiteAccess(user_id=str(eng.id), site_id="PUN01", is_active=True))
        db.commit()

        sites = list_accessible_sites(db, eng)
        assert [s.site_id for s in sites] == ["AMT01", "PUN01"]

        assert resolve_site_context(db, eng).site_id == "AMT01"
        assert resolve_site_context(db, eng, "PUN01").site_id == "PUN01"

        with pytest.raises(SiteAccessDenied):
            resolve_site_context(db, eng, "KUT01")
    finally:
        db.close()


def test_strict_user_management_hierarchy():
    from role_policy import creatable_roles_for, can_manage_target_user

    hq = User(id="1", username="hq", full_name="HQ", password_hash="x", role="hq_admin")
    st1 = User(id="2", username="st1", full_name="ST1", password_hash="x", role="state_admin", assigned_state="Maharashtra")
    st2 = User(id="3", username="st2", full_name="ST2", password_hash="x", role="state_admin", assigned_state="Gujarat")
    sa1 = User(id="4", username="sa1", full_name="SA1", password_hash="x", role="site_admin", site_id="AMT01")
    sa2 = User(id="5", username="sa2", full_name="SA2", password_hash="x", role="site_admin", site_id="AMT01")
    eng = User(id="6", username="eng", full_name="ENG", password_hash="x", role="engineer", site_id="AMT01")
    usr = User(id="7", username="usr", full_name="USR", password_hash="x", role="user", site_id="AMT01")

    assert set(creatable_roles_for(hq)) == {"hq_admin", "state_admin", "site_admin", "engineer", "user"}
    assert set(creatable_roles_for(st1)) == {"site_admin", "engineer", "user"}
    assert set(creatable_roles_for(sa1)) == {"engineer", "user"}
    assert creatable_roles_for(eng) == ()

    # HQ can manage everyone
    for target in (hq, st1, sa1, eng, usr):
        assert can_manage_target_user(hq, target) is True

    # State admin: only strictly lower roles (site_admin, engineer, user), never self/other state_admin/hq_admin
    assert can_manage_target_user(st1, hq) is False
    assert can_manage_target_user(st1, st1) is False
    assert can_manage_target_user(st1, st2) is False
    assert can_manage_target_user(st1, sa1) is True
    assert can_manage_target_user(st1, eng) is True
    assert can_manage_target_user(st1, usr) is True

    # Site admin: only engineer/user, never self/other site_admin/state_admin/hq_admin
    assert can_manage_target_user(sa1, hq) is False
    assert can_manage_target_user(sa1, st1) is False
    assert can_manage_target_user(sa1, sa1) is False
    assert can_manage_target_user(sa1, sa2) is False
    assert can_manage_target_user(sa1, eng) is True
    assert can_manage_target_user(sa1, usr) is True

