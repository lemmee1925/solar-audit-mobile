"""Targeted tests for the offline-first audit/photo synchronization API.

Run from backend/ with:
    python -m pytest tests/offline_sync_test.py -v
"""
import io
import os
import re
import sys
import tempfile

import pytest
from PIL import Image

_TMP = tempfile.mkdtemp(prefix='solar_audit_offline_sync_')
os.environ['SOLAR_AUDIT_DATA_DIR'] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from models import Session, Plant, AssetType, Audit, AuditResponse, AuditResponsePhoto  # noqa: E402


def jpeg():
    b = io.BytesIO()
    Image.new('RGB', (320, 240), (40, 120, 80)).save(b, 'JPEG')
    b.seek(0)
    return b


def test_offline_audit_photo_sync_is_idempotent_and_independent():
    appmod.app.config['TESTING'] = True
    c = appmod.app.test_client()
    c.post('/setup', data={
        'site_id': 'PUNE01', 'site_name': 'Pune', 'username': 'admin',
        'full_name': 'Site Admin', 'password': 'adminpass123'
    })
    c.post('/login', data={'username': 'admin', 'password': 'adminpass123'})

    c.post('/plants', data={'name': 'Plant A', 'location': 'X', 'capacity_mw': '2'})
    s = Session()
    plant = s.query(Plant).filter_by(name='Plant A').first()
    asset_type = s.query(AssetType).first()
    s.close()
    assert plant and asset_type

    # Choose a real checklist question for this asset type.
    s = Session()
    from models import AuditQuestion
    q = s.query(AuditQuestion).filter_by(asset_type_id=asset_type.id, is_active=True).first()
    s.close()
    assert q

    audit_uuid = '11111111-1111-4111-8111-111111111111'
    asset_ref = 'local-asset-1'
    payload = {
        'local_uuid': audit_uuid,
        'plant_id': str(plant.id),
        'plant_name': plant.name,
        'audit_date': '2026-10-06',
        'audit_time': '12:00',
        'audit_type': 'O&M',
        'auditor_name': 'Offline Auditor',
        'assets': [{
            'asset_ref': asset_ref,
            'asset_type_id': asset_type.id,
            'asset_name': 'Inverter 01',
            'location': 'Block A',
            'rank': '5',
            'is_audited': True,
        }],
        'responses': [{
            'asset_ref': asset_ref,
            'question_id': q.id,
            'response_status': 'pass',
            'remarks': 'Offline response',
        }],
    }

    r = c.post('/api/offline/sync/audit', json=payload)
    assert r.status_code == 200, r.get_json()
    asset_map = r.get_json()['asset_map']
    assert asset_ref in asset_map

    photo_uuid = '22222222-2222-4222-8222-222222222222'
    data = {
        'local_uuid': photo_uuid,
        'audit_local_uuid': audit_uuid,
        'asset_ref': asset_ref,
        'asset_id': asset_map[asset_ref],
        'question_id': str(q.id),
        'latitude': '18.5204',
        'longitude': '73.8567',
        'gps_accuracy': '5',
        'captured_at': '2026-10-06T12:00:00Z',
    }
    r = c.post('/api/offline/sync/photo', data={**data, 'file': (jpeg(), 'photo.jpg')},
               content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['ok']

    # Same client UUID/path is a retry, not a second server photo.
    r = c.post('/api/offline/sync/photo', data={**data, 'file': (jpeg(), 'photo.jpg')},
               content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('duplicate') is True

    s = Session()
    try:
        audit = s.query(Audit).filter_by(id=audit_uuid).one()
        response = s.query(AuditResponse).filter_by(audit_id=audit_uuid, question_id=q.id).one()
        assert s.query(AuditResponsePhoto).filter_by(audit_response_id=response.id).count() == 1
    finally:
        s.close()

    # Repeating the audit request is also idempotent.
    r = c.post('/api/offline/sync/audit', json=payload)
    assert r.status_code == 200, r.get_json()
    s = Session()
    try:
        assert s.query(Audit).filter_by(id=audit_uuid).count() == 1
    finally:
        s.close()


def test_offline_photo_rejects_missing_multipart_file():
    # This route-level assertion specifically guards the bug where the old
    # frontend sent a data URL as a normal form value instead of a Blob/File.
    appmod.app.config['TESTING'] = True
    c = appmod.app.test_client()
    c.post('/login', data={'username': 'admin', 'password': 'adminpass123'})
    r = c.post('/api/offline/sync/photo', data={
        'local_uuid': '33333333-3333-4333-8333-333333333333',
        'audit_local_uuid': '11111111-1111-4111-8111-111111111111',
        'asset_ref': 'Site Safety',
        'question_id': '999999',
    })
    assert r.status_code in (400, 404)
