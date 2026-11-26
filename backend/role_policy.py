"""Canonical role policy shared by web and native API authorization.

Canonical stored values: hq_admin, site_admin, engineer, user.
Legacy values are recognized only for safe compatibility during migration.
"""
CANONICAL_ROLES = ("hq_admin", "state_admin", "site_admin", "engineer", "user")
LEGACY_ROLE_MAP = {"admin": "site_admin", "technician": "user"}
ROLE_RANK = {
    "hq_admin": 4,
    "state_admin": 3,
    "site_admin": 2,
    "engineer": 1,
    "user": 0,
}

def canonical_role(value):
    role = str(value or "").strip().lower()
    return LEGACY_ROLE_MAP.get(role, role)

def role_rank(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return ROLE_RANK.get(canonical_role(role), -1)

def creatable_roles_for(actor):
    """Roles that actor may create or assign.
    Only hq_admin can manage all roles (including hq_admin and state_admin).
    Lower admins can only create/manage roles strictly below their own rank.
    """
    if is_hq_admin(actor):
        return ("hq_admin", "state_admin", "site_admin", "engineer", "user")
    if is_state_admin(actor):
        return ("site_admin", "engineer", "user")
    if is_site_admin(actor):
        return ("engineer", "user")
    return ()

def can_manage_target_user(actor, target):
    """HQ admin can manage any user; others only strictly lower roles (never self)."""
    if is_hq_admin(actor):
        return True
    actor_id = str(getattr(actor, "id", "") or "")
    target_id = str(getattr(target, "id", "") or "")
    if actor_id and actor_id == target_id:
        return False
    return role_rank(actor) > role_rank(target)

def is_hq_admin(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return canonical_role(role) == "hq_admin"

def is_state_admin(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return canonical_role(role) == "state_admin"

def is_portal_admin(user_or_role):
    return is_hq_admin(user_or_role) or is_state_admin(user_or_role)

def is_site_admin(user_or_role):
    role = getattr(user_or_role, "role", user_or_role)
    return canonical_role(role) == "site_admin"

def is_admin(user_or_role):
    return is_hq_admin(user_or_role) or is_state_admin(user_or_role) or is_site_admin(user_or_role)

def can_manage_site(user_or_role):
    return is_admin(user_or_role)

def can_manage_plant_master(user_or_role):
    # Plant master data is an administrative responsibility, not an engineer privilege.
    return is_admin(user_or_role)

def can_work_audits(user_or_role):
    role = canonical_role(getattr(user_or_role, "role", user_or_role))
    return role in {"hq_admin", "site_admin", "engineer", "user"}
