"""Schema-level checks for Central Model Foundation M1.

Run from the backend directory:
    python -m pytest tests/test_central_model_foundation.py -q
"""
from sqlalchemy import inspect

from models import (
    Base, Organization, Site, AuditNumberCounter, MobileAPIToken, User, engine
)


def test_central_models_are_registered_in_metadata():
    expected = {
        "organizations",
        "sites",
        "audit_number_counters",
        "mobile_api_tokens",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_hq_user_can_have_no_site_assignment():
    assert User.__table__.c.site_id.nullable is True


def test_site_registry_has_organization_link_and_unique_site_code():
    assert "organization_id" in Site.__table__.c
    assert Site.__table__.c.site_id.unique is True


def test_audit_counter_is_unique_per_site_and_year():
    constraints = [
        c for c in AuditNumberCounter.__table__.constraints
        if getattr(c, "name", None) == "uq_audit_counter_site_year"
    ]
    assert len(constraints) == 1


def test_mobile_token_schema_has_revocation_fields():
    columns = MobileAPIToken.__table__.c
    assert {"token_hash", "user_id", "expires_at", "revoked_at"}.issubset(set(columns.keys()))
    assert columns.token_hash.unique is True
