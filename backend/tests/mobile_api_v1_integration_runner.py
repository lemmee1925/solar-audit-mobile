"""Isolated end-to-end test for the native Android v1 API contract.

Run from backend/: python tests/mobile_api_v1_integration_runner.py
This runner creates its own temporary SQLite database and upload directory.
"""
import io
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA_DIR = tempfile.mkdtemp(prefix='solar_mobile_v1_e2e_')
os.environ['SOLAR_AUDIT_DATA_DIR'] = DATA_DIR
os.environ['SECRET_KEY'] = 'isolated-mobile-v1-e2e-session-secret'
os.environ['MOBILE_API_ENABLED'] = '1'
os.environ['MOBILE_API_SIGNING_SECRET'] = 'isolated-mobile-v1-signing-secret-at-least-32-chars'
os.environ.pop('DATABASE_URL', None)
os.environ.pop('CENTRAL_MODE', None)

from PIL import Image
import app as appmod
from models import Session, Plant, AssetType, AuditQuestion, Audit, AuditResponse, AuditResponsePhoto


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print('[PASS]', message)


def jpeg_bytes():
    out = io.BytesIO()
    Image.new('RGB', (64, 64), (45, 110, 160)).save(out, 'JPEG')
    out.seek(0)
    return out


def main():
    appmod.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    client = appmod.app.test_client()

    setup = client.post('/setup', data={
        'site_id': 'MOBILEE2E', 'site_name': 'Mobile E2E', 'username': 'e2e-admin',
        'full_name': 'Mobile E2E Admin', 'password': 'E2E-admin-pass-123'
    })
    check(setup.status_code == 302 and urlparse(setup.headers.get('Location', '')).path == '/login',
          f'isolated site and admin setup (HTTP {setup.status_code}, redirect={setup.headers.get("Location")})')

    login = client.post('/login', data={'username': 'e2e-admin', 'password': 'E2E-admin-pass-123'})
    check(login.status_code == 302 and urlparse(login.headers.get('Location', '')).path == '/dashboard',
          f'web setup login (HTTP {login.status_code}, redirect={login.headers.get("Location")})')

    plant_resp = client.post('/plants', data={'name': 'E2E Plant', 'location': 'Test Site', 'capacity_mw': '2'})
    check(plant_resp.status_code == 302 and urlparse(plant_resp.headers.get('Location', '')).path == '/plants',
          f'create test plant (HTTP {plant_resp.status_code}, redirect={plant_resp.headers.get("Location")})')

    db = Session()
    try:
        plant = db.query(Plant).filter_by(name='E2E Plant').first()

        # Do not assume the first AssetType row has an active checklist.
        # Older databases can contain empty/retired types before newly seeded types.
        pair = (
            db.query(AssetType, AuditQuestion)
            .join(AuditQuestion, AuditQuestion.asset_type_id == AssetType.id)
            .filter(AuditQuestion.is_active.is_(True))
            .order_by(AssetType.id.asc(), AuditQuestion.question_order.asc(), AuditQuestion.id.asc())
            .first()
        )
        asset_type, question = pair if pair else (None, None)

        if not plant or not asset_type or not question:
            available = (
                db.query(AssetType.name, AuditQuestion.id, AuditQuestion.is_active)
                .outerjoin(AuditQuestion, AuditQuestion.asset_type_id == AssetType.id)
                .limit(20)
                .all()
            )
            print('[DEBUG] Fixture state:', {'plant_found': bool(plant), 'asset_type_found': bool(asset_type), 'question_found': bool(question)})
            print('[DEBUG] Available asset type/question rows:', available)
        check(bool(plant and asset_type and question),
              'seeded plant and an asset type with an active checklist question available')
        plant_id, asset_type_id, question_id = str(plant.id), int(asset_type.id), int(question.id)
        print(f'[INFO] Using checklist: {asset_type.name} / question #{question.id}')
    finally:
        db.close()

    token_response = client.post('/api/mobile/v1/auth/token', json={
        'username': 'e2e-admin', 'password': 'E2E-admin-pass-123'
    })
    check(token_response.status_code == 200 and token_response.get_json().get('access_token'), 'native bearer-token login')
    token = token_response.get_json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}

    bootstrap = client.get('/api/mobile/v1/bootstrap', headers=headers)
    check(bootstrap.status_code == 200 and bootstrap.get_json().get('ok'), 'Android bootstrap/reference snapshot')
    site_id = token_response.get_json()['site']['id']
    reference = client.get(f'/api/mobile/v1/sites/{site_id}/reference-data', headers=headers)
    check(reference.status_code == 200 and reference.get_json().get('complete'), 'site reference-data download')

    audit_id = 'a1111111-1111-4111-8111-111111111111'
    asset_ref = 'local-asset-e2e-1'
    manifest = {
        'local_uuid': audit_id,
        'plant_id': plant_id,
        'plant_name': 'E2E Plant',
        'audit_date': '2026-11-05',
        'audit_time': '10:00',
        'audit_type': 'O&M',
        'auditor_name': 'E2E Auditor',
        'auditee_name': 'E2E Site Representative',
        'assets': [{
            'asset_ref': asset_ref, 'asset_type_id': asset_type_id,
            'asset_name': 'E2E Inverter 01', 'location': 'Block A',
            'rank': '5', 'is_audited': True,
        }],
        'responses': [{
            'asset_ref': asset_ref, 'question_id': question_id,
            'response_status': 'pass', 'remarks': 'Native API integration test',
        }],
    }
    manifest_resp = client.post(f'/api/mobile/v1/audits/{audit_id}/manifest', json=manifest, headers=headers)
    check(manifest_resp.status_code == 200 and manifest_resp.get_json().get('ok'), 'versioned API audit manifest upload')
    asset_id = manifest_resp.get_json()['asset_map'][asset_ref]

    photo_id = 'b2222222-2222-4222-8222-222222222222'
    photo_fields = {
        'local_uuid': photo_id, 'audit_local_uuid': audit_id,
        'asset_ref': asset_ref, 'asset_id': asset_id,
        'question_id': str(question_id),
        'latitude': '18.5204', 'longitude': '73.8567',
        'gps_accuracy': '5', 'captured_at': '2026-11-05T10:05:00Z',
    }
    photo_resp = client.post(
        f'/api/mobile/v1/audits/{audit_id}/photos/{photo_id}',
        data={**photo_fields, 'file': (jpeg_bytes(), 'e2e.jpg')},
        headers=headers, content_type='multipart/form-data'
    )
    check(photo_resp.status_code == 200 and photo_resp.get_json().get('ok'), 'versioned API photo upload')

    duplicate_resp = client.post(
        f'/api/mobile/v1/audits/{audit_id}/photos/{photo_id}',
        data={**photo_fields, 'file': (jpeg_bytes(), 'e2e.jpg')},
        headers=headers, content_type='multipart/form-data'
    )
    check(duplicate_resp.status_code == 200 and duplicate_resp.get_json().get('duplicate') is True,
          'photo retry is idempotent')

    manifest_retry = client.post(f'/api/mobile/v1/audits/{audit_id}/manifest', json=manifest, headers=headers)
    check(manifest_retry.status_code == 200, 'manifest retry accepted')

    complete = client.post(
        f'/api/mobile/v1/audits/{audit_id}/complete',
        json={'audit_local_uuid': audit_id}, headers=headers
    )
    check(complete.status_code == 200 and complete.get_json().get('status') == 'submitted',
          'versioned API audit completion')

    status = client.get('/api/mobile/v1/audits/status', headers=headers)
    rows = status.get_json().get('audits', []) if status.status_code == 200 else []
    check(any(row.get('id') == audit_id and row.get('status') == 'submitted' for row in rows),
          'submitted audit visible in status refresh')

    observations = client.get(f'/api/mobile/v1/observations?audit_id={audit_id}', headers=headers)
    check(observations.status_code == 200 and isinstance(observations.get_json().get('observations'), list),
          'observation endpoint responds for synced audit')

    db = Session()
    try:
        check(db.query(Audit).filter_by(id=audit_id).count() == 1, 'manifest retry did not duplicate audit')
        response = db.query(AuditResponse).filter_by(audit_id=audit_id, question_id=question_id).one()
        check(db.query(AuditResponsePhoto).filter_by(audit_response_id=response.id).count() == 1,
              'photo retry did not duplicate evidence record')
    finally:
        db.close()

    print('\nNATIVE MOBILE API V1 END-TO-END TEST PASSED')
    print('Isolated test data:', DATA_DIR)


if __name__ == '__main__':
    main()
