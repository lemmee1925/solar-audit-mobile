"""HQ/global administrator authorization regression test.

Uses a temporary SQLite database in CENTRAL_MODE and verifies:
- hq_admin has site_id=NULL
- HQ can view the global site portfolio
- HQ can switch between active sites
- HQ can access data in both sites
- site_admin remains locked to its own site
- site_admin cannot use HQ controls
"""
import os
import shutil
import tempfile
import uuid
from pathlib import Path

DATA_DIR = Path(tempfile.mkdtemp(prefix="solaraudit-hq-admin-"))
os.environ["SOLAR_AUDIT_DATA_DIR"] = str(DATA_DIR)
os.environ["CENTRAL_MODE"] = "1"
os.environ["SECRET_KEY"] = "hq-admin-test-secret"
os.environ["CSRF_ENABLED"] = "0"

from werkzeug.security import generate_password_hash
from models import Session, Organization, Site, User, Plant
from app import app


def check(label, condition):
    print(f"[{'OK ' if condition else 'FAIL'}] {label}")
    if not condition:
        raise AssertionError(label)


def login(client, username, password):
    r = client.post('/login', data={'username': username, 'password': password}, follow_redirects=False)
    check(f'{username} login succeeds', r.status_code == 302)
    return r


def main():
    try:
        app.config.update(TESTING=True, CSRF_ENABLED=False)
        db = Session()
        try:
            org = Organization(name='HQ Test Org')
            db.add(org)
            db.flush()
            site_a = Site(site_id='HQ001', site_name='HQ Site A', organization_id=org.id)
            site_b = Site(site_id='HQ002', site_name='HQ Site B', organization_id=org.id)
            db.add_all([site_a, site_b])
            db.flush()
            hq = User(username='hq', full_name='HQ', password_hash=generate_password_hash('hq-pass'), role='hq_admin', is_active=True, site_id=None)
            admin_a = User(username='sitea', full_name='Site A Admin', password_hash=generate_password_hash('site-pass'), role='site_admin', is_active=True, site_id='HQ001')
            plant_a = Plant(name='Plant A', location='Pune', capacity_mw=5, site_id='HQ001')
            plant_b = Plant(name='Plant B', location='Bikaner', capacity_mw=10, site_id='HQ002')
            db.add_all([hq, admin_a, plant_a, plant_b])
            db.commit()
            hq_id = str(hq.id)
            admin_id = str(admin_a.id)
        finally:
            db.close()

        hq_client = app.test_client()
        site_client = app.test_client()
        login(hq_client, 'hq', 'hq-pass')
        login(site_client, 'sitea', 'site-pass')

        db = Session()
        try:
            hq_row = db.query(User).filter_by(id=hq_id).one()
            check('HQ admin has NULL site_id', hq_row.site_id is None and hq_row.role == 'hq_admin')
            admin_row = db.query(User).filter_by(id=admin_id).one()
            check('Site admin remains site-scoped', admin_row.site_id == 'HQ001')
        finally:
            db.close()

        r = hq_client.get('/hq')
        check('HQ sees global site portfolio', r.status_code == 200 and b'HQ Site A' in r.data and b'HQ Site B' in r.data)
        r = hq_client.get('/switch-site/HQ001')
        check('HQ can switch to Site A', r.status_code == 302)
        r = hq_client.get('/plants')
        check('HQ can access Site A plants', r.status_code == 200 and b'Plant A' in r.data)
        check('HQ Site A view does not expose Site B plant', b'Plant B' not in r.data)

        r = hq_client.get('/switch-site/HQ002')
        check('HQ can switch to Site B', r.status_code == 302)
        r = hq_client.get('/plants')
        check('HQ can access Site B plants', r.status_code == 200 and b'Plant B' in r.data)

        r = site_client.get('/hq')
        check('Site admin cannot open HQ overview', r.status_code in (302, 403))
        r = site_client.get('/switch-site/HQ002')
        check('Site admin cannot switch sites', r.status_code == 302)
        r = site_client.get('/plants')
        check('Site admin stays on Site A', r.status_code == 200 and b'Plant A' in r.data and b'Plant B' not in r.data)

        print('\nHQ ADMIN ROLE TEST PASSED')
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)


if __name__ == '__main__':
    main()
