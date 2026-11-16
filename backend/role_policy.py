"""Canonical role policy shared by web and native API authorization.

Canonical stored values: hq_admin, site_admin, engineer, user.
Legacy values are recognized only for safe compatibility during migration.
"""
CANONICAL_ROLES = ("hq_admin", "site_admin", "engineer", "user")
LEGACY_ROLE_MAP = {"admin": "site_admin", "technician": "user"}

def canonical_role(value):
    role = str(value or "").strip().lower()
    return LEGACY_ROLE_MAP.get(role, role)

def is_hq_admin(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return canonical_role(role) == "hq_admin"

def is_site_admin(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return canonical_role(role) == "site_admin"

def is_admin(user_or_role):
    return is_hq_admin(user_or_role) or is_site_admin(user_or_role)

def can_manage_site(user_or_role):
    return is_admin(user_or_role)

def can_manage_plant_master(user_or_role):
    # Plant master data is an administrative responsibility, not an engineer privilege.
    return is_admin(user_or_role)

def can_work_audits(user_or_role):
    role = canonical_role(getattr(user_or_role, "role", user_or_role))
    return role in {"hq_admin", "site_admin", "engineer", "user"}
