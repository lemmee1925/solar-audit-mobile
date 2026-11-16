"""Native mobile bearer-token API tests. Run from backend/ with pytest."""
import os
import tempfile
from pathlib import Path

_TEST_DATA = tempfile.mkdtemp(prefix='solar_audit_mobile_api_')
os.environ['SOLAR_AUDIT_DATA_DIR'] = _TEST_DATA
os.environ['CENTRAL_MODE'] = '1'
os.environ['SECRET_KEY'] = 'mobile-api-test-secret-not-for-production'
os.environ['CSRF_ENABLED'] = '1'

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from werkzeug.security import generate_password_hash
import app as appmod
from models import Session, Organization, Site, User, MobileAPIToken


def test_mobile_bearer_login_ping_logout_and_hq_restriction():
    appmod.app.config.update(TESTING=True, CSRF_ENABLED=True)
    db = Session()
    try:
        org = Organization(name='Mobile API Test Org')
        db.add(org)
        db.flush()
        site = Site(site_id='MOB001', site_name='Mobile Test Site', organization_id=org.id, is_active=True)
        db.add(site)
        db.flush()
        auditor = User(username='mobile_auditor', full_name='Mobile Auditor',
                       password_hash=generate_password_hash('StrongPass123!'),
                       role='site_admin', is_active=True, site_id='MOB001')
        hq = User(username='mobile_hq', full_name='HQ User',
                  password_hash=generate_password_hash('StrongPass123!'),
                  role='hq_admin', is_active=True, site_id=None)
        db.add_all([auditor, hq])
        db.commit()
    finally:
        db.close()

    client = appmod.app.test_client()
    bad = client.post('/api/mobile/login', json={
        'username': 'mobile_auditor', 'password': 'wrong-password'
    })
    assert bad.status_code == 401

    denied = client.post('/api/mobile/login', json={
        'username': 'mobile_hq', 'password': 'StrongPass123!'
    })
    assert denied.status_code == 403

    response = client.post('/api/mobile/login', json={
        'username': 'mobile_auditor', 'password': 'StrongPass123!',
        'device_name': 'QA Android'
    })
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['ok'] is True
    assert body['token_type'] == 'Bearer'
    assert body['user']['site_id'] == 'MOB001'
    token = body['access_token']

    db = Session()
    try:
        stored = db.query(MobileAPIToken).one()
        assert stored.token_hash != token
        assert len(stored.token_hash) == 64
        assert stored.device_name == 'QA Android'
    finally:
        db.close()

    ping = client.get('/api/offline/ping', headers={'Authorization': f'Bearer {token}'})
    assert ping.status_code == 200, ping.get_json()
    assert ping.get_json()['site_id'] == 'MOB001'

    logout = client.post('/api/mobile/logout', headers={'Authorization': f'Bearer {token}'})
    assert logout.status_code == 200, logout.get_json()
    after_logout = client.get('/api/offline/ping', headers={'Authorization': f'Bearer {token}'})
    assert after_logout.status_code == 401
