"""Central-mode identity and site authorization primitives.

HQ administrators are organization-scoped. They can administer/select active
sites belonging to their assigned active organization, never sites belonging
to another organization. Site-scoped users remain bound to User.site_id.
"""
from dataclasses import dataclass
from typing import Optional, List

from models import Organization, Site, User
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


def list_accessible_sites(db, user: User) -> List[Site]:
    """Return active sites authorized for this identity.

    HQ Admin: all active sites in the user's explicitly assigned active
    organization. Missing organization membership is a hard denial.
    Site-scoped roles: only the active site in User.site_id.
    """
    if user is None or not bool(user.is_active):
        raise InactiveIdentityError("User is missing or inactive")

    query = db.query(Site).filter_by(is_active=True)
    if _is_hq(user):
        organization = _active_organization(db, getattr(user, "organization_id", None))
        if organization is None:
            raise SiteAccessDenied("HQ administrator has no active organization")
        query = query.filter(Site.organization_id == organization.id)
    else:
        if not user.site_id:
            raise SiteAccessDenied("Site user has no assigned site")
        query = query.filter(Site.site_id == user.site_id)

    return query.order_by(Site.site_name.asc()).all()


def resolve_site_context(db, user: User, requested_site_id: Optional[str] = None) -> SiteContext:
    """Resolve one active site within the caller's authorization boundary."""
    if user is None or not bool(user.is_active):
        raise InactiveIdentityError("User is missing or inactive")

    is_hq_admin = _is_hq(user)
    if is_hq_admin:
        organization = _active_organization(db, getattr(user, "organization_id", None))
        if organization is None:
            raise SiteAccessDenied("HQ administrator has no active organization")
        if not requested_site_id:
            raise SiteSelectionRequired("HQ administrator must select a site")
        effective_site_id = requested_site_id
    else:
        if not user.site_id:
            raise SiteAccessDenied("Site user has no assigned site")
        if requested_site_id and requested_site_id != user.site_id:
            raise SiteAccessDenied("Site user cannot switch to another site")
        effective_site_id = user.site_id
        organization = None

    site_query = db.query(Site).filter_by(site_id=effective_site_id, is_active=True)
    if is_hq_admin:
        site_query = site_query.filter(Site.organization_id == organization.id)
    site = site_query.first()
    if site is None:
        raise SiteAccessDenied("Site is missing, inactive, or outside the assigned organization")

    if is_hq_admin:
        # Already checked above; keep the organization object as the trusted
        # source for the returned context.
        pass
    elif site.organization_id:
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
        is_hq_admin=is_hq_admin,
    )
