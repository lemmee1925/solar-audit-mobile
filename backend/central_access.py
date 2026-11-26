"""Central-mode identity and site authorization primitives.

HQ administrators are organization-scoped. State administrators are scoped to
active sites in their assigned state within their organization. Site-scoped
users can access their primary User.site_id plus any active UserSiteAccess grants.
"""
from dataclasses import dataclass
from typing import Optional, List

from models import Organization, Site, User, UserSiteAccess
from role_policy import canonical_role


class CentralAccessError(Exception):
    """Base class for denied or invalid central access."""


class InactiveIdentityError(CentralAccessError):
    pass


class SiteSelectionRequired(CentralAccessError):
    pass


class SiteAccessDenied(CentralAccessError):
    pass


@dataclass(frozen=True)
class SiteContext:
    user_id: str
    site_id: str
    site_name: str
    organization_id: Optional[str]
    organization_name: Optional[str]
    is_hq_admin: bool


def _active_organization(db, organization_id):
    if not organization_id:
        return None
    return db.query(Organization).filter_by(
        id=organization_id, is_active=True
    ).first()


def _is_hq(user):
    return canonical_role(getattr(user, "role", None)) == "hq_admin"


def _is_state_admin(user):
    return canonical_role(getattr(user, "role", None)) == "state_admin"


def _user_allowed_site_ids(db, user: User) -> List[str]:
    site_ids = []
    if getattr(user, "site_id", None):
        site_ids.append(user.site_id)
    grants = (
        db.query(UserSiteAccess.site_id)
        .filter_by(user_id=str(user.id), is_active=True)
        .all()
    )
    for (sid,) in grants:
        if sid and sid not in site_ids:
            site_ids.append(sid)
    return site_ids


def list_accessible_sites(db, user: User) -> List[Site]:
    """Return active sites authorized for this identity."""
    if user is None or not bool(user.is_active):
        raise InactiveIdentityError("User is missing or inactive")

    query = db.query(Site).filter_by(is_active=True)
    if _is_hq(user):
        organization = _active_organization(db, getattr(user, "organization_id", None))
        if organization is None:
            raise SiteAccessDenied("HQ administrator has no active organization")
        query = query.filter(Site.organization_id == organization.id)
    elif _is_state_admin(user):
        organization = _active_organization(db, getattr(user, "organization_id", None))
        if organization is None:
            raise SiteAccessDenied("State administrator has no active organization")
        assigned_state = (getattr(user, "assigned_state", None) or "").strip()
        if not assigned_state:
            raise SiteAccessDenied("State administrator has no assigned state")
        query = query.filter(
            Site.organization_id == organization.id,
            Site.state == assigned_state,
        )
    else:
        allowed_ids = _user_allowed_site_ids(db, user)
        if not allowed_ids:
            raise SiteAccessDenied("Site user has no assigned site")
        query = query.filter(Site.site_id.in_(allowed_ids))

    return query.order_by(Site.site_name.asc()).all()


def resolve_site_context(db, user: User, requested_site_id: Optional[str] = None) -> SiteContext:
    """Resolve one active site within the caller's authorization boundary."""
    if user is None or not bool(user.is_active):
        raise InactiveIdentityError("User is missing or inactive")

    is_hq = _is_hq(user)
    is_state = _is_state_admin(user)
    is_portal = is_hq or is_state

    if is_portal:
        organization = _active_organization(db, getattr(user, "organization_id", None))
        if organization is None:
            raise SiteAccessDenied("Administrator has no active organization")
        if is_state and not (getattr(user, "assigned_state", None) or "").strip():
            raise SiteAccessDenied("State administrator has no assigned state")
        if not requested_site_id:
            raise SiteSelectionRequired("Administrator must select a site")
        effective_site_id = requested_site_id
    else:
        allowed_ids = _user_allowed_site_ids(db, user)
        if not allowed_ids:
            raise SiteAccessDenied("Site user has no assigned site")
        if requested_site_id:
            if requested_site_id not in allowed_ids:
                raise SiteAccessDenied("Site user cannot switch to an unassigned site")
            effective_site_id = requested_site_id
        else:
            effective_site_id = allowed_ids[0]
        organization = None

    site_query = db.query(Site).filter_by(site_id=effective_site_id, is_active=True)
    if is_portal:
        site_query = site_query.filter(Site.organization_id == organization.id)
    if is_state:
        site_query = site_query.filter(Site.state == user.assigned_state.strip())
    site = site_query.first()
    if site is None:
        raise SiteAccessDenied("Site is missing, inactive, or outside the assigned scope")

    if not is_portal:
        if site.organization_id:
            organization = _active_organization(db, site.organization_id)
            if organization is None:
                raise SiteAccessDenied("Site organization is missing or inactive")
        else:
            organization = None

    return SiteContext(
        user_id=str(user.id),
        site_id=site.site_id,
        site_name=site.site_name,
        organization_id=organization.id if organization else None,
        organization_name=organization.name if organization else None,
        is_hq_admin=is_portal,
    )
