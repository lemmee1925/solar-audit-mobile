"""Bootstrap a central Solar Audit database.

Examples:
  Site administrator:
    python bootstrap_central.py --organization "CleanMax" --site-id MH001 \
      --site-name "Example Solar Site" --admin site_admin --admin-name "Site Admin" \
      --password 'change-me' --role site_admin

  Global HQ administrator:
    python bootstrap_central.py --organization "CleanMax" \
      --admin hq_admin --admin-name "HQ Administrator" \
      --password 'change-me' --role hq_admin
"""
import argparse
import os

from werkzeug.security import generate_password_hash
from sqlalchemy import inspect
from models import Session, Organization, Site, User, engine


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--organization', required=True)
    p.add_argument('--site-id')
    p.add_argument('--site-name')
    p.add_argument('--admin', required=True, help='Username to create/update')
    p.add_argument('--admin-name', required=True)
    p.add_argument('--password', required=True)
    p.add_argument('--role', choices=['hq_admin', 'site_admin', 'admin'], default='site_admin')
    args = p.parse_args()
    if args.role == 'admin':
        args.role = 'site_admin'

    if os.environ.get('CENTRAL_MODE', '').lower() not in {'1', 'true', 'yes', 'on'}:
        raise SystemExit('Refusing to bootstrap without CENTRAL_MODE=1')
    if not os.environ.get('SECRET_KEY'):
        raise SystemExit('SECRET_KEY is required')
    if args.role != 'hq_admin' and (not args.site_id or not args.site_name):
        raise SystemExit('--site-id and --site-name are required for a site administrator')
    if args.role == 'hq_admin' and args.site_id:
        raise SystemExit('Do not provide --site-id when creating hq_admin')

    # Central bootstrap creates records only; schema is managed by Alembic.
    if engine.dialect.name != 'postgresql':
        raise SystemExit('Central bootstrap is intended for PostgreSQL only.')
    required_tables = {'organizations', 'sites', 'users', 'alembic_version'}
    existing_tables = set(inspect(engine).get_table_names())
    missing_tables = sorted(required_tables - existing_tables)
    if missing_tables:
        raise SystemExit(
            'Database schema is not ready. Apply the approved Alembic migrations first. '
            f'Missing tables: {", ".join(missing_tables)}'
        )
    db = Session()
    try:
        org = db.query(Organization).filter_by(name=args.organization).first()
        if not org:
            org = Organization(name=args.organization)
            db.add(org)
            db.flush()
        else:
            org.is_active = True

        site = None
        if args.role != 'hq_admin':
            site = db.query(Site).filter_by(site_id=args.site_id).first()
            if not site:
                site = Site(site_id=args.site_id, site_name=args.site_name, organization_id=org.id)
                db.add(site)
            else:
                site.site_name = args.site_name
                site.organization_id = org.id
                site.is_active = True

        user = db.query(User).filter_by(username=args.admin).first()
        if not user:
            user = User(username=args.admin, full_name=args.admin_name,
                        password_hash=generate_password_hash(args.password),
                        role=args.role, is_active=True,
                        site_id=None if args.role == 'hq_admin' else args.site_id,
                        organization_id=org.id)
            db.add(user)
        else:
            user.full_name = args.admin_name
            user.site_id = None if args.role == 'hq_admin' else args.site_id
            user.organization_id = org.id
            user.role = args.role
            user.is_active = True
            user.password_hash = generate_password_hash(args.password)

        db.commit()
        if site:
            print(f'Central site ready: {site.site_id} / {site.site_name}')
        print(f'Administrator ready: {args.admin} ({args.role})')
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    main()
