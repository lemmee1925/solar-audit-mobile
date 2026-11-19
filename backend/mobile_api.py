"""Versioned native API adapter for the existing Flask Solar Audit database.

Disabled by default. Enable only after setting MOBILE_API_ENABLED=1 and a
random MOBILE_API_SIGNING_SECRET of at least 32 characters. This adapter is
intentionally scoped to this install's configured site; the current data model
is not a central multi-site directory.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, g, session as flask_session
from flask_login import login_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

from models import (Session, User, Site, Plant, AssetType, Asset, AuditQuestion,
                    AuditSection, UserPlantAssignment, Audit, Observation,
                    ObservationPhoto, ObservationEvent, AuditResponse,
                    AuditResponsePhoto, MobileAPIToken)
from site_config import load_site_config
from site_scope import (
    assigned_plant_ids, load_scoring_config, SEVERITY_LABELS, STATUS_LABELS,
    WORKING, WorkflowError, is_overdue, age_days, display_status, obs_log,
    start_work, save_action, submit_for_review, review_observation,
    reassign_observation, can_access_plant,
)
from version import APP_VERSION, EXPORT_SCHEMA_VERSION
from role_policy import is_admin, canonical_role, is_hq_admin
from central_access import resolve_site_context, CentralAccessError

mobile_api = Blueprint('mobile_api_v1', __name__, url_prefix='/api/mobile/v1')
TOKEN_MAX_AGE_SECONDS = 8 * 60 * 60
TOKEN_SALT = 'solar-audit-mobile-access-v1'
_LEGACY_SYNC_HANDLERS = {}
_OBSERVATION_PHOTO_STORE = None

def configure_legacy_sync_handlers(audit_handler, photo_handler, complete_handler):
    """Register the existing PWA sync business-rule handlers.

    Native routes delegate to the same handlers instead of duplicating audit
    creation, photo persistence, observation validation, or scoring logic.
    """
    _LEGACY_SYNC_HANDLERS.update({
        'audit': audit_handler, 'photo': photo_handler, 'complete': complete_handler,
    })

def configure_observation_photo_store(store_photo_handler):
    """Reuse the existing backend photo-storage implementation for closure evidence."""
    global _OBSERVATION_PHOTO_STORE
    _OBSERVATION_PHOTO_STORE = store_photo_handler


def _call_legacy_handler(name):
    handler = _LEGACY_SYNC_HANDLERS.get(name)
    if handler is None:
        return _error('Native audit sync is not configured on this server.', 503)
    # The bearer token has already been verified and the user is site-scoped.
    # Establish Flask-Login's request-local user, then invoke the underlying
    # existing handler without its cookie-only login_required wrapper.
    user = g.mobile_db.query(User).filter_by(id=g.mobile_user_id, site_id=g.mobile_site.site_id).first()
    if not user or not user.is_active:
        return _error('User is unavailable or access has been revoked.', 401)
    login_user(user, remember=False, fresh=True)
    try:
        underlying = getattr(handler, '__wrapped__', None)
        if underlying is None:
            return _error('Legacy sync handler cannot be safely delegated.', 503)
        return underlying()
    finally:
        # Do not turn a bearer-token request into a persistent cookie session.
        flask_session.clear()


def _central_mode():
    return os.environ.get('CENTRAL_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')


def _enabled():
    return os.environ.get('MOBILE_API_ENABLED', '').strip().lower() in ('1', 'true', 'yes')


def _serializer():
    secret = os.environ.get('MOBILE_API_SIGNING_SECRET', '')
    if len(secret) < 32:
        return None
    return URLSafeTimedSerializer(secret_key=secret, salt=TOKEN_SALT)


def _error(message, status):
    return jsonify({'ok': False, 'error': message}), status


def _bearer_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not _enabled():
            return _error('Native API is disabled by server configuration.', 503)
        serializer = _serializer()
        if serializer is None:
            current_app.logger.error('Mobile API enabled without a valid signing secret')
            return _error('Native API authentication is not configured.', 503)
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return _error('A bearer access token is required.', 401)
        token = header[7:].strip()
        try:
            claims = serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        except SignatureExpired:
            return _error('Access token expired. Sign in again.', 401)
        except BadSignature:
            return _error('Invalid access token.', 401)
        db = Session()
        try:
            if _central_mode():
                site_id = claims.get('sid')
                user = db.query(User).filter_by(id=claims.get('sub'), site_id=site_id).first()
                if not user or not user.is_active or is_hq_admin(user):
                    return _error('User is unavailable or native access is not permitted.', 401)
                try:
                    site = resolve_site_context(db, user)
                except CentralAccessError:
                    return _error('Token site is no longer authorized for this user.', 401)
                if site.site_id != site_id:
                    return _error('Token site is no longer valid.', 401)
            else:
                configured_site = load_site_config()
                if not configured_site or claims.get('sid') != configured_site.site_id:
                    return _error('Token site is no longer valid for this installation.', 401)
                user = db.query(User).filter_by(id=claims.get('sub'), site_id=configured_site.site_id).first()
                if not user or not user.is_active:
                    return _error('User is unavailable or access has been revoked.', 401)
                site = configured_site
            token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            token_row = db.query(MobileAPIToken).filter_by(token_hash=token_hash).first()
            if token_row and (token_row.revoked_at is not None or token_row.expires_at <= now_utc):
                return _error('Access token has been revoked or expired. Sign in again.', 401)
            g.mobile_token_hash = token_hash
            g.mobile_user_id = str(user.id)
            g.mobile_user_role = user.role
            g.mobile_username = user.username
            g.mobile_full_name = user.full_name
            g.mobile_site = site
            # Keep the DB session alive only for this request.
            g.mobile_db = db
            response = fn(*args, **kwargs)
            return response
        finally:
            db.close()
    return wrapped


@mobile_api.teardown_request
def _close_mobile_db(_exc):
    db = getattr(g, 'mobile_db', None)
    if db is not None:
        # close() is idempotent; the decorator also closes on early return.
        try:
            db.close()
        except Exception:
            pass


@mobile_api.post('/auth/token')
def issue_token():
    """Exchange existing site-scoped username/password for a short-lived token."""
    if not _enabled():
        return _error('Native API is disabled by server configuration.', 503)

    serializer = _serializer()
    if serializer is None:
        return _error('Native API authentication is not configured.', 503)

    central_mode = _central_mode()
    site = None if central_mode else load_site_config()
    if not central_mode and not site:
        return _error('This installation has not been configured.', 503)

    payload = request.get_json(silent=True) or {}
    username = str(payload.get('username', '')).strip()
    password = payload.get('password', '')

    if not username or not isinstance(password, str) or not password:
        return _error('Username and password are required.', 400)

    from rate_limit import is_rate_limited, record_failure, clear_failures
    rate_key = f"mobile:{request.remote_addr or 'unknown'}:{username.lower()}"
    limited, retry_after = is_rate_limited(rate_key)
    if limited:
        return _error(f'Too many failed sign-in attempts. Retry in {retry_after} seconds.', 429)

    db = Session()
    try:
        if central_mode:
            user = db.query(User).filter_by(username=username).first()
            if user and (is_hq_admin(user) or not user.site_id):
                user = None
            if user:
                try:
                    site = resolve_site_context(db, user)
                except CentralAccessError:
                    user = None
        else:
            user = db.query(User).filter_by(username=username, site_id=site.site_id).first()

        password_matches = bool(user and check_password_hash(user.password_hash, password))

        if not user or not user.is_active or not password_matches:
            record_failure(rate_key)
            return _error('Invalid username or password.', 401)
        clear_failures(rate_key)

        token = serializer.dumps({
            'sub': str(user.id),
            'sid': site.site_id,
            'ver': 1
        })
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        device_name = str(payload.get('device_name') or request.headers.get('User-Agent') or 'Android device')[:120]
        db.add(MobileAPIToken(
            user_id=str(user.id),
            token_hash=token_hash,
            device_name=device_name,
            expires_at=now_utc + timedelta(seconds=TOKEN_MAX_AGE_SECONDS),
        ))
        db.commit()

        return jsonify({
            'ok': True,
            'access_token': token,
            'token_type': 'Bearer',
            'expires_in': TOKEN_MAX_AGE_SECONDS,
            'user': {
                'id': str(user.id),
                'username': user.username,
                'full_name': user.full_name,
                'role': user.role
            },
            'site': {
                'id': site.site_id,
                'name': site.site_name
            }
        })
    finally:
        db.close()


@mobile_api.post('/auth/logout')
@_bearer_required
def revoke_token():
    """Revoke the current mobile bearer token immediately."""
    db = g.mobile_db
    token_hash = getattr(g, 'mobile_token_hash', None)
    if token_hash:
        row = db.query(MobileAPIToken).filter_by(token_hash=token_hash).first()
        if row and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    return jsonify({'ok': True})

@mobile_api.get('/bootstrap')
@_bearer_required
def bootstrap():
    db = g.mobile_db
    site = g.mobile_site
    user = db.query(User).filter_by(id=g.mobile_user_id, site_id=site.site_id).first()
    allowed_ids = assigned_plant_ids(db, user)
    plant_query = db.query(Plant).filter_by(site_id=site.site_id).order_by(Plant.name)
    if allowed_ids is not None:
        plant_query = plant_query.filter(Plant.id.in_(allowed_ids))
    plants = [{'id': str(p.id), 'name': p.name, 'location': p.location,
               'capacity_mw': p.capacity_mw} for p in plant_query.all()]
    reference = _build_reference_data(db, site.site_id, plants, allowed_ids)
    canonical = json.dumps(reference, sort_keys=True, separators=(',', ':'), default=str)
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return jsonify({
        'ok': True, 'api_version': 'v1', 'schema_version': EXPORT_SCHEMA_VERSION,
        'application_version': APP_VERSION, 'server_time': datetime.now(timezone.utc).isoformat(),
        'authorization_checked_at': datetime.now(timezone.utc).isoformat(),
        'user': {'id': g.mobile_user_id, 'username': g.mobile_username,
                 'full_name': g.mobile_full_name, 'role': g.mobile_user_role},
        # Current Flask data model represents one configured site per installation.
        'sites': [{'id': site.site_id, 'name': site.site_name,
                   'plants': plants, 'reference_version': digest}],
        'reference_version': digest,
        'capabilities': {'reference_data_download': True, 'audit_sync': True,
                         'photo_upload': True, 'server_completion': True, 'observation_sync': True,
                         'sync_payload_contract': 'existing-pwa-v1'},
    })


@mobile_api.post('/plants')
@_bearer_required
def create_plant():
    """Create a plant using the same site-scoped master record as the PWA.

    The PWA's plant-creation screen is admin-only; the native API enforces the
    same rule and never trusts a client-supplied site ID.
    """
    user = g.mobile_db.query(User).filter_by(
        id=g.mobile_user_id, site_id=g.mobile_site.site_id
    ).first()
    if not user or not user.is_active:
        return _error('User is unavailable or access has been revoked.', 401)
    if not is_admin(user):
        return _error('Only site administrators can create plants.', 403)

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    location = str(payload.get('location') or '').strip()
    raw_capacity = payload.get('capacity_mw')
    if not name:
        return _error('Plant name is required.', 400)
    if len(name) > 200 or len(location) > 200:
        return _error('Plant name and location must be 200 characters or fewer.', 400)
    capacity = None
    if raw_capacity not in (None, ''):
        try:
            capacity = float(raw_capacity)
        except (TypeError, ValueError):
            return _error('Capacity must be a valid number.', 400)
        if capacity < 0 or capacity != capacity or capacity == float('inf'):
            return _error('Capacity must be a finite non-negative number.', 400)

    plant = Plant(name=name, location=location, capacity_mw=capacity,
                  site_id=g.mobile_site.site_id)
    db = g.mobile_db
    db.add(plant)
    try:
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Native plant creation failed')
        return _error('Plant could not be saved. No changes were applied.', 500)
    return jsonify({'ok': True, 'plant': {
        'id': str(plant.id), 'name': plant.name, 'location': plant.location,
        'capacity_mw': plant.capacity_mw, 'site_id': plant.site_id,
    }}), 201


@mobile_api.get('/sites/<site_id>/reference-data')
@_bearer_required
def site_reference_data(site_id):
    site = g.mobile_site
    if site_id != site.site_id:
        return _error('Site is not available to this user on this installation.', 404)
    db = g.mobile_db
    user = db.query(User).filter_by(id=g.mobile_user_id, site_id=site.site_id).first()
    allowed_ids = assigned_plant_ids(db, user)
    plant_query = db.query(Plant).filter_by(site_id=site.site_id).order_by(Plant.name)
    if allowed_ids is not None:
        plant_query = plant_query.filter(Plant.id.in_(allowed_ids))
    plants = [{'id': str(p.id), 'name': p.name, 'location': p.location,
               'capacity_mw': p.capacity_mw} for p in plant_query.all()]
    data = _build_reference_data(db, site.site_id, plants, allowed_ids)
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'), default=str)
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    etag = '"' + digest + '"'
    if request.headers.get('If-None-Match') == etag:
        return current_app.response_class(status=304, headers={'ETag': etag})
    return jsonify({'ok': True, 'complete': True, 'site_id': site.site_id,
                    'schema_version': EXPORT_SCHEMA_VERSION,
                    'reference_version': digest, 'reference_hash': digest,
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                    'data': data}), 200, {'ETag': etag, 'Cache-Control': 'private, no-cache'}




# Plant master specifications mirror the existing /plants/<id>/specs web workflow.
_PLANT_SPEC_INT_FIELDS = [
    'no_of_blocks', 'central_inverter_count', 'string_inverter_count', 'scb_count',
    'mms_count', 'transformer_count', 'clamps_connectors_count', 'lt_panel_count',
    'ht_panel_count', 'acdb_count', 'ups_db_battery_count', 'lightning_protection_count',
]
_PLANT_SPEC_FLOAT_FIELDS = ['dc_capacity', 'ac_capacity']
_PLANT_SPEC_TEXT_FIELDS = [
    'building_name', 'roof_type', 'city', 'state', 'central_inverter_type',
    'string_inverter_type', 'mms_type', 'transformer_type', 'lt_panel_type',
    'ht_panel_type', 'dc_earthing', 'ac_earthing',
]

def _mobile_admin_required():
    return is_admin(getattr(g, 'mobile_user_role', ''))

def _plant_for_mobile(db, plant_id):
    user = db.query(User).filter_by(id=g.mobile_user_id, site_id=g.mobile_site.site_id).first()
    allowed_ids = assigned_plant_ids(db, user)
    query = db.query(Plant).filter_by(id=str(plant_id), site_id=g.mobile_site.site_id)
    if allowed_ids is not None:
        query = query.filter(Plant.id.in_(allowed_ids))
    return query.first()

def _plant_spec_payload(plant):
    fields = ['id', 'name', 'location', 'capacity_mw'] + _PLANT_SPEC_TEXT_FIELDS + _PLANT_SPEC_INT_FIELDS + _PLANT_SPEC_FLOAT_FIELDS
    return {field: getattr(plant, field, None) for field in fields}

@mobile_api.get('/plants/<plant_id>/specs')
@_bearer_required
def mobile_get_plant_specs(plant_id):
    plant = _plant_for_mobile(g.mobile_db, plant_id)
    if not plant:
        return _error('Plant not found or not assigned to this user.', 404)
    return jsonify({'ok': True, 'plant': _plant_spec_payload(plant)})

@mobile_api.put('/plants/<plant_id>/specs')
@_bearer_required
def mobile_update_plant_specs(plant_id):
    if not _mobile_admin_required():
        return _error('Administrator permission is required to edit plant specifications.', 403)
    db = g.mobile_db
    plant = _plant_for_mobile(db, plant_id)
    if not plant:
        return _error('Plant not found or not assigned to this user.', 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error('A JSON object of plant specifications is required.', 400)
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()
        if not name or len(name) > 200:
            return _error('Plant name is required and must be at most 200 characters.', 400)
        plant.name = name
    if 'location' in payload:
        location = str(payload.get('location') or '').strip()
        if len(location) > 200: return _error('Location must be at most 200 characters.', 400)
        plant.location = location
    if 'capacity_mw' in payload:
        try:
            value = None if payload.get('capacity_mw') in (None, '') else float(payload['capacity_mw'])
            if value is not None and (value < 0 or value != value or value == float('inf')): raise ValueError()
            plant.capacity_mw = value
        except (TypeError, ValueError): return _error('capacity_mw must be a non-negative number.', 400)
    for field in _PLANT_SPEC_TEXT_FIELDS:
        if field in payload:
            value = str(payload.get(field) or '').strip()
            if len(value) > 200: return _error(f'{field} must be at most 200 characters.', 400)
            setattr(plant, field, value or None)
    for field in _PLANT_SPEC_INT_FIELDS:
        if field in payload:
            try:
                raw = payload.get(field)
                value = None if raw in (None, '') else int(raw)
                if value is not None and value < 0: raise ValueError()
                setattr(plant, field, value)
            except (TypeError, ValueError): return _error(f'{field} must be a non-negative integer.', 400)
    for field in _PLANT_SPEC_FLOAT_FIELDS:
        if field in payload:
            try:
                raw = payload.get(field)
                value = None if raw in (None, '') else float(raw)
                if value is not None and (value < 0 or value != value or value == float('inf')): raise ValueError()
                setattr(plant, field, value)
            except (TypeError, ValueError): return _error(f'{field} must be a non-negative number.', 400)
    try:
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Mobile plant specification save failed')
        return _error('Plant specifications could not be saved.', 500)
    return jsonify({'ok': True, 'plant': _plant_spec_payload(plant)})

@mobile_api.post('/plants/<plant_id>/assets')
@_bearer_required
def mobile_create_plant_asset(plant_id):
    if not _mobile_admin_required():
        return _error('Administrator permission is required to change plant equipment.', 403)
    db = g.mobile_db
    plant = _plant_for_mobile(db, plant_id)
    if not plant:
        return _error('Plant not found or not assigned to this user.', 404)
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    type_id = payload.get('asset_type_id')
    if not name or len(name) > 200:
        return _error('Equipment name is required and must be at most 200 characters.', 400)
    try: type_id = int(type_id)
    except (TypeError, ValueError): return _error('A valid backend asset_type_id is required.', 400)
    asset_type = db.query(AssetType).filter_by(id=type_id).first()
    if not asset_type: return _error('Asset type not found. Refresh reference data and try again.', 400)
    asset = Asset(plant_id=str(plant.id), asset_type_id=asset_type.id, asset_name=name,
                  is_active=True, site_id=g.mobile_site.site_id)
    db.add(asset)
    try: db.commit()
    except Exception:
        db.rollback(); current_app.logger.exception('Mobile asset create failed')
        return _error('Equipment could not be saved.', 500)
    return jsonify({'ok': True, 'asset': {'id': str(asset.id), 'plant_id': str(plant.id),
                   'asset_type_id': asset.asset_type_id, 'asset_type_name': asset_type.name,
                   'name': asset.asset_name}}), 201

@mobile_api.patch('/plants/<plant_id>/assets/<asset_id>')
@_bearer_required
def mobile_rename_plant_asset(plant_id, asset_id):
    if not _mobile_admin_required(): return _error('Administrator permission is required to change plant equipment.', 403)
    db = g.mobile_db
    plant = _plant_for_mobile(db, plant_id)
    if not plant: return _error('Plant not found or not assigned to this user.', 404)
    asset = db.query(Asset).filter_by(id=str(asset_id), plant_id=str(plant.id), site_id=g.mobile_site.site_id, is_active=True).first()
    if not asset: return _error('Equipment not found for this plant.', 404)
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name or len(name) > 200: return _error('Equipment name is required and must be at most 200 characters.', 400)
    asset.asset_name = name
    try: db.commit()
    except Exception:
        db.rollback(); current_app.logger.exception('Mobile asset rename failed')
        return _error('Equipment could not be renamed.', 500)
    return jsonify({'ok': True, 'asset': {'id': str(asset.id), 'plant_id': str(plant.id), 'asset_type_id': asset.asset_type_id, 'name': asset.asset_name}})

@mobile_api.delete('/plants/<plant_id>/assets/<asset_id>')
@_bearer_required
def mobile_delete_plant_asset(plant_id, asset_id):
    if not _mobile_admin_required(): return _error('Administrator permission is required to change plant equipment.', 403)
    db = g.mobile_db
    plant = _plant_for_mobile(db, plant_id)
    if not plant: return _error('Plant not found or not assigned to this user.', 404)
    asset = db.query(Asset).filter_by(id=str(asset_id), plant_id=str(plant.id), site_id=g.mobile_site.site_id, is_active=True).first()
    if not asset: return _error('Equipment not found for this plant.', 404)
    # Soft-delete to preserve historical audit references and reports.
    asset.is_active = False
    try: db.commit()
    except Exception:
        db.rollback(); current_app.logger.exception('Mobile asset delete failed')
        return _error('Equipment could not be removed.', 500)
    return jsonify({'ok': True, 'deleted': True, 'asset_id': str(asset.id)})


@mobile_api.get('/audits/status')
@_bearer_required
def audit_status_list():
    """Return status-only metadata for audits in the authenticated installation's site."""
    db = g.mobile_db
    query = db.query(Audit).filter(
        Audit.site_id == g.mobile_site.site_id,
        Audit.is_deleted == False,
    )
    user = db.query(User).filter_by(id=g.mobile_user_id, site_id=g.mobile_site.site_id).first()
    allowed_ids = assigned_plant_ids(db, user)
    if allowed_ids is not None:
        query = query.filter(Audit.plant_id.in_(allowed_ids))
    rows = query.order_by(Audit.updated_at.desc()).all()
    return jsonify({
        'ok': True,
        'audits': [{
            'id': str(a.id), 'status': str(a.status or 'draft'),
            'updated_at': a.updated_at.isoformat() if a.updated_at else None,
            'audit_number': a.audit_number,
        } for a in rows],
    })


@mobile_api.post('/audits/<local_audit_id>/manifest')
@_bearer_required
def audit_manifest(local_audit_id):
    """Idempotently upsert audit data through the existing PWA sync rules.

    Body must use the existing /api/offline/sync/audit payload contract.
    This deliberately does not invent a second mobile-specific audit schema.
    """
    payload = request.get_json(silent=True) or {}
    if str(payload.get('local_uuid') or '') != str(local_audit_id):
        return _error('Path audit ID must match payload local_uuid.', 400)

    # Validate every referenced question and type before calling the legacy
    # sync handler. The legacy PWA endpoint historically skips unknown IDs;
    # native sync must not report success when responses would be silently lost.
    db = g.mobile_db
    responses = payload.get('responses') or []
    assets = payload.get('assets') or []
    asset_type_by_ref = {}
    for item in assets:
        ref = str(item.get('asset_ref') or '').strip()
        try:
            type_id = int(item.get('asset_type_id'))
        except (TypeError, ValueError):
            return _error(f'Invalid asset_type_id for asset reference {ref or "(missing)"}.', 400)
        if not ref or not str(item.get('asset_name') or '').strip():
            return _error('Each audit asset requires asset_ref and asset_name.', 400)
        if not db.query(AssetType).filter_by(id=type_id).first():
            return _error(f'Unknown backend asset type ID {type_id} for {ref}. Refresh reference data and retry.', 400)
        asset_type_by_ref[ref] = type_id

    for index, response in enumerate(responses, start=1):
        try:
            question_id = int(response.get('question_id'))
        except (TypeError, ValueError):
            return _error(f'Checkpoint response {index} has an invalid question_id.', 400)
        question = db.query(AuditQuestion).filter_by(id=question_id).first()
        if not question:
            return _error(f'Checkpoint response {index} references unknown backend question ID {question_id}. Refresh checklist reference data and retry.', 400)
        status = str(response.get('response_status') or '').strip().lower()
        if status not in ('pass', 'fail', 'na', 'ofi'):
            return _error(f'Checkpoint response {index} has unsupported status {status or "(empty)"}.', 400)
        asset_ref = str(response.get('asset_ref') or '').strip()
        if asset_ref not in ('', 'Site Safety', 'Civil / Infrastructure'):
            if asset_ref not in asset_type_by_ref:
                return _error(f'Checkpoint response {index} refers to missing audit asset {asset_ref}.', 400)
            if question.asset_type_id is not None and int(question.asset_type_id) != asset_type_by_ref[asset_ref]:
                return _error(f'Checkpoint response {index} question/type mismatch. Refresh reference data and rebuild the audit checklist.', 400)

    result = _call_legacy_handler('audit')
    # The underlying handler returns a Flask response or (response, status).
    # Do not alter its established response contract; successful preflight
    # means all question/type IDs were resolvable before persistence.
    return result


@mobile_api.post('/audits/<local_audit_id>/photos/<photo_id>')
@_bearer_required
def audit_photo(local_audit_id, photo_id):
    """Upload one photo using the existing PWA photo validation and storage.

    Multipart fields must include local_uuid=photo_id and
    audit_local_uuid=local_audit_id plus the existing question/asset mapping.
    """
    if request.form.get('local_uuid') != photo_id or request.form.get('audit_local_uuid') != local_audit_id:
        return _error('Path IDs must match multipart photo and audit UUIDs.', 400)
    return _call_legacy_handler('photo')


@mobile_api.post('/audits/<local_audit_id>/complete')
@_bearer_required
def audit_complete(local_audit_id):
    """Explicit server completion using the existing scoring and NC checks."""
    payload = request.get_json(silent=True) or {}
    if str(payload.get('audit_local_uuid') or '') != str(local_audit_id):
        return _error('Path audit ID must match payload audit_local_uuid.', 400)
    return _call_legacy_handler('complete')



# ---------------------------------------------------------------------------
# Native observation workflow
# These routes use the same ORM records and workflow helpers as the web UI.
# The audit manifest creates a canonical server observation for each NC or OFI
# checkpoint; the APK links its local checkpoint to that server record.
# ---------------------------------------------------------------------------
def _mobile_observation_context(db, observation_id):
    site = g.mobile_site
    try:
        observation_id = str(uuid.UUID(str(observation_id)))
    except (ValueError, TypeError, AttributeError):
        return None, None, None
    obs = db.query(Observation).filter_by(
        id=observation_id, site_id=site.site_id
    ).first()
    if not obs:
        return None, None, None
    audit = db.query(Audit).filter_by(
        id=obs.audit_id, site_id=site.site_id, is_deleted=False
    ).first()
    if not audit:
        return None, None, None
    user = db.query(User).filter_by(
        id=g.mobile_user_id, site_id=site.site_id
    ).first()
    if not user or not can_access_plant(db, user, audit.plant_id):
        return None, None, None
    return obs, audit, user


def _iso_utc(dt):
    if not dt:
        return None
    s = dt.isoformat()
    if dt.tzinfo is None and not s.endswith('Z'):
        return s + 'Z'
    return s


def _observation_json(db, obs, audit):
    response = db.query(AuditResponse).filter_by(id=obs.audit_response_id, audit_id=audit.id).first()
    question = db.query(AuditQuestion).filter_by(id=response.question_id).first() if response else None
    asset = db.query(Asset).filter_by(id=response.asset_id, site_id=g.mobile_site.site_id).first() if response and response.asset_id else None
    section = db.query(AuditSection).filter_by(id=question.section_id).first() if question and question.section_id else None
    asset_type = db.query(AssetType).filter_by(id=asset.asset_type_id).first() if asset else None
    plant = db.query(Plant).filter_by(id=audit.plant_id, site_id=g.mobile_site.site_id).first()
    closure_photos = db.query(ObservationPhoto).filter_by(
        observation_id=obs.id, kind='closure'
    ).order_by(ObservationPhoto.uploaded_at, ObservationPhoto.id).all()
    events = db.query(ObservationEvent).filter_by(
        observation_id=obs.id
    ).order_by(ObservationEvent.created_at, ObservationEvent.id).all()
    finding_photos = db.query(AuditResponsePhoto).filter_by(
        audit_response_id=obs.audit_response_id
    ).order_by(AuditResponsePhoto.uploaded_at, AuditResponsePhoto.id).all() if response else []
    return {
        'id': str(obs.id), 'audit_id': str(audit.id),
        'audit_number': audit.audit_number, 'audit_status': audit.status,
        'plant_id': str(audit.plant_id) if audit.plant_id else None,
        'plant_name': plant.name if plant else '',
        'equipment_name': asset.asset_name if asset else (section.name if section else 'Site Safety'),
        'equipment_type_name': (asset_type.name if asset_type else (section.name if section else 'Site Safety')),
        'asset_id': str(asset.id) if asset else None,
        'audit_response_id': str(obs.audit_response_id),
        'question_id': question.id if question else None,
        'checkpoint_text': question.question_text if question else '',
        'response_status': response.response_status if response else None,
        'finding_type': ('OFI' if response and (response.response_status or '').lower() == 'ofi' else (obs.finding_type or 'NC')).upper(),
        'ofi_category': (obs.ofi_category or (response.ofi_category if response else '') or ''),
        'observation': obs.observation or '', 'severity': obs.severity or '',
        'responsible_person': obs.responsible_person or '',
        'target_date': obs.target_date or '', 'remarks': obs.remarks or '',
        'action_taken': obs.action_taken or '', 'closure_date': obs.closure_date or '',
        'reviewer_comments': obs.reviewer_comments or '',
        'review_status': obs.review_status or 'pending', 'status': obs.status,
        'status_label': display_status(obs), 'overdue': is_overdue(obs),
        'age_days': age_days(obs),
        'created_at': _iso_utc(obs.created_at),
        'updated_at': _iso_utc(obs.updated_at),
        'reviewed_by': obs.reviewed_by or '',
        'reviewed_at': _iso_utc(obs.reviewed_at),
        'closed_at': _iso_utc(obs.closed_at),
        'closure_photos': [{
            'id': str(p.id), 'kind': p.kind,
            'remark': getattr(p, 'remark', None) or '',
            'captured_at': _iso_utc(p.captured_at),
            'uploaded_at': _iso_utc(p.uploaded_at),
        } for p in closure_photos],
        'finding_photo_count': len(finding_photos),
        'history': [{
            'id': str(e.id), 'event_type': e.event_type,
            'from_status': e.from_status, 'to_status': e.to_status,
            'comment': e.comment or '', 'actor': e.actor or '',
            'created_at': _iso_utc(e.created_at),
        } for e in events],
    }


@mobile_api.get('/observations')
@_bearer_required
def observation_list():
    db = g.mobile_db
    site_id = g.mobile_site.site_id
    user = db.query(User).filter_by(id=g.mobile_user_id, site_id=site_id).first()
    allowed_ids = assigned_plant_ids(db, user)
    query = db.query(Observation, Audit).join(
        Audit, Observation.audit_id == Audit.id
    ).filter(
        Observation.site_id == site_id,
        # Include withdrawn records so the APK can reconcile status changes
        # when a checkpoint previously marked NC is later changed to C/NA.
        Audit.site_id == site_id,
        Audit.is_deleted == False,
    )
    if allowed_ids is not None:
        query = query.filter(Audit.plant_id.in_(allowed_ids))
    requested_audit_id = (request.args.get('audit_id') or '').strip()
    if requested_audit_id:
        query = query.filter(Audit.id == requested_audit_id)
    rows = query.order_by(Observation.updated_at.desc(), Observation.id).all()
    return jsonify({'ok': True, 'observations': [
        _observation_json(db, obs, audit) for obs, audit in rows
    ]})


@mobile_api.post('/observations/<observation_id>/details')
@_bearer_required
def observation_details(observation_id):
    db = g.mobile_db
    obs, audit, user = _mobile_observation_context(db, observation_id)
    if not obs:
        return _error('Observation not found or plant access denied.', 404)
    if obs.status in ('closed', 'withdrawn'):
        return _error('This observation is closed. Reopen it before changing finding details.', 409)
    payload = request.get_json(silent=True) or {}
    severity = str(payload.get('severity') or '').strip().lower()
    if severity and severity not in SEVERITY_LABELS:
        return _error('Choose a valid severity or leave it unclassified.', 400)
    target_date = str(payload.get('target_date') or '').strip()
    if target_date:
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            return _error('Target date must be a valid date.', 400)
    updates = {}
    if 'observation' in payload:
        updates['observation'] = str(payload.get('observation') or '').strip() or None
    if 'remarks' in payload:
        updates['remarks'] = str(payload.get('remarks') or '').strip() or None
    if 'severity' in payload:
        updates['severity'] = severity or None
    if 'responsible_person' in payload:
        updates['responsible_person'] = str(payload.get('responsible_person') or '').strip() or None
    if 'target_date' in payload:
        updates['target_date'] = target_date or None
    changes = []
    for field, value in updates.items():
        old = getattr(obs, field)
        if old != value:
            changes.append(f'{field}: {old or "—"} → {value or "—"}')
            setattr(obs, field, value)
    if changes:
        obs_log(db, obs, 'finding_details_updated', g.mobile_full_name or g.mobile_username, '; '.join(changes))
        db.commit()
    else:
        db.rollback()
    return jsonify({'ok': True, 'observation': _observation_json(db, obs, audit), 'changed': bool(changes)})


@mobile_api.post('/observations/<observation_id>/update')
@_bearer_required
def observation_owner_update(observation_id):
    db = g.mobile_db
    obs, audit, user = _mobile_observation_context(db, observation_id)
    if not obs:
        return _error('Observation not found or plant access denied.', 404)
    payload = request.get_json(silent=True) or {}
    action = str(payload.get('do') or 'save').strip().lower()
    if action not in ('start', 'save', 'submit'):
        return _error('Unknown observation action.', 400)
    actor = g.mobile_full_name or g.mobile_username
    try:
        if obs.status not in WORKING:
            return _error(f'Observation is not open for updates (status: {STATUS_LABELS.get(obs.status, obs.status)}).', 409)
        if action == 'start' and obs.status != 'in_progress':
            start_work(db, obs, actor)
        if action in ('save', 'submit') or 'action_taken' in payload or 'closure_date' in payload:
            if 'action_taken' in payload or 'closure_date' in payload:
                save_action(db, obs, actor, payload.get('action_taken'), payload.get('closure_date'))
        db.commit()
        if action == 'submit':
            submit_for_review(db, obs, actor)
            db.commit()
    except WorkflowError as exc:
        db.rollback()
        return _error(str(exc), 409)
    return jsonify({'ok': True, 'observation': _observation_json(db, obs, audit)})


@mobile_api.post('/observations/<observation_id>/closure-photos/<photo_id>')
@_bearer_required
def observation_closure_photo(observation_id, photo_id):
    db = g.mobile_db
    obs, audit, _user = _mobile_observation_context(db, observation_id)
    if not obs:
        return _error('Observation not found or plant access denied.', 404)
    if obs.status not in WORKING:
        return _error('Closure evidence is locked once submitted for review.', 409)
    if not photo_id or len(photo_id) > 64 or not all(c.isalnum() or c == '-' for c in photo_id):
        return _error('Invalid closure photo ID.', 400)
    prefix = f'closure_{photo_id.replace("-", "")}'
    existing = db.query(ObservationPhoto).filter(
        ObservationPhoto.observation_id == obs.id,
        ObservationPhoto.kind == 'closure',
        ObservationPhoto.file_path.like(f'%/{prefix}_%'),
    ).first()
    if existing:
        return jsonify({'ok': True, 'photo_id': str(existing.id), 'duplicate': True})
    if _OBSERVATION_PHOTO_STORE is None:
        return _error('Observation photo storage is not configured on this server.', 503)
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return _error('A closure evidence photo is required.', 400)
    current_count = db.query(ObservationPhoto).filter_by(observation_id=obs.id, kind='closure').count()
    if current_count >= 8:
        return _error('A maximum of 8 closure evidence photos is allowed.', 409)
    try:
        info = _OBSERVATION_PHOTO_STORE(
            uploaded,
            f'{str(audit.id).replace("-", "")[:12]}/o{str(obs.id).replace("-", "")[:10]}',
            prefix, request.form, photo_id,
        )
        if not info:
            return _error('Photo could not be stored. Check the image format and 50 MB size limit.', 400)
        actor = g.mobile_full_name or g.mobile_username
        photo = ObservationPhoto(observation_id=obs.id, kind='closure', uploaded_by=actor, **info)
        db.add(photo)
        obs_log(db, obs, 'closure_evidence_added', actor, '1 photo added')
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Native observation closure photo upload failed')
        return _error('Photo upload failed. Retry safely; the observation was not submitted.', 500)
    return jsonify({'ok': True, 'photo_id': str(photo.id), 'duplicate': False})


@mobile_api.post('/observations/<observation_id>/review')
@_bearer_required
def observation_review_native(observation_id):
    db = g.mobile_db
    obs, audit, user = _mobile_observation_context(db, observation_id)
    if not obs:
        return _error('Observation not found or plant access denied.', 404)
    payload = request.get_json(silent=True) or {}
    decision = str(payload.get('decision') or '').strip().lower()
    actor = g.mobile_full_name or g.mobile_username
    try:
        review_observation(db, obs, actor, user, decision, payload.get('reviewer_comments'))
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        return _error(str(exc), 403 if 'Only a Quality/Engineering reviewer' in str(exc) else 409)
    return jsonify({'ok': True, 'observation': _observation_json(db, obs, audit)})


@mobile_api.post('/observations/<observation_id>/reassign')
@_bearer_required
def observation_reassign_native(observation_id):
    db = g.mobile_db
    obs, audit, user = _mobile_observation_context(db, observation_id)
    if not obs:
        return _error('Observation not found or plant access denied.', 404)
    payload = request.get_json(silent=True) or {}
    try:
        changes = reassign_observation(
            db, obs, g.mobile_full_name or g.mobile_username, user,
            payload.get('severity'), payload.get('responsible_person'), payload.get('target_date'),
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        return _error(str(exc), 403 if 'Only a reviewer' in str(exc) else 409)
    return jsonify({'ok': True, 'changes': changes, 'observation': _observation_json(db, obs, audit)})

def _build_reference_data(db, site_id, plants, allowed_ids):
    plant_ids = [p['id'] for p in plants]
    asset_query = db.query(Asset).filter_by(site_id=site_id, is_active=True)
    if allowed_ids is not None:
        if not plant_ids:
            assets = []
        else:
            assets = asset_query.filter(Asset.plant_id.in_(plant_ids)).order_by(Asset.asset_name).all()
    else:
        assets = asset_query.order_by(Asset.asset_name).all()
    asset_types = [{'id': t.id, 'name': t.name} for t in db.query(AssetType).order_by(AssetType.name).all()]
    rows = db.query(AuditQuestion, AssetType, AuditSection).join(
        AssetType, AuditQuestion.asset_type_id == AssetType.id, isouter=True
    ).join(AuditSection, AuditQuestion.section_id == AuditSection.id, isouter=True).filter(
        (AuditQuestion.is_active.isnot(False)) | (AuditQuestion.is_active.is_(None))
    ).order_by(AuditSection.section_order, AuditQuestion.question_order, AuditQuestion.id).all()
    questions = []
    for q, asset_type, section in rows:
        questions.append({'id': q.id, 'asset_type_id': q.asset_type_id,
                          'asset_type_name': asset_type.name if asset_type else ('Civil / Infrastructure' if section and section.name == 'Civil / Infrastructure' else 'Site Safety'),
                          'section_id': section.id if section else None,
                          'section': section.name if section else 'Checklist',
                          'text': q.question_text, 'mandatory': bool(q.is_mandatory),
                          'order': q.question_order, 'weight': q.weight})
    return {
        'site_id': site_id,
        'plants': plants,
        'asset_types': asset_types,
        'assets': [{'id': str(a.id), 'plant_id': str(a.plant_id) if a.plant_id else None,
                    'asset_type_id': a.asset_type_id, 'name': a.asset_name}
                   for a in assets],
        'checklist': {'version': hashlib.sha256(json.dumps(questions, sort_keys=True, default=str).encode()).hexdigest(),
                      'questions': questions},
        'scoring': load_scoring_config(),
    }
