import os
import time
import uuid
import json
import shutil
import zipfile
import tempfile
import re
from io import BytesIO
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_file, send_from_directory, jsonify, g, session as flask_session, abort)
from flask_login import LoginManager, login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, case, or_
from models import (Session, Plant, AssetType, Asset, AuditSection,
                    AuditQuestion, Audit, AuditSiteDetail, AuditAsset,
                    AuditResponse, AuditResponsePhoto, CorrectiveAction, User, engine,
                    UserPlantAssignment, Observation, ObservationPhoto, ObservationEvent)
from pdf_report import generate_pdf
from paths import (photos_dir, reports_dir, exports_dir, backups_dir,
                   data_dir, db_path, static_dir, templates_dir)
from site_config import (load_site_config, save_site_config, config_exists,
                         is_valid_site_id)
from site_scope import (SEVERITY_LABELS, STATUS_LABELS, UNFINISHED, WORKING, WorkflowError, age_days, backfill_observations,
    display_status, incomplete_observations, is_overdue, is_reviewer, obs_log, reassign_observation,
    review_observation, save_action, start_work, submit_for_review, sync_observation, today_str,
    load_scoring_config, site_rating_for, score_audit,
                        get_plant_or_404, get_audit_or_404 as _scoped_get_audit,
                        get_user_or_404, get_asset_or_404, next_audit_number,
                        assigned_plant_ids, can_access_plant)
from role_policy import canonical_role, is_admin, is_site_admin, is_hq_admin
from central_access import (list_accessible_sites, resolve_site_context,
                            CentralAccessError, SiteSelectionRequired, SiteAccessDenied)
from version import APP_VERSION, EXPORT_SCHEMA_VERSION

from seed import ensure_seeded, ensure_safety_checklist, ensure_civil_checklist
# SQLite remains self-initializing for the offline workflow. PostgreSQL
# schema and shared reference data are provisioned explicitly, not at startup.
if engine.dialect.name == 'sqlite':
    ensure_seeded()
    ensure_safety_checklist()
    ensure_civil_checklist()
from question_data import SAFETY_SECTION_NAME, CIVIL_SECTION_NAME

app = Flask(__name__, static_folder=str(static_dir()), template_folder=str(templates_dir()))
 
# Public, read-only health check for deployment verification.
# It checks database connectivity only; it does not expose credentials or errors.
@app.route('/health', methods=['GET'])
def health_check():
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return jsonify({
            "status": "healthy",
            "database": "healthy",
            "central_mode": engine.dialect.name == "postgresql",
            "version": APP_VERSION,
        }), 200
    except Exception:
        app.logger.exception("Health check: database connection failed")
        return jsonify({
            "status": "unhealthy",
            "database": "unavailable",
        }), 503


# Versioned native API adapter (disabled unless explicitly enabled by deployment config).
from mobile_api import mobile_api
app.register_blueprint(mobile_api)
# CORS for the native mobile API only.
# Keep the existing web application routes unchanged.
@app.after_request
def add_mobile_api_cors_headers(response):
    if request.path.startswith('/api/mobile/v1/'):
        origin = request.headers.get('Origin', '')

        # Allow the origins used by local Capacitor/WebView development.
        allowed_origins = {
            'http://localhost',
            'https://localhost',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
            'http://10.0.2.2:5173',
            'capacitor://localhost',
            'http://10.0.2.2',
        }

        if origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Methods'] = (
                'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            )
            response.headers['Access-Control-Allow-Headers'] = (
                'Authorization, Content-Type, If-None-Match'
            )
            response.headers['Access-Control-Expose-Headers'] = (
                'ETag'
            )
            response.headers['Access-Control-Max-Age'] = '600'

    return response

app.secret_key = os.environ.get('SECRET_KEY') or 'solar-audit-local-secret-change-if-multi-user-network-shared'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').strip().lower() in {'1', 'true', 'yes', 'on'}
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
UPLOAD_FOLDER = str(photos_dir())
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# Support audits with 100+ high-resolution images. Flask streams multipart uploads
# to temporary files; keep this configurable for deployments with stricter proxy limits.
MAX_PHOTO_BYTES = 50 * 1024 * 1024
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 6 * 1024 * 1024 * 1024))


# ---------------------------------------------------------------------------
# Site identity
#
# This install's site_id/site_name comes ONLY from config/site_config.json
# (see site_config.py), which an ordinary user cannot edit from inside the
# running app. Every protected query below is filtered by this value.
# ---------------------------------------------------------------------------
CENTRAL_MODE = os.environ.get('CENTRAL_MODE', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def current_site():
    """Return the trusted site context for this request."""
    if not CENTRAL_MODE:
        return load_site_config()
    if not current_user.is_authenticated:
        return None
    db = Session()
    try:
        requested = flask_session.get('active_site_id') if is_hq_admin(current_user) else None
        return resolve_site_context(db, current_user, requested)
    except CentralAccessError:
        return None
    finally:
        db.close()


SETUP_EXEMPT_ENDPOINTS = {
    'setup', 'static', 'uploaded_file', 'login', 'hq_dashboard',
    'hq_add_site', 'hq_deactivate_site', 'hq_reactivate_site', 'hq_return',
    'switch_site', 'logout',
}
_backfilled_sites = set()


@app.before_request
def _require_site_setup():
    if request.path.startswith('/api/mobile/v1/'):
        return
    if request.endpoint in SETUP_EXEMPT_ENDPOINTS or (request.endpoint or '').startswith('static'):
        return
    if CENTRAL_MODE:
        if not current_user.is_authenticated:
            return None
        if is_hq_admin(current_user) and not flask_session.get('active_site_id'):
            return redirect(url_for('hq_dashboard'))
        site = current_site()
        if site is None:
            flask_session.pop('active_site_id', None)
            if is_hq_admin(current_user):
                return redirect(url_for('hq_dashboard'))
            logout_user()
            flash('Your assigned site is unavailable. Contact an administrator.', 'error')
            return redirect(url_for('login'))
    else:
        site = current_site()
        if site is None:
            return redirect(url_for('setup'))
    if site.site_id not in _backfilled_sites:
        _backfilled_sites.add(site.site_id)
        bs = Session()
        try:
            if backfill_observations(bs, site.site_id):
                bs.commit()
        except Exception:
            bs.rollback()
        finally:
            bs.close()

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    db = Session()
    try:
        if CENTRAL_MODE:
            user = db.query(User).filter_by(id=user_id, is_active=True).first()
            if user is None:
                return None
            if is_hq_admin(user):
                try: list_accessible_sites(db, user)
                except CentralAccessError: return None
                return user
            if not user.site_id:
                return None
            try: resolve_site_context(db, user)
            except CentralAccessError: return None
            return user
        site = load_site_config()
        if site is None: return None
        return db.query(User).filter_by(id=user_id, site_id=site.site_id, is_active=True).first()
    finally:
        db.close()

@app.context_processor
def inject_current_user():
    return {'current_user': current_user, 'central_mode': CENTRAL_MODE,
            'active_site_context': current_site() if current_user.is_authenticated else None,
            'is_hq_admin': is_hq_admin(current_user) if current_user.is_authenticated else False,
            'canonical_role': canonical_role}

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        if not is_admin(current_user):
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def admin_or_engineer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        if canonical_role(current_user.role) not in ['hq_admin', 'site_admin', 'engineer']:
            flash('Admin or Engineer access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def can_manage_plants(f):
    """Admin and Engineer can manage plants"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        if canonical_role(current_user.role) not in ['hq_admin', 'site_admin']:
            flash('You do not have permission to manage plants.', 'error')
            return redirect(url_for('plants'))
        return f(*args, **kwargs)
    return decorated_function

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _parse_gps(value):
    try:
        if value is None or str(value).strip() == '':
            return None
        v = float(value)
        if not (-90 <= v <= 90):
            return None
        return v
    except Exception:
        return None


def _parse_longitude(value):
    try:
        if value is None or str(value).strip() == '':
            return None
        v = float(value)
        if not (-180 <= v <= 180):
            return None
        return v
    except Exception:
        return None


def _stamp_photo_with_gps(source_path, latitude, longitude, accuracy=None, captured_at=None):
    """Burn audit GPS metadata visibly into the image itself.

    The raw coordinates are also stored in AuditResponsePhoto. The visible
    stamp means the evidence survives common image metadata stripping.
    """
    try:
        image = Image.open(source_path)
        # Bound working memory for high-resolution phone images before drawing
        # the audit stamp. Preserve aspect ratio and keep readable evidence.
        image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
        image = image.convert('RGB')
        draw = ImageDraw.Draw(image, 'RGBA')
        font = ImageFont.load_default()
        stamp_time = captured_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        acc_text = f' | ±{accuracy:.0f} m' if accuracy is not None else ''
        text = f'GPS: {latitude:.6f}, {longitude:.6f}{acc_text}\nCaptured: {stamp_time}'
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=3)
        pad = 10
        box_w = bbox[2] - bbox[0] + pad * 2
        box_h = bbox[3] - bbox[1] + pad * 2
        x = 0
        y = image.height - box_h
        draw.rectangle((x, y, min(image.width, box_w), image.height), fill=(0, 0, 0, 185))
        draw.multiline_text((x + pad, y + pad), text, fill=(255, 255, 255, 255), font=font, spacing=3)
        image.save(source_path, quality=92, optimize=True)
        return True
    except Exception:
        return False


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _valid_uuid(value):
    """Format-validate a UUID string without caring about DB dialect."""
    try:
        uuid.UUID(str(value))
        return str(value)
    except Exception:
        return None


def get_audit_or_404(session, audit_id):
    """Site-scoped audit lookup used throughout this file. An audit that
    exists but belongs to a different site's data 404s exactly like an
    audit that doesn't exist at all — no information is leaked either way.

    Also enforces plant-level assignment: a non-admin user only gets a hit
    if the audit's plant is one they're assigned to. Since every route that
    touches an audit (site details, equipment, submit, report, export,
    delete) goes through this one function, this single check protects all
    of them at once."""
    audit_id = _valid_uuid(audit_id)
    if not audit_id:
        return None
    site = current_site()
    if site is None:
        return None
    audit = _scoped_get_audit(session, audit_id, site.site_id)
    if audit is None:
        return None
    if not can_access_plant(session, current_user, audit.plant_id):
        return None
    return audit


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Checkpoint responses: labels, shared save logic (technical + safety)
#
# The database keeps the original values 'pass' / 'fail' / 'na' so existing
# audits stay valid; the UI/report show them as Complied / Not Complied /
# Not Applicable.
# ---------------------------------------------------------------------------
RESPONSE_LABELS = {'pass': 'Complied', 'fail': 'Not Complied', 'na': 'Not Applicable', 'ofi': 'Opportunity for Improvement'}
VALID_RESPONSES = set(RESPONSE_LABELS)
MAX_PHOTOS_PER_QUESTION = 1000


@app.template_filter('resp_label')
def resp_label(value):
    return RESPONSE_LABELS.get((value or 'na').lower(), 'Not Applicable')


@app.template_filter('sev_label')
def sev_label(value):
    return SEVERITY_LABELS.get((value or '').lower(), '—')


@app.context_processor
def inject_workflow_helpers():
    return {
        'obs_display_status': display_status,
        'obs_status_labels': STATUS_LABELS,
        'user_is_reviewer': is_reviewer(current_user) if current_user.is_authenticated else False,
    }


def _actor():
    return (getattr(current_user, 'full_name', None) or getattr(current_user, 'username', None) or 'unknown')


def _get_response(session, audit_id, asset_id, question_id):
    """asset_id=None addresses site-level (Safety) responses."""
    q = session.query(AuditResponse).filter_by(audit_id=audit_id, question_id=question_id)
    q = q.filter(AuditResponse.asset_id.is_(None)) if asset_id is None else q.filter_by(asset_id=asset_id)
    return q.first()


def _load_checkpoint_state(session, audit, asset_id):
    """responses_map / photos_map / obs_map keyed by str(question_id)."""
    q = session.query(AuditResponse).filter_by(audit_id=audit.id)
    q = q.filter(AuditResponse.asset_id.is_(None)) if asset_id is None else q.filter_by(asset_id=asset_id)
    responses = q.all()
    responses_map = {str(r.question_id): r for r in responses}
    photos_map, obs_map = {}, {}
    for r in responses:
        photos = session.query(AuditResponsePhoto).filter_by(
            audit_response_id=r.id).order_by(AuditResponsePhoto.uploaded_at).all()
        if photos:
            photos_map[str(r.question_id)] = [{'id': str(p.id), 'file_path': p.file_path} for p in photos]
        o = session.query(Observation).filter_by(audit_response_id=r.id).first()
        if o and o.status != 'withdrawn':
            obs_map[str(r.question_id)] = o
    return responses_map, photos_map, obs_map


def _store_photo(file, rel_dir, prefix, form, gps_suffix):
    """Save one uploaded image under UPLOAD_FOLDER/<rel_dir>, burn the GPS stamp
    in when coordinates were supplied. Returns a dict for the DB row or None."""
    if not file or not file.filename or not allowed_file(file.filename):
        return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    folder = os.path.join(UPLOAD_FOLDER, *rel_dir.split('/'))
    # Recreate the complete directory tree immediately before writing. This is
    # intentionally repeated at the write boundary: an administrative restore
    # or filesystem sync can replace the photos root while uploads are running.
    os.makedirs(folder, exist_ok=True)
    filename = f'{prefix}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}.{ext}'
    filepath = os.path.join(folder, filename)
    # Enforce the per-photo limit independently of the whole multipart request.
    try:
        file.stream.seek(0, os.SEEK_END)
        photo_size = file.stream.tell()
        file.stream.seek(0)
    except Exception:
        photo_size = 0
    if photo_size > MAX_PHOTO_BYTES:
        return None
    # On Windows, a concurrent folder replacement can invalidate the path
    # between mkdir and open(). Recreate the directory and retry once instead
    # of failing an otherwise valid immediate photo upload.
    try:
        os.makedirs(folder, exist_ok=True)
        file.save(filepath)
    except FileNotFoundError:
        os.makedirs(folder, exist_ok=True)
        file.stream.seek(0)
        file.save(filepath)

    # Magic-byte & structure verification: reject non-image or corrupt payloads
    try:
        with Image.open(filepath) as img:
            img.verify()
            if (img.format or '').upper() not in {'JPEG', 'PNG', 'WEBP', 'GIF'}:
                raise ValueError('Unsupported image format')
    except Exception:
        try:
            os.remove(filepath)
        except OSError:
            pass
        return None

    latitude = _parse_gps(form.get(f'photo_latitude_{gps_suffix}'))
    longitude = _parse_longitude(form.get(f'photo_longitude_{gps_suffix}'))
    accuracy = None
    try:
        raw_acc = form.get(f'photo_accuracy_{gps_suffix}')
        accuracy = float(raw_acc) if raw_acc not in (None, '') and float(raw_acc) >= 0 else None
    except Exception:
        accuracy = None
    captured_at = None
    raw_cap = form.get(f'photo_captured_at_{gps_suffix}')
    if raw_cap:
        try:
            captured_at = datetime.fromisoformat(raw_cap.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            captured_at = None
    if latitude is not None and longitude is not None:
        _stamp_photo_with_gps(filepath, latitude, longitude, accuracy, captured_at)
    return {'file_path': f'{rel_dir}/{filename}', 'latitude': latitude, 'longitude': longitude,
            'gps_accuracy': accuracy, 'captured_at': captured_at or datetime.now()}


def _save_checkpoints(session, audit, asset_id, valid_question_ids, form, files, site_id):
    """
    Shared by the Technical (per-equipment) and Safety saves.
      * only question ids belonging to this scope are accepted
      * only Complied/Not Complied/Not Applicable values are accepted
      * Not Complied raises/updates an Observation (observations.sync_observation);
        switching away withdraws it rather than deleting it
      * photos are attached to the exact response (audit + equipment + checkpoint)
    Returns (responses_saved, photos_skipped_without_response).
    """
    actor = _actor()
    saved = 0
    for key in list(form.keys()):
        if not key.startswith('response_status_'):
            continue
        qid_s = key.replace('response_status_', '')
        if not qid_s.isdigit() or int(qid_s) not in valid_question_ids:
            continue
        status = (form.get(key) or '').lower()
        if status not in VALID_RESPONSES:
            continue
        qid = int(qid_s)
        resp = _get_response(session, audit.id, asset_id, qid)
        remark = form.get(f'remarks_{qid}', '')
        if resp:
            resp.response_status, resp.remarks = status, remark
        else:
            resp = AuditResponse(audit_id=audit.id, asset_id=asset_id, question_id=qid,
                                 response_status=status, remarks=remark)
            session.add(resp)
        resp.updated_at = datetime.utcnow()
        resp.responded_by = actor
        session.flush()
        sync_observation(session, audit, resp, site_id, actor, {
            'observation': form.get(f'obs_text_{qid}'),
            'severity': None,  # classified later in Observations after audit submission
            'responsible_person': form.get(f'obs_responsible_{qid}'),
            'target_date': form.get(f'obs_target_{qid}'),
            'obs_remarks': form.get(f'obs_remarks_{qid}'),
        })
        saved += 1
    session.commit()

    skipped = 0
    scope = str(asset_id).replace('-', '')[:12] if asset_id else 'safety'
    short_audit_dir = str(audit.id).replace('-', '')[:12]
    for key in list(files.keys()):
        if not key.startswith('photo_'):
            continue
        qid_s = key.replace('photo_', '')
        if not qid_s.isdigit() or int(qid_s) not in valid_question_ids:
            continue
        upload = [f for f in files.getlist(key) if f and f.filename]
        if not upload:
            continue
        qid = int(qid_s)
        resp = _get_response(session, audit.id, asset_id, qid)
        if not resp:
            skipped += len(upload)  # a photo needs a response to attach to
            continue
        existing = session.query(AuditResponsePhoto).filter_by(audit_response_id=resp.id).count()
        for f in upload[:max(0, MAX_PHOTOS_PER_QUESTION - existing)]:
            info = _store_photo(f, f'{short_audit_dir}/{scope}', f'q{qid}', form, qid)
            if info:
                session.add(AuditResponsePhoto(audit_response_id=resp.id, **info))
                session.commit()
            else:
                skipped += 1
    return saved, skipped



# ---------------------------------------------------------------------------
# Mobile offline-first API
#
# These endpoints back the existing static/offline.html capture workspace.
# They use the normal Flask-Login session (never a password/token stored in
# IndexedDB/localStorage), are site/plant scoped, and are idempotent by the
# client-generated UUIDs used by the PWA.
# ---------------------------------------------------------------------------
@app.route('/api/offline/ping')
@login_required
def offline_ping():
    site = current_site()
    return jsonify({'ok': True, 'site_id': site.site_id, 'user_id': str(current_user.id), 'username': current_user.username, 'server_time': datetime.now(timezone.utc).isoformat()})


@app.route('/api/offline/bootstrap')
@login_required
def offline_bootstrap():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        q = session.query(Plant).filter_by(site_id=site.site_id).order_by(Plant.name)
        if allowed is not None:
            q = q.filter(Plant.id.in_(allowed))
        plants = []
        for p in q.all():
            plants.append({
                'id': str(p.id), 'name': p.name, 'location': p.location,
                'capacity_mw': p.capacity_mw, 'building_name': p.building_name,
                'city': p.city, 'state': p.state,
                'site_detail': {k: getattr(p, k) for k in (
                    'no_of_blocks','dc_capacity','ac_capacity',
                    'central_inverter_type','central_inverter_count',
                    'string_inverter_type','string_inverter_count','scb_count',
                    'mms_type','mms_count','transformer_type','transformer_count',
                    'clamps_connectors_count','lt_panel_type','lt_panel_count',
                    'ht_panel_type','ht_panel_count','acdb_count',
                    'ups_db_battery_count','lightning_protection_count',
                    'dc_earthing','ac_earthing'
                )}
            })
        rows = session.query(AuditQuestion, AssetType, AuditSection).join(
            AssetType, AuditQuestion.asset_type_id == AssetType.id, isouter=True
        ).join(
            AuditSection, AuditQuestion.section_id == AuditSection.id, isouter=True
        ).filter(
            or_(AuditQuestion.is_active.isnot(False), AuditQuestion.is_active.is_(None))
        ).order_by(
            AuditSection.section_order, AuditQuestion.question_order, AuditQuestion.id
        ).all()
        questions = [{
            'question_id': q.id,
            'asset_type_id': q.asset_type_id,
            'asset_type_name': (at.name if at else ('Civil / Infrastructure' if sec and sec.name == CIVIL_SECTION_NAME else 'Site Safety')),
            'section': sec.name if sec else 'Checklist',
            'text': q.question_text,
            'mandatory': bool(q.is_mandatory),
            'order': q.question_order,
            'weight': q.weight,
        } for q, at, sec in rows]
        return jsonify({
            'ok': True, 'schema_version': EXPORT_SCHEMA_VERSION,
            'application_version': APP_VERSION, 'site_id': site.site_id,
            'site_name': site.site_name, 'plants': plants, 'questions': questions,
            'offline_user_id': str(current_user.id),
            'offline_username': current_user.username,
            'cached_at': datetime.now(timezone.utc).isoformat(), 'scoring': load_scoring_config()
        })
    finally:
        session.close()


def _offline_json_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


def _offline_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return None


def _offline_parse_date(value):
    return value if value and re.match(r'^\\d{4}-\\d{2}-\\d{2}$', str(value)) else None


@app.route('/api/offline/sync/audit', methods=['POST'])
@login_required
def offline_sync_audit():
    site = current_site()
    payload = request.get_json(silent=True) or {}
    local_uuid = _offline_uuid(payload.get('local_uuid'))
    if not local_uuid:
        return _offline_json_error('local_uuid must be a valid UUID')
    plant_id = _valid_uuid(payload.get('plant_id'))
    session = Session()
    try:
        # Older offline drafts can contain a malformed/missing cached plant UUID.
        # Recover them by exact plant name within this user's site instead of
        # permanently poisoning the sync queue. Never accept a plant outside
        # the current site/assignment scope.
        plant = None
        if plant_id:
            plant = get_plant_or_404(session, plant_id, site.site_id)
        if plant is None and payload.get('plant_name'):
            plant = session.query(Plant).filter(
                Plant.site_id == site.site_id,
                func.lower(Plant.name) == str(payload.get('plant_name')).strip().lower()
            ).first()
        if not plant:
            return _offline_json_error('Plant could not be resolved from offline data. Download the latest plant data while online and retry.', 400)
        if not can_access_plant(session, current_user, plant.id):
            return _offline_json_error('Plant is not available to this user', 403)

        audit = session.query(Audit).filter_by(id=local_uuid, site_id=site.site_id).first()
        if audit is None:
            try:
                year = datetime.strptime(payload.get('audit_date',''), '%Y-%m-%d').year
            except Exception:
                year = datetime.now(timezone.utc).year
            audit = Audit(
                id=local_uuid, plant_id=plant.id, site_id=site.site_id,
                audit_number=next_audit_number(session, site.site_id, year),
                status='draft'
            )
            session.add(audit)
            session.flush()
            sd = payload.get('site_detail') or {}
            session.add(AuditSiteDetail(
                audit_id=audit.id,
                **{k: sd.get(k) for k in (
                    'no_of_blocks','dc_capacity','ac_capacity',
                    'central_inverter_type','central_inverter_count',
                    'string_inverter_type','string_inverter_count','scb_count',
                    'mms_type','mms_count','transformer_type','transformer_count',
                    'clamps_connectors_count','lt_panel_type','lt_panel_count',
                    'ht_panel_type','ht_panel_count','acdb_count',
                    'ups_db_battery_count','lightning_protection_count',
                    'dc_earthing','ac_earthing'
                )}
            ))
        elif audit.status == 'submitted':
            # A retry may need the server asset IDs to upload photos after the
            # manifest itself has already been accepted. Return the same stable
            # asset_ref -> server asset ID mapping even for submitted audits.
            asset_map = {}
            for a in payload.get('assets') or []:
                aref = str(a.get('asset_ref') or a.get('asset_name') or '').strip()
                name = str(a.get('asset_name') or '').strip()
                try:
                    atid = int(a.get('asset_type_id'))
                except Exception:
                    continue
                if not aref or not name:
                    continue
                asset = session.query(Asset).filter_by(
                    plant_id=audit.plant_id, asset_type_id=atid,
                    asset_name=name, site_id=site.site_id
                ).first()
                if asset is None:
                    linked = session.query(AuditAsset, Asset).join(
                        Asset, AuditAsset.asset_id == Asset.id
                    ).filter(
                        AuditAsset.audit_id == audit.id,
                        Asset.site_id == site.site_id,
                        Asset.asset_name == name,
                        Asset.asset_type_id == atid,
                    ).first()
                    asset = linked[1] if linked else None
                if asset is not None:
                    # A previously submitted audit can have a valid site/plant
                    # asset but lack the AuditAsset join row (for example, if
                    # it was submitted before the mobile manifest retry). The
                    # photo endpoint requires this association. Reconcile only
                    # when the asset belongs to this audit's exact plant and
                    # site; never attach an unrelated asset to a locked audit.
                    if str(asset.plant_id) != str(audit.plant_id) or str(asset.site_id) != str(site.site_id):
                        continue
                    linked_asset = session.query(AuditAsset).filter_by(
                        audit_id=audit.id, asset_id=asset.id
                    ).first()
                    if linked_asset is None:
                        session.add(AuditAsset(
                            audit_id=audit.id,
                            asset_id=asset.id,
                            location='',
                            rank='N/A',
                            observation='',
                            remarks='',
                            is_audited=True,
                        ))
                        session.flush()
                    asset_map[aref] = str(asset.id)
            # Persist any missing audit/asset associations before returning the
            # mapping that the APK uses for queued photo uploads. This does not
            # change the submitted status or alter checkpoint scores.
            session.commit()
            return jsonify({'ok': True, 'local_uuid': local_uuid,
                            'audit_id': audit.id, 'audit_number': audit.audit_number,
                            'asset_map': asset_map, 'already_submitted': True})

        # Idempotent upsert of the audit's scalar fields.
        for key in ('audit_date','audit_time','audit_type','auditor_name','auditee_name',
                    'building_name','capacity_mw','city','state','asset_id_ref'):
            if key in payload:
                setattr(audit, key, payload.get(key))
        audit.plant_id = plant.id
        audit.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        sd = payload.get('site_detail')
        if isinstance(sd, dict):
            detail = session.query(AuditSiteDetail).filter_by(audit_id=audit.id).first()
            if detail:
                for k in ('no_of_blocks','dc_capacity','ac_capacity',
                          'central_inverter_type','central_inverter_count',
                          'string_inverter_type','string_inverter_count','scb_count',
                          'mms_type','mms_count','transformer_type','transformer_count',
                          'clamps_connectors_count','lt_panel_type','lt_panel_count',
                          'ht_panel_type','ht_panel_count','acdb_count',
                          'ups_db_battery_count','lightning_protection_count',
                          'dc_earthing','ac_earthing'):
                    if k in sd: setattr(detail, k, sd[k])

        # Assets are identified by the PWA's stable asset_ref. On retry the
        # same audit/plant/name/type resolves to the same server asset.
        asset_map = {}
        for a in payload.get('assets') or []:
            aref = str(a.get('asset_ref') or a.get('asset_name') or '').strip()
            name = str(a.get('asset_name') or '').strip()
            try: atid = int(a.get('asset_type_id'))
            except Exception: continue
            if not aref or not name: continue
            asset = None
            # First reuse an already-created audit asset with the same ref/name.
            existing_aa = None
            for aa in session.query(AuditAsset).filter_by(audit_id=audit.id).all():
                candidate = session.query(Asset).filter_by(id=aa.asset_id, site_id=site.site_id).first()
                if candidate and candidate.asset_name == name and int(candidate.asset_type_id) == atid:
                    existing_aa = aa; asset = candidate; break
            if asset is None:
                asset = session.query(Asset).filter_by(
                    plant_id=plant.id, asset_type_id=atid, asset_name=name, site_id=site.site_id
                ).first()
            if asset is None:
                asset = Asset(plant_id=plant.id, asset_type_id=atid, asset_name=name,
                              is_active=True, site_id=site.site_id)
                session.add(asset); session.flush()
            aa = existing_aa or session.query(AuditAsset).filter_by(
                audit_id=audit.id, asset_id=asset.id).first()
            if aa is None:
                aa = AuditAsset(audit_id=audit.id, asset_id=asset.id)
                session.add(aa)
            aa.location = a.get('location') or ''
            aa.rank = a.get('rank') or 'N/A'
            aa.observation = a.get('observation') or ''
            aa.remarks = a.get('remarks') or ''
            aa.is_audited = bool(a.get('is_audited'))
            asset_map[aref] = str(asset.id)

        session.flush()

        # Upsert responses and observations. Foreign question IDs are rejected
        # rather than silently creating corrupt cross-type responses.
        for r in payload.get('responses') or []:
            try: qid = int(r.get('question_id'))
            except Exception: continue
            q = session.query(AuditQuestion).filter_by(id=qid).first()
            if not q: continue
            aref = str(r.get('asset_ref') or '')
            asset_id = asset_map.get(aref) if aref and aref not in ('Site Safety', 'Civil / Infrastructure') else None
            if asset_id and int(q.asset_type_id or -1) != int(
                    session.query(Asset).filter_by(id=asset_id).first().asset_type_id):
                continue
            if not asset_id and aref not in ('', 'Site Safety', 'Civil / Infrastructure'):
                continue
            status = str(r.get('response_status') or 'na').lower()
            if status not in VALID_RESPONSES: continue
            resp = _get_response(session, audit.id, asset_id, qid)
            if resp is None:
                resp = AuditResponse(audit_id=audit.id, asset_id=asset_id,
                                     question_id=qid, response_status=status)
                session.add(resp)
            resp.response_status = status
            resp.remarks = r.get('remarks') or ''
            resp.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            resp.responded_by = _actor()
            session.flush()
            sync_observation(session, audit, resp, site.site_id, _actor(), {
                'observation': r.get('observation'),
                'severity': None,  # offline checkpoints no longer classify severity during audit
                'responsible_person': None,
                'target_date': None,
                'obs_remarks': None,
            })
        session.commit()
        return jsonify({'ok': True, 'local_uuid': local_uuid,
                        'audit_id': str(audit.id), 'audit_number': audit.audit_number,
                        'status': audit.status, 'asset_map': asset_map})
    except Exception as exc:
        session.rollback()
        return _offline_json_error(f'sync failed: {exc}', 500)
    finally:
        session.close()


@app.route('/api/offline/sync/photo', methods=['POST'])
@login_required
def offline_sync_photo():
    """Upload exactly one durable offline photo.

    The client supplies a stable local photo UUID. The server uses that UUID
    as the idempotency key (encoded in the deterministic file path) and never
    creates a second AuditResponsePhoto for a successful retry.
    """
    site = current_site()
    local_photo_uuid = _offline_uuid(request.form.get('local_uuid'))
    audit_local_uuid = _offline_uuid(request.form.get('audit_local_uuid'))
    if not local_photo_uuid or not audit_local_uuid:
        return _offline_json_error('photo UUIDs are required')
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_local_uuid)
        if not audit:
            return _offline_json_error('Audit not found', 404)
        try:
            qid = int(request.form.get('question_id') or 0)
        except Exception:
            qid = 0
        q = session.query(AuditQuestion).filter_by(id=qid).first()
        if not q:
            return _offline_json_error('Question not found', 404)

        asset_id = _valid_uuid(request.form.get('asset_id'))
        asset_ref = request.form.get('asset_ref') or 'Site Safety'
        if asset_id:
            linked = session.query(AuditAsset).filter_by(audit_id=audit.id, asset_id=asset_id).first()
            asset = session.query(Asset).filter_by(id=asset_id, site_id=site.site_id).first()
            if not linked or not asset:
                return _offline_json_error('Equipment not found for audit', 404)
        elif asset_ref not in ('Site Safety', 'Civil / Infrastructure'):
            # Backward-compatible fallback for older offline drafts. Newer
            # clients send server_asset_id after the audit sync response.
            row = session.query(AuditAsset, Asset).join(
                Asset, AuditAsset.asset_id == Asset.id
            ).filter(AuditAsset.audit_id == audit.id,
                     Asset.asset_name == asset_ref,
                     Asset.site_id == site.site_id).first()
            if not row:
                return _offline_json_error('Equipment not found for audit', 404)
            asset_id = str(row[0].asset_id)

        resp = _get_response(session, audit.id, asset_id, qid)
        if not resp:
            return _offline_json_error('Response must sync before its photo', 409)
        upload = request.files.get('file')
        if not upload or not upload.filename:
            return _offline_json_error('Photo file is required')
        if not allowed_file(upload.filename):
            return _offline_json_error('Unsupported photo file type')

        short_audit_dir = str(audit.id).replace('-', '')[:12]
        if asset_id:
            photo_scope = str(asset_id).replace('-', '')[:12]
        else:
            photo_scope = 'civil' if asset_ref == 'Civil / Infrastructure' else 'safety'
        rel_dir = f'{short_audit_dir}/{photo_scope}'
        filename = f'p_{local_photo_uuid.replace("-", "")[:24]}.jpg'
        rel_path = f'{rel_dir}/{filename}'
        # Also recognize the previous long-path filename format so an upload
        # whose response was lost before this upgrade remains idempotent.
        existing = session.query(AuditResponsePhoto).filter(
            AuditResponsePhoto.audit_response_id == resp.id,
            or_(AuditResponsePhoto.file_path == rel_path,
                AuditResponsePhoto.file_path.like(f'%photo_{local_photo_uuid}.jpg'))
        ).first()
        if existing:
            return jsonify({'ok': True, 'duplicate': True, 'photo_id': str(existing.id)})

        folder = os.path.join(UPLOAD_FOLDER, *rel_dir.split('/'))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        try:
            upload.stream.seek(0, os.SEEK_END)
            photo_size = upload.stream.tell()
            upload.stream.seek(0)
        except Exception:
            photo_size = 0
        if photo_size > MAX_PHOTO_BYTES:
            return _offline_json_error('Each photo must be 50 MB or smaller', 413)
        upload.save(path)

        lat = _parse_gps(request.form.get('latitude'))
        lon = _parse_longitude(request.form.get('longitude'))
        try:
            acc = float(request.form.get('gps_accuracy')) if request.form.get('gps_accuracy') else None
        except Exception:
            acc = None
        captured = None
        if request.form.get('captured_at'):
            try:
                captured = datetime.fromisoformat(request.form['captured_at'].replace('Z','+00:00')).replace(tzinfo=None)
            except Exception:
                captured = None
        if lat is not None and lon is not None:
            _stamp_photo_with_gps(path, lat, lon, acc, captured)

        ph = AuditResponsePhoto(audit_response_id=resp.id, file_path=rel_path,
                                latitude=lat, longitude=lon, gps_accuracy=acc,
                                captured_at=captured or datetime.now(timezone.utc).replace(tzinfo=None))
        session.add(ph)
        session.commit()
        return jsonify({'ok': True, 'photo_id': str(ph.id)})
    except Exception as exc:
        session.rollback()
        # If database persistence failed after the file was written, do not
        # leave a misleading orphan around to be mistaken for a successful
        # upload. A later retry will write the same deterministic path again.
        try:
            if 'path' in locals() and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return _offline_json_error(f'photo sync failed: {exc}', 500)
    finally:
        session.close()


@app.route('/api/offline/sync/complete', methods=['POST'])
@login_required
def offline_sync_complete():
    site = current_site()
    payload = request.get_json(silent=True) or {}
    aid = _offline_uuid(payload.get('audit_local_uuid'))
    if not aid: return _offline_json_error('audit_local_uuid is required')
    session = Session()
    try:
        audit = get_audit_or_404(session, aid)
        if not audit: return _offline_json_error('Audit not found', 404)
        if audit.status == 'submitted':
            return jsonify({'ok': True, 'already_complete': True})
        incomplete = incomplete_observations(session, audit.id)
        if incomplete:
            return _offline_json_error('Audit still has incomplete Not Complied observations', 409)
        result = score_audit(session, audit.id)
        audit.status = 'submitted'
        audit.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        audit.compliance_pct = result['compliance_pct']
        audit.score_pct = result['score_pct']
        audit.site_rating = result['rating']
        audit.score_detail = json.dumps(result)
        session.commit()
        try:
            generate_pdf(str(audit.id), site_id=site.site_id)
        except Exception:
            pass
        return jsonify({'ok': True, 'status': 'submitted',
                        'audit_id': audit.id, 'audit_number': audit.audit_number})
    except Exception as exc:
        session.rollback()
        return _offline_json_error(f'complete failed: {exc}', 500)
    finally:
        session.close()


# Native bearer-token endpoints delegate to these same established PWA handlers.
from mobile_api import configure_legacy_sync_handlers, configure_observation_photo_store
configure_legacy_sync_handlers(offline_sync_audit, offline_sync_photo, offline_sync_complete)
configure_observation_photo_store(_store_photo)

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """
    One-time, first-run wizard: assigns this install to a site and creates
    its first administrator account.

    This is the ONLY place site_id is ever written. Once config/site_config
    .json exists, this route immediately bounces to /login — an ordinary
    user cannot get back here to reassign the site (requirement #25). If a
    site genuinely needs reassigning, an administrator must delete
    config/site_config.json (and normally the database) directly on disk,
    which is a deliberate out-of-band, filesystem-level action.
    """
    site = current_site()
    session = Session()
    try:
        has_users = session.query(func.count(User.id)).scalar() > 0 if site else False
        if site is not None and has_users:
            flash('This installation is already configured.', 'error')
            return redirect(url_for('login'))

        if request.method == 'POST':
            site_id = request.form.get('site_id', '').strip()
            site_name = request.form.get('site_name', '').strip()
            username = request.form.get('username', '').strip()
            full_name = request.form.get('full_name', '').strip()
            password = request.form.get('password', '')

            if site is None:
                try:
                    site = save_site_config(site_id, site_name)
                except ValueError as e:
                    flash(str(e), 'error')
                    return render_template('setup.html', site=None)

            if not has_users:
                if not username or not full_name or not password:
                    flash('Administrator username, full name, and password are required.', 'error')
                    return render_template('setup.html', site=site)
                if len(password) < 6:
                    flash('Password must be at least 6 characters.', 'error')
                    return render_template('setup.html', site=site)
                admin = User(
                    username=username,
                    full_name=full_name,
                    password_hash=generate_password_hash(password),
                    role='site_admin',
                    is_active=True,
                    site_id=site.site_id,
                )
                session.add(admin)
                session.commit()

            flash(f'Site "{site.site_name}" ({site.site_id}) configured successfully. Please log in.', 'success')
            return redirect(url_for('login'))

        return render_template('setup.html', site=site)
    finally:
        session.close()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        from rate_limit import is_rate_limited, record_failure, clear_failures
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Username and password are required.', 'error')
            return redirect(url_for('login'))
        rate_key = f"web:{request.remote_addr or 'unknown'}:{username.lower()}"
        limited, retry_after = is_rate_limited(rate_key)
        if limited:
            flash(f'Too many failed login attempts. Try again in {retry_after} seconds.', 'error')
            return redirect(url_for('login')), 429
        db = Session()
        try:
            if CENTRAL_MODE:
                user = db.query(User).filter_by(username=username).first()
            else:
                site = load_site_config()
                if site is None:
                    flash('This installation has not been configured.', 'error')
                    return redirect(url_for('setup'))
                user = db.query(User).filter_by(username=username, site_id=site.site_id).first()
            if not user or not check_password_hash(user.password_hash, password):
                record_failure(rate_key)
                flash('Invalid username or password.', 'error')
                return redirect(url_for('login'))
            if not user.is_active:
                record_failure(rate_key)
                flash('Your account is disabled.', 'error')
                return redirect(url_for('login'))
            clear_failures(rate_key)
            if CENTRAL_MODE:
                try:
                    list_accessible_sites(db, user) if is_hq_admin(user) else resolve_site_context(db, user)
                except CentralAccessError:
                    flash('Your account does not have a valid organization/site assignment. Contact an administrator.', 'error')
                    return redirect(url_for('login'))
            login_user(user, remember=True)
            flask_session.permanent = True
            flask_session.pop('active_site_id', None)
            if CENTRAL_MODE and is_hq_admin(user):
                return redirect(url_for('hq_dashboard'))
            flash('Logged in successfully.', 'success')
            return redirect(url_for('dashboard'))
        finally:
            db.close()
    return render_template('login.html')

@app.route('/hq')
@login_required
def hq_dashboard():
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    db = Session()
    try:
        from models import Site
        active_sites = list_accessible_sites(db, current_user)
        org_id = getattr(current_user, 'organization_id', None)
        inactive_q = db.query(Site).filter(Site.is_active == False)
        if org_id:
            inactive_q = inactive_q.filter(Site.organization_id == org_id)
        inactive_sites = inactive_q.order_by(Site.site_name).all()
        resp = app.make_response(
            render_template('hq_dashboard.html', sites=active_sites, inactive_sites=inactive_sites)
        )
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        return resp
    except CentralAccessError:
        abort(403)
    finally:
        db.close()


@app.route('/hq/sites/add', methods=['POST'])
@login_required
def hq_add_site():
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    site_id = (request.form.get('site_id') or '').strip().upper()
    site_name = (request.form.get('site_name') or '').strip()
    admin_username = (request.form.get('admin_username') or '').strip()
    admin_full_name = (request.form.get('admin_full_name') or '').strip()
    admin_password = request.form.get('admin_password') or ''

    if not is_valid_site_id(site_id) or not site_name:
        flash('Valid Site ID (letters/numbers/underscore/hyphen) and Site Name are required.', 'error')
        return redirect(url_for('hq_dashboard'))
    if admin_username and (not admin_full_name or len(admin_password) < 6):
        flash('When creating a site admin, full name and a password of at least 6 characters are required.', 'error')
        return redirect(url_for('hq_dashboard'))

    db = Session()
    try:
        from models import Site, Organization
        org_id = getattr(current_user, 'organization_id', None)
        if not org_id:
            first_org = db.query(Organization).filter_by(is_active=True).order_by(Organization.created_at).first()
            org_id = first_org.id if first_org else None
        existing = db.query(Site).filter_by(site_id=site_id).first()
        if existing:
            flash(f'Site ID "{site_id}" already exists.', 'error')
            return redirect(url_for('hq_dashboard'))
        new_site = Site(site_id=site_id, site_name=site_name, organization_id=org_id, is_active=True)
        db.add(new_site)
        if admin_username:
            if db.query(User).filter_by(username=admin_username).first():
                db.rollback()
                flash(f'Username "{admin_username}" already exists.', 'error')
                return redirect(url_for('hq_dashboard'))
            db.add(User(
                username=admin_username,
                full_name=admin_full_name,
                password_hash=generate_password_hash(admin_password),
                role='site_admin',
                is_active=True,
                site_id=site_id,
                organization_id=org_id,
            ))
        db.commit()
        flash(f'Site "{site_name}" ({site_id}) added.', 'success')
        return redirect(url_for('hq_dashboard'))
    except Exception:
        db.rollback()
        app.logger.exception('HQ add site failed')
        flash('Could not add site. Please retry.', 'error')
        return redirect(url_for('hq_dashboard'))
    finally:
        db.close()


@app.route('/hq/sites/<site_id>/deactivate', methods=['POST'])
@login_required
def hq_deactivate_site(site_id):
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    db = Session()
    try:
        from models import Site
        org_id = getattr(current_user, 'organization_id', None)
        q = db.query(Site).filter_by(site_id=site_id)
        if org_id:
            q = q.filter(Site.organization_id == org_id)
        site = q.first()
        if not site:
            flash('Site not found in your organization.', 'error')
            return redirect(url_for('hq_dashboard'))
        site.is_active = False
        if flask_session.get('active_site_id') == site.site_id:
            flask_session.pop('active_site_id', None)
        db.commit()
        flash(f'Site "{site.site_name}" ({site.site_id}) deactivated. Historical audit data has been preserved.', 'success')
        return redirect(url_for('hq_dashboard'))
    finally:
        db.close()


@app.route('/hq/sites/<site_id>/reactivate', methods=['POST'])
@login_required
def hq_reactivate_site(site_id):
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    db = Session()
    try:
        from models import Site
        org_id = getattr(current_user, 'organization_id', None)
        q = db.query(Site).filter_by(site_id=site_id)
        if org_id:
            q = q.filter(Site.organization_id == org_id)
        site = q.first()
        if not site:
            flash('Site not found in your organization.', 'error')
            return redirect(url_for('hq_dashboard'))
        site.is_active = True
        db.commit()
        flash(f'Site "{site.site_name}" ({site.site_id}) restored.', 'success')
        return redirect(url_for('hq_dashboard'))
    finally:
        db.close()


@app.route('/switch-site/<site_id>', methods=['POST'])
@login_required
def switch_site(site_id):
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    db = Session()
    try:
        context = resolve_site_context(db, current_user, site_id)
        flask_session['active_site_id'] = context.site_id
        flash(f'Now working in {context.site_name}.', 'success')
        return redirect(url_for('dashboard'))
    except CentralAccessError:
        abort(403)
    finally:
        db.close()


@app.route('/hq/return')
@login_required
def hq_return():
    if not CENTRAL_MODE or not is_hq_admin(current_user): abort(403)
    flask_session.pop('active_site_id', None)
    return redirect(url_for('hq_dashboard'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))


@app.route('/users')
@login_required
@admin_required
def users():
    site = current_site()
    session = Session()
    try:
        users_raw = session.query(User).filter_by(site_id=site.site_id).order_by(User.created_at.desc()).all()
        plants_by_id = {p.id: p.name for p in session.query(Plant).filter_by(site_id=site.site_id).all()}
        users_list = []
        for u in users_raw:
            if is_admin(u):
                plant_names = 'All plants'
            else:
                ids = [r[0] for r in session.query(UserPlantAssignment.plant_id).filter_by(user_id=u.id).all()]
                plant_names = ', '.join(plants_by_id.get(pid, 'Unknown') for pid in ids) if ids else 'None assigned'
            users_list.append({'user': u, 'plant_names': plant_names})
        return render_template('users.html', users=users_list)
    finally:
        session.close()


@app.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def users_new():
    site = current_site()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        full_name = request.form.get('full_name', '').strip()
        password = request.form.get('password', '')
        role = canonical_role(request.form.get('role', 'user'))
        if role not in ('site_admin', 'engineer', 'user'):
            flash('Choose Site Admin, Engineer, or User.', 'error')
            return redirect(url_for('users_new'))
        plant_ids = request.form.getlist('plant_ids')

        if not username or not full_name or not password:
            flash('Username, full name, and password are required.', 'error')
            return redirect(url_for('users_new'))

        session = Session()
        try:
            # Username uniqueness is global in this DB, but since this DB
            # only ever holds one site's data, that's equivalent to
            # per-site uniqueness.
            existing_user = session.query(User).filter_by(username=username).first()
            if existing_user:
                flash(f'Username "{username}" already exists.', 'error')
                return redirect(url_for('users_new'))

            password_hash = generate_password_hash(password)
            new_user = User(
                username=username,
                full_name=full_name,
                password_hash=password_hash,
                role=role,
                is_active=True,
                site_id=site.site_id,
                organization_id=getattr(site, 'organization_id', None),
            )
            session.add(new_user)
            session.flush()

            if role not in ('hq_admin', 'site_admin'):
                for plant_id in plant_ids:
                    plant_id = _valid_uuid(plant_id)
                    if plant_id and get_plant_or_404(session, plant_id, site.site_id):
                        session.add(UserPlantAssignment(
                            user_id=new_user.id, plant_id=plant_id, site_id=site.site_id))

            session.commit()

            flash(f'User "{username}" created successfully with role: {role}', 'success')
            return redirect(url_for('users'))
        except Exception as e:
            session.rollback()
            flash(f'Error creating user: {str(e)}', 'error')
            return redirect(url_for('users_new'))
        finally:
            session.close()

    session = Session()
    try:
        plants = session.query(Plant).filter_by(site_id=site.site_id).order_by(Plant.name).all()
        return render_template('users_new.html', plants=plants)
    finally:
        session.close()


@app.route('/users/<user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def users_edit(user_id):
    site = current_site()
    session = Session()
    try:
        user = get_user_or_404(session, user_id, site.site_id)
        if not user:
            flash('User not found.', 'error')
            return redirect(url_for('users'))
        
        if request.method == 'POST':
            full_name = request.form.get('full_name', '').strip()
            role = canonical_role(request.form.get('role', user.role))
            if role not in ('site_admin', 'engineer', 'user'):
                flash('Invalid site role. HQ Admin accounts are managed centrally.', 'error')
                return redirect(url_for('users_edit', user_id=user_id))
            is_active = request.form.get('is_active', 'off') == 'on'
            password = request.form.get('password', '')
            plant_ids = request.form.getlist('plant_ids')

            if not full_name:
                flash('Full name is required.', 'error')
                return redirect(url_for('users_edit', user_id=user_id))
            
            user.full_name = full_name
            user.role = role
            user.is_active = is_active
            
            if password:
                user.password_hash = generate_password_hash(password)

            # Replace this user's plant assignments with whatever was
            # checked. Admins are always unrestricted regardless of any
            # rows here, so we don't bother storing any for that role.
            session.query(UserPlantAssignment).filter_by(user_id=user.id).delete()
            if role not in ('hq_admin', 'site_admin'):
                for plant_id in plant_ids:
                    plant_id = _valid_uuid(plant_id)
                    if plant_id and get_plant_or_404(session, plant_id, site.site_id):
                        session.add(UserPlantAssignment(
                            user_id=user.id, plant_id=plant_id, site_id=site.site_id))

            session.commit()
            flash(f'User "{user.username}" updated successfully.', 'success')
            return redirect(url_for('users'))
        
        plants = session.query(Plant).filter_by(site_id=site.site_id).order_by(Plant.name).all()
        assigned_ids = {r[0] for r in session.query(UserPlantAssignment.plant_id).filter_by(user_id=user.id).all()}
        return render_template('users_edit.html', user=user, plants=plants, assigned_ids=assigned_ids)
    finally:
        session.close()


@app.route('/users/<user_id>/delete', methods=['POST'])
@login_required
@admin_required
def users_delete(user_id):
    site = current_site()
    session = Session()
    try:
        # Prevent deleting own account
        if user_id == current_user.id:
            flash('You cannot delete your own account.', 'error')
            return redirect(url_for('users'))
        
        user = get_user_or_404(session, user_id, site.site_id)
        if not user:
            flash('User not found.', 'error')
            return redirect(url_for('users'))
        
        username = user.username
        session.query(UserPlantAssignment).filter_by(user_id=user.id).delete()
        session.delete(user)
        session.commit()
        
        flash(f'User "{username}" has been deleted.', 'success')
        return redirect(url_for('users'))
    except Exception as e:
        session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
        return redirect(url_for('users'))
    finally:
        session.close()


@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))



@app.route('/mobile')
@login_required
def mobile_home():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        query = session.query(Audit, Plant).join(Plant, Audit.plant_id == Plant.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False)
        if allowed is not None:
            query = query.filter(Audit.plant_id.in_(allowed))
        recent = query.order_by(Audit.created_at.desc()).limit(8).all()
        return render_template('mobile_home.html', audits=recent, site=site)
    finally:
        session.close()


@app.route('/dashboard')
@login_required
def dashboard():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)

        def _plant_ok(query, plant_col):
            if allowed is None:
                return query
            return query.filter(plant_col.in_(allowed))  # allowed=[] naturally yields zero rows

        total_plants = _plant_ok(
            session.query(func.count(Plant.id)).filter(Plant.site_id == site.site_id), Plant.id
        ).scalar() or 0
        audit_base = session.query(Audit).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False
        )
        if allowed is not None:
            audit_base = audit_base.filter(Audit.plant_id.in_(allowed))
        total_audits = audit_base.count()
        draft_audits_count = audit_base.filter(Audit.status == 'draft').count()
        closed_audits_count = audit_base.filter(Audit.status == 'submitted').count()
        latest_audit = audit_base.order_by(Audit.created_at.desc()).first()
        latest_audit_number = latest_audit.audit_number if latest_audit else '—'
        total_responses = _plant_ok(session.query(func.count(AuditResponse.id)).join(
            Audit, AuditResponse.audit_id == Audit.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False), Audit.plant_id).scalar() or 0
        pass_count = _plant_ok(session.query(func.count(AuditResponse.id)).join(
            Audit, AuditResponse.audit_id == Audit.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False,
            func.lower(AuditResponse.response_status) == 'pass'), Audit.plant_id).scalar() or 0
        fail_count = _plant_ok(session.query(func.count(AuditResponse.id)).join(
            Audit, AuditResponse.audit_id == Audit.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False,
            func.lower(AuditResponse.response_status) == 'fail'), Audit.plant_id).scalar() or 0
        pass_rate = round((pass_count / total_responses) * 100, 1) if total_responses else 0.0
        audit_health = int(round(pass_rate))

        # ---- Audit health pulse: daily pass-rate over the last 14 days ----
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)
        daily_rows = _plant_ok(session.query(
            func.date(AuditResponse.created_at).label('day'),
            func.sum(case((func.lower(AuditResponse.response_status) == 'pass', 1), else_=0)),
            func.count(AuditResponse.id)
        ).join(Audit, AuditResponse.audit_id == Audit.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False, AuditResponse.created_at >= since
        ).group_by('day').order_by('day'), Audit.plant_id).all()
        pulse_points = [round((p / t) * 100, 1) if t else 0 for _, p, t in daily_rows]
        if not pulse_points:
            pulse_points = [audit_health]
        if len(pulse_points) == 1:
            pulse_points = pulse_points * 2
        pulse_delta = round(pulse_points[-1] - pulse_points[0], 1) if len(pulse_points) > 1 else 0.0

        # ---- Audit portfolio (most recently touched audits) ----
        recent_raw = _plant_ok(session.query(Audit, Plant).join(Plant, Audit.plant_id == Plant.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False
        ).order_by(Audit.created_at.desc()), Audit.plant_id).limit(8).all()

        portfolio = []
        for audit, plant in recent_raw:
            total_assets = session.query(func.count(Asset.id)).filter(
                Asset.plant_id == audit.plant_id, Asset.is_active == True
            ).scalar() or 0
            audited_assets = session.query(func.count(AuditAsset.id)).filter(
                AuditAsset.audit_id == audit.id, AuditAsset.is_audited == True
            ).scalar() or 0
            progress = round((audited_assets / total_assets) * 100) if total_assets else 0

            ranks_here = {r[0] for r in session.query(AuditAsset.rank).filter(AuditAsset.audit_id == audit.id).all()}
            if '1' in ranks_here:
                risk_label = 'Critical'
            elif '2' in ranks_here:
                risk_label = 'High'
            elif '3' in ranks_here:
                risk_label = 'Medium'
            else:
                risk_label = 'Low'

            portfolio.append({
                'id': str(audit.id),
                'plant_name': plant.name,
                'auditor_name': audit.auditor_name or 'Unassigned',
                'audit_type': audit.audit_type or 'General audit',
                'status': audit.status,
                'progress': progress,
                'risk_label': risk_label,
                'next_milestone': audit.audit_date or '—',
            })

        # ---- "Needs attention" — up to 3 concrete, prioritized items ----
        attention = []

        critical_finding = _plant_ok(session.query(AuditAsset, Asset, Plant).join(
            Asset, AuditAsset.asset_id == Asset.id
        ).join(Plant, Asset.plant_id == Plant.id).join(
            Audit, AuditAsset.audit_id == Audit.id
        ).filter(AuditAsset.rank == '1', Audit.site_id == site.site_id, Audit.is_deleted == False),
        Audit.plant_id).order_by(
            AuditAsset.created_at.desc()
        ).first()
        if critical_finding:
            aa, asset, plant = critical_finding
            attention.append({
                'level': 'critical',
                'title': aa.observation or f'Critical condition flagged on {asset.asset_name}',
                'meta': f'{plant.name} · {asset.asset_name}',
                'cta': 'Review finding',
                'href': url_for('audit_equipment', audit_id=str(aa.audit_id), asset_id=str(asset.id)),
            })

        draft_audits = _plant_ok(session.query(Audit, Plant).join(Plant, Audit.plant_id == Plant.id).filter(
            Audit.site_id == site.site_id, Audit.status == 'draft', Audit.is_deleted == False
        ), Audit.plant_id).all()

        best_gap = None
        for audit, plant in draft_audits:
            t_assets = session.query(func.count(Asset.id)).filter(
                Asset.plant_id == audit.plant_id, Asset.is_active == True
            ).scalar() or 0
            audited = session.query(func.count(AuditAsset.id)).filter(
                AuditAsset.audit_id == audit.id, AuditAsset.is_audited == True
            ).scalar() or 0
            gap = t_assets - audited
            if gap > 0 and (best_gap is None or gap > best_gap[0]):
                best_gap = (gap, audit, plant)
        if best_gap:
            gap, audit, plant = best_gap
            attention.append({
                'level': 'high',
                'title': f'{gap} equipment item{"s" if gap != 1 else ""} still need inspection',
                'meta': f'{plant.name} · {audit.audit_type or "Audit"}',
                'cta': 'View equipment',
                'href': url_for('audit_equipment', audit_id=str(audit.id)),
            })

        upcoming = None
        for audit, plant in draft_audits:
            parsed = None
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
                try:
                    parsed = datetime.strptime(audit.audit_date, fmt)
                    break
                except Exception:
                    continue
            if parsed and parsed >= datetime.now(timezone.utc).replace(tzinfo=None) and (upcoming is None or parsed < upcoming[0]):
                upcoming = (parsed, audit, plant)
        if upcoming:
            parsed, audit, plant = upcoming
            days = (parsed - datetime.now(timezone.utc).replace(tzinfo=None)).days
            when = 'today' if days <= 0 else f'in {days} day{"s" if days != 1 else ""}'
            attention.append({
                'level': 'upcoming',
                'title': f'{audit.audit_type or "Audit"} scheduled {when}',
                'meta': plant.name,
                'cta': 'View audit',
                'href': url_for('audit_site', audit_id=str(audit.id)),
            })

        # ---- Observation KPIs / open observations / ageing / site-wise performance ----
        cfg = load_scoring_config()
        obs_rows = _observation_rows(session)
        unfinished = [r for r in obs_rows if r['obs'].status in UNFINISHED]
        n_total = len(obs_rows)
        n_closed = sum(1 for r in obs_rows if r['obs'].status == 'closed')
        obs_kpi = {
            'total': n_total,
            'critical': sum(1 for r in obs_rows if r['obs'].severity == 'critical'),
            'major': sum(1 for r in obs_rows if r['obs'].severity == 'major'),
            'minor': sum(1 for r in obs_rows if r['obs'].severity == 'minor'),
            'unclassified': sum(1 for r in obs_rows if r['obs'].severity not in SEVERITY_LABELS),
            'open': len(unfinished),
            'closed': n_closed,
            'overdue': sum(1 for r in obs_rows if r['overdue']),
            'awaiting_review': sum(1 for r in obs_rows if r['obs'].status == 'submitted'),
            'closure_pct': round(n_closed * 100.0 / n_total, 1) if n_total else 0.0,
        }

        ageing = []
        for label, lo, hi in cfg['ageing_buckets']:
            ageing.append({'label': label, 'count': sum(
                1 for r in unfinished if r['age_days'] >= lo and (hi is None or r['age_days'] <= hi))})

        # Audit score: frozen snapshot for submitted audits (computed on the fly for
        # audits submitted before scoring existed).
        sub_q = session.query(Audit).filter(Audit.site_id == site.site_id, Audit.is_deleted == False,
                                            Audit.status == 'submitted')
        if allowed is not None:
            sub_q = sub_q.filter(Audit.plant_id.in_(allowed))
        scores_by_plant = {}
        for a_ in sub_q.all():
            sc = a_.score_pct if a_.score_pct is not None else score_audit(session, a_.id)['score_pct']
            scores_by_plant.setdefault(a_.plant_id, []).append(sc)
        all_scores = [x for v in scores_by_plant.values() for x in v]
        avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else None
        site_rating = site_rating_for(avg_score, cfg) if avg_score is not None else 'Not rated yet'

        site_perf = []
        plant_q = session.query(Plant).filter(Plant.site_id == site.site_id)
        if allowed is not None:
            plant_q = plant_q.filter(Plant.id.in_(allowed))
        for pl in plant_q.order_by(Plant.name).all():
            sc = scores_by_plant.get(pl.id, [])
            avg = round(sum(sc) / len(sc), 1) if sc else None
            mine = [r for r in unfinished if r['plant'].id == pl.id]
            site_perf.append({
                'name': pl.name, 'audits': len(sc), 'score': avg,
                'rating': site_rating_for(avg, cfg) if avg is not None else '—',
                'open': len(mine), 'overdue': sum(1 for r in mine if r['overdue']),
                'critical_open': sum(1 for r in mine if r['obs'].severity == 'critical'),
            })

        # Open observations, most severe first (list is already sorted that way)
        findings = []
        for r in unfinished[:6]:
            o = r['obs']
            findings.append({
                'level': {'critical': 'critical', 'major': 'high', 'minor': 'medium'}.get(o.severity, 'low'),
                'title': o.observation or r['question'].question_text,
                'meta': f"{r['plant'].name} · {r['equipment']} · {SEVERITY_LABELS.get(o.severity, 'Unclassified')}"
                        f" · {r['status_label']}" + (f" · owner: {o.responsible_person}" if o.responsible_person else ''),
                'age_days': r['age_days'],
                'href': url_for('observation_detail', obs_id=o.id),
            })

        # Observation-driven "needs attention" items go first
        obs_attention = []
        if obs_kpi['overdue']:
            obs_attention.append({'level': 'critical', 'title': f"{obs_kpi['overdue']} observation{'s are' if obs_kpi['overdue'] != 1 else ' is'} past the target closure date",
                                  'meta': 'Follow up with the responsible person/team', 'cta': 'View overdue',
                                  'href': url_for('observations_list', status='overdue')})
        if is_reviewer(current_user) and obs_kpi['awaiting_review']:
            obs_attention.append({'level': 'high', 'title': f"{obs_kpi['awaiting_review']} closure{'s' if obs_kpi['awaiting_review'] != 1 else ''} waiting for your review",
                                  'meta': 'Accept, reject or reopen', 'cta': 'Review now',
                                  'href': url_for('observations_list', status='submitted')})
        attention = (obs_attention + attention)[:5]

        # ---- Pass/fail by equipment type (unchanged) ----
        asset_type_stats = []
        asset_stats_raw = _plant_ok(session.query(
            AssetType.name,
            func.sum(case((func.lower(AuditResponse.response_status) == 'pass', 1), else_=0)).label('pass_count'),
            func.sum(case((func.lower(AuditResponse.response_status) == 'fail', 1), else_=0)).label('fail_count'),
            func.count(AuditResponse.id).label('total')
        ).join(AuditQuestion, AssetType.id == AuditQuestion.asset_type_id).join(
            AuditResponse, AuditResponse.question_id == AuditQuestion.id).join(
            Audit, AuditResponse.audit_id == Audit.id).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False), Audit.plant_id).group_by(AssetType.name).all()
        for name, pcount, fcount, total in asset_stats_raw:
            asset_type_stats.append({
                'name': name,
                'pass_count': int(pcount or 0),
                'fail_count': int(fcount or 0),
                'total': int(total or 0),
            })

        # ---- AI insight, generated deterministically from the aggregates above ----
        worst_type = max(asset_type_stats, key=lambda s: s['fail_count']) if asset_type_stats else None
        if worst_type and worst_type['fail_count'] > 0 and fail_count:
            share = round((worst_type['fail_count'] / fail_count) * 100)
            ai_insight = (f"{fail_count} checks are currently failing across active audits, with {share}% "
                          f"concentrated in {worst_type['name']}.")
        elif fail_count:
            ai_insight = f"{fail_count} checks are currently failing across active audits."
        else:
            ai_insight = "No failing checks recorded yet — audit health is holding steady across active plants."

        if attention:
            ai_recommendation = attention[0]['title']
        else:
            ai_recommendation = "No urgent items right now — good time to advance draft audits."

        recent_audits = [{
            'id': str(audit.id),
            'plant_name': plant.name,
            'audit_date': audit.audit_date,
            'status': audit.status,
        } for audit, plant in recent_raw[:5]]

        now_hour = datetime.now().hour
        return render_template('dashboard.html', total_plants=total_plants, now_hour=now_hour,
                               total_audits=total_audits,
                               draft_audits_count=draft_audits_count,
                               closed_audits_count=closed_audits_count,
                               latest_audit_number=latest_audit_number,
                               pass_rate=pass_rate,
                               fail_count=fail_count,
                               audit_health=audit_health,
                               pulse_points=pulse_points,
                               pulse_delta=pulse_delta,
                               attention=attention,
                               portfolio=portfolio,
                               findings=findings,
                               ai_insight=ai_insight,
                               ai_recommendation=ai_recommendation,
                               recent_audits=recent_audits,
                               asset_type_stats=asset_type_stats,
                               obs_kpi=obs_kpi, ageing=ageing, site_perf=site_perf,
                               avg_score=avg_score, site_rating=site_rating)
    finally:
        session.close()


@app.route('/plants')
@login_required
def plants():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        query = session.query(Plant).filter_by(site_id=site.site_id)
        if allowed is not None:
            query = query.filter(Plant.id.in_(allowed))
        plants_raw = query.order_by(Plant.name).all()
        plants = []
        for plant in plants_raw:
            asset_count = session.query(func.count(Asset.id)).filter(
                Asset.plant_id == plant.id, Asset.site_id == site.site_id).scalar() or 0
            audit_count = session.query(func.count(Audit.id)).filter(
                Audit.plant_id == plant.id, Audit.site_id == site.site_id, Audit.is_deleted == False).scalar() or 0
            plants.append({
                'id': str(plant.id),
                'name': plant.name,
                'location': plant.location,
                'capacity_mw': plant.capacity_mw,
                'asset_count': asset_count,
                'audit_count': audit_count,
            })
        return render_template('plants.html', plants=plants, is_admin=is_admin(current_user))
    finally:
        session.close()


@app.route('/plants', methods=['POST'])
@login_required
@admin_required
def plants_post():
    site = current_site()
    session = Session()
    try:
        name = request.form.get('name', '').strip()
        location = request.form.get('location', '').strip()
        capacity_mw = request.form.get('capacity_mw', '').strip()
        if not name:
            flash('Plant name is required.', 'error')
            return redirect(url_for('plants'))
        plant = Plant(name=name, location=location, capacity_mw=safe_float(capacity_mw), site_id=site.site_id)
        session.add(plant)
        session.commit()
        flash('Plant added successfully.', 'success')
        return redirect(url_for('plants'))
    finally:
        session.close()


PLANT_SPEC_INT_FIELDS = [
    'no_of_blocks', 'central_inverter_count', 'string_inverter_count', 'scb_count',
    'mms_count', 'transformer_count', 'clamps_connectors_count', 'lt_panel_count',
    'ht_panel_count', 'acdb_count', 'ups_db_battery_count', 'lightning_protection_count',
]
PLANT_SPEC_FLOAT_FIELDS = ['dc_capacity', 'ac_capacity']
PLANT_SPEC_TEXT_FIELDS = [
    'building_name', 'roof_type', 'city', 'state',
    'central_inverter_type', 'string_inverter_type', 'mms_type', 'transformer_type',
    'lt_panel_type', 'ht_panel_type', 'dc_earthing', 'ac_earthing',
]


@app.route('/plants/<plant_id>/specs')
@login_required
@admin_required
def plant_specs(plant_id):
    site = current_site()
    session = Session()
    try:
        plant = get_plant_or_404(session, plant_id, site.site_id)
        if not plant:
            flash('Plant not found.', 'error')
            return redirect(url_for('plants'))
        return render_template('plant_specs.html', plant=plant)
    finally:
        session.close()


@app.route('/plants/<plant_id>/specs', methods=['POST'])
@login_required
@admin_required
def plant_specs_post(plant_id):
    """
    Saves this plant's master specification (site details + equipment
    counts) once. Every new audit created against this plant copies these
    values in as its starting point (see audit_new_post) — the auditor no
    longer has to retype the plant's fixed specifications from scratch each
    time, only confirm or adjust anything that's actually changed.
    """
    site = current_site()
    session = Session()
    try:
        plant = get_plant_or_404(session, plant_id, site.site_id)
        if not plant:
            flash('Plant not found.', 'error')
            return redirect(url_for('plants'))

        plant.name = request.form.get('name', plant.name).strip() or plant.name
        plant.location = request.form.get('location', '').strip()
        plant.capacity_mw = safe_float(request.form.get('capacity_mw'))

        for field in PLANT_SPEC_TEXT_FIELDS:
            setattr(plant, field, request.form.get(field, '').strip())
        for field in PLANT_SPEC_INT_FIELDS:
            setattr(plant, field, safe_int(request.form.get(field)))
        for field in PLANT_SPEC_FLOAT_FIELDS:
            setattr(plant, field, safe_float(request.form.get(field)))

        session.commit()
        flash(f'Specifications for "{plant.name}" saved. New audits for this plant will start pre-filled with these values.', 'success')
        return redirect(url_for('plants'))
    finally:
        session.close()


def _hard_delete_audits(session, audit_ids):
    """Permanently removes audits and everything under them (equipment
    responses, photos on disk, corrective actions). Used both for purging
    already-soft-deleted audits when a plant is removed normally, and for
    the admin-only 'force delete' path that also removes live audits."""
    if not audit_ids:
        return
    obs_ids = [o.id for o in session.query(Observation.id).filter(
        Observation.audit_id.in_(audit_ids)).all()]
    if obs_ids:
        for ph in session.query(ObservationPhoto).filter(ObservationPhoto.observation_id.in_(obs_ids)).all():
            old_file = os.path.join(UPLOAD_FOLDER, ph.file_path)
            if os.path.exists(old_file):
                os.remove(old_file)
        session.query(ObservationPhoto).filter(
            ObservationPhoto.observation_id.in_(obs_ids)).delete(synchronize_session=False)
        session.query(ObservationEvent).filter(
            ObservationEvent.observation_id.in_(obs_ids)).delete(synchronize_session=False)
        session.query(Observation).filter(Observation.id.in_(obs_ids)).delete(synchronize_session=False)
    resp_ids = [r.id for r in session.query(AuditResponse.id).filter(
        AuditResponse.audit_id.in_(audit_ids)).all()]
    if resp_ids:
        photos = session.query(AuditResponsePhoto).filter(
            AuditResponsePhoto.audit_response_id.in_(resp_ids)).all()
        for p in photos:
            old_file = os.path.join(UPLOAD_FOLDER, p.file_path)
            if os.path.exists(old_file):
                os.remove(old_file)
        session.query(AuditResponsePhoto).filter(
            AuditResponsePhoto.audit_response_id.in_(resp_ids)).delete(synchronize_session=False)
        session.query(CorrectiveAction).filter(
            CorrectiveAction.audit_response_id.in_(resp_ids)).delete(synchronize_session=False)
    session.query(AuditResponse).filter(
        AuditResponse.audit_id.in_(audit_ids)).delete(synchronize_session=False)
    session.query(AuditSiteDetail).filter(
        AuditSiteDetail.audit_id.in_(audit_ids)).delete(synchronize_session=False)
    session.query(AuditAsset).filter(
        AuditAsset.audit_id.in_(audit_ids)).delete(synchronize_session=False)
    session.query(Audit).filter(
        Audit.id.in_(audit_ids)).delete(synchronize_session=False)


@app.route('/plants/<plant_id>/delete', methods=['POST'])
@login_required
@admin_required
def plant_delete(plant_id):
    site = current_site()
    session = Session()
    try:
        plant_id = _valid_uuid(plant_id)
        if not plant_id:
            flash('Invalid plant ID.', 'error')
            return redirect(url_for('plants'))

        plant = get_plant_or_404(session, plant_id, site.site_id)
        if not plant:
            flash('Plant not found.', 'error')
            return redirect(url_for('plants'))

        # Block deletion if there are LIVE (non-deleted) audits — don't silently
        # destroy active audit history.
        active_count = session.query(func.count(Audit.id)).filter(
            Audit.plant_id == plant.id, Audit.site_id == site.site_id, Audit.is_deleted == False).scalar() or 0
        if active_count > 0:
            flash(f'Cannot delete "{plant.name}" — it has {active_count} audit(s) recorded against it. '
                  f'Delete or reassign those audits first.', 'error')
            return redirect(url_for('plants'))

        # Any remaining audits for this plant are already soft-deleted. The
        # foreign key still blocks a hard delete of the plant while those rows
        # exist, and there's no reason to keep them once the whole plant is
        # being removed — so purge their full dependency chain for real.
        stale_audit_ids = [a.id for a in session.query(Audit.id).filter(
            Audit.plant_id == plant.id, Audit.site_id == site.site_id, Audit.is_deleted == True).all()]
        _hard_delete_audits(session, stale_audit_ids)
        session.commit()

        # Safe to remove now: no audits (live or stale) reference this plant.
        session.query(Asset).filter_by(plant_id=plant.id).delete()
        session.query(UserPlantAssignment).filter_by(plant_id=plant.id).delete()
        session.delete(plant)
        session.commit()
        flash(f'Plant "{plant.name}" deleted.', 'success')
        return redirect(url_for('plants'))
    finally:
        session.close()


@app.route('/plants/<plant_id>/force-delete', methods=['POST'])
@login_required
@admin_required
def plant_force_delete(plant_id):
    """
    Admin-only override for plant_delete's normal safety block: this
    permanently deletes the plant AND every audit (live or already-deleted)
    recorded against it, including responses, photos on disk, and
    corrective actions. There is no undo except restoring from a backup
    taken beforehand.

    Requires the admin to type the plant's exact name as a confirmation —
    this isn't extra security theatre, it's specifically to make this
    difficult to trigger by an accidental click, since the normal delete
    button's confirm() dialog is easy to click through without reading.
    """
    site = current_site()
    session = Session()
    try:
        plant_id = _valid_uuid(plant_id)
        if not plant_id:
            flash('Invalid plant ID.', 'error')
            return redirect(url_for('plants'))

        plant = get_plant_or_404(session, plant_id, site.site_id)
        if not plant:
            flash('Plant not found.', 'error')
            return redirect(url_for('plants'))

        confirm_name = (request.form.get('confirm_name') or '').strip()
        if confirm_name != plant.name:
            flash(f'Force delete cancelled: the typed name did not exactly match "{plant.name}".', 'error')
            return redirect(url_for('plants'))

        all_audit_ids = [a.id for a in session.query(Audit.id).filter(
            Audit.plant_id == plant.id, Audit.site_id == site.site_id).all()]
        audit_count = len(all_audit_ids)
        _hard_delete_audits(session, all_audit_ids)
        session.query(Asset).filter_by(plant_id=plant.id).delete()
        session.query(UserPlantAssignment).filter_by(plant_id=plant.id).delete()
        session.delete(plant)
        session.commit()
        flash(f'Plant "{plant.name}" and {audit_count} audit(s) permanently deleted.', 'success')
        return redirect(url_for('plants'))
    finally:
        session.close()


@app.route('/audits')
@login_required
def audits():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        query = session.query(Audit).filter(
            Audit.site_id == site.site_id, Audit.is_deleted == False)
        if allowed is not None:
            query = query.filter(Audit.plant_id.in_(allowed))
        audits_raw = query.order_by(Audit.created_at.desc()).all()
        audits = []
        for audit in audits_raw:
            plant = session.query(Plant).filter_by(id=audit.plant_id, site_id=site.site_id).first()
            audits.append({
                'id': str(audit.id),
                'audit_number': audit.audit_number,
                'plant_name': plant.name if plant else 'Unknown',
                'audit_date': audit.audit_date,
                'audit_type': audit.audit_type,
                'auditor_name': audit.auditor_name,
                'status': audit.status,
                'created_at': audit.created_at,
            })
        return render_template('all_audits.html', audits=audits)
    finally:
        session.close()


@app.route('/audit/new')
@login_required
def audit_new():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        query = session.query(Plant).filter_by(site_id=site.site_id)
        if allowed is not None:
            query = query.filter(Plant.id.in_(allowed))
        plants = query.order_by(Plant.name).all()
        return render_template('audit_basic.html', plants=plants)
    finally:
        session.close()


@app.route('/audit/new', methods=['POST'])
@login_required
def audit_new_post():
    site = current_site()
    session = Session()
    try:
        plant_id = _valid_uuid(request.form.get('plant_id'))
        audit_date = request.form.get('audit_date')
        audit_time = request.form.get('audit_time')
        building_name = request.form.get('building_name', '')
        capacity_mw = request.form.get('capacity_mw', '')
        roof_type = request.form.get('roof_type', '')
        city = request.form.get('city', '')
        state = request.form.get('state', '')
        audit_type = request.form.get('audit_type', '')
        auditor_name = request.form.get('auditor_name', '')
        auditee_name = request.form.get('auditee_name', '')
        asset_id_ref = request.form.get('asset_id_ref', '')

        if not plant_id or not audit_date or not auditor_name:
            flash('Plant, date, and auditor name are required.', 'error')
            return redirect(url_for('audit_new'))

        # Plant must belong to this site — prevents posting a plant_id that
        # was guessed/copied from elsewhere.
        plant = get_plant_or_404(session, plant_id, site.site_id)
        if not plant:
            flash('Selected plant was not found for this site.', 'error')
            return redirect(url_for('audit_new'))

        # A non-admin can only create audits for a plant they're assigned to
        # — this is the same rule that get_audit_or_404 enforces for
        # reading an existing audit, applied here at creation time too.
        if not can_access_plant(session, current_user, plant.id):
            flash('You are not assigned to this plant.', 'error')
            return redirect(url_for('audit_new'))

        try:
            year = datetime.strptime(audit_date, '%Y-%m-%d').year
        except Exception:
            year = datetime.now(timezone.utc).replace(tzinfo=None).year

        audit = Audit(
            plant_id=plant.id,
            site_id=site.site_id,
            audit_number=next_audit_number(session, site.site_id, year),
            audit_date=audit_date,
            audit_time=audit_time,
            building_name=building_name,
            capacity_mw=capacity_mw,
            roof_type=roof_type,
            city=city,
            state=state,
            audit_type=audit_type,
            auditor_name=auditor_name,
            auditee_name=auditee_name,
            asset_id_ref=asset_id_ref,
        )
        session.add(audit)
        session.flush()  # need audit.id before creating the linked site-detail row

        # Pre-fill this audit's site-detail row from the plant's stored
        # master specification, so the auditor doesn't retype equipment
        # counts/types from scratch every time — still fully editable
        # afterward on the site-details page if something's actually changed.
        site_detail = AuditSiteDetail(
            audit_id=audit.id,
            no_of_blocks=plant.no_of_blocks,
            dc_capacity=plant.dc_capacity,
            ac_capacity=plant.ac_capacity,
            central_inverter_type=plant.central_inverter_type,
            central_inverter_count=plant.central_inverter_count,
            string_inverter_type=plant.string_inverter_type,
            string_inverter_count=plant.string_inverter_count,
            scb_count=plant.scb_count,
            mms_type=plant.mms_type,
            mms_count=plant.mms_count,
            transformer_type=plant.transformer_type,
            transformer_count=plant.transformer_count,
            clamps_connectors_count=plant.clamps_connectors_count,
            lt_panel_type=plant.lt_panel_type,
            lt_panel_count=plant.lt_panel_count,
            ht_panel_type=plant.ht_panel_type,
            ht_panel_count=plant.ht_panel_count,
            acdb_count=plant.acdb_count,
            ups_db_battery_count=plant.ups_db_battery_count,
            lightning_protection_count=plant.lightning_protection_count,
            dc_earthing=plant.dc_earthing,
            ac_earthing=plant.ac_earthing,
        )
        session.add(site_detail)
        session.commit()
        return redirect(url_for('audit_site', audit_id=str(audit.id)))
    finally:
        session.close()


@app.route('/audit/<audit_id>/site')
@login_required
def audit_site(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        plant = session.query(Plant).filter_by(id=audit.plant_id).first()
        site_detail = session.query(AuditSiteDetail).filter_by(audit_id=audit.id).first()
        return render_template('audit_site.html', audit=audit, plant=plant, site_detail=site_detail)
    finally:
        session.close()


@app.route('/audit/<audit_id>/site', methods=['POST'])
@login_required
def audit_site_post(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))

        values = {
            'no_of_blocks': safe_int(request.form.get('no_of_blocks')),
            'dc_capacity': safe_float(request.form.get('dc_capacity')),
            'ac_capacity': safe_float(request.form.get('ac_capacity')),
            'central_inverter_type': request.form.get('central_inverter_type', ''),
            'central_inverter_count': safe_int(request.form.get('central_inverter_count')),
            'string_inverter_type': request.form.get('string_inverter_type', ''),
            'string_inverter_count': safe_int(request.form.get('string_inverter_count')),
            'scb_count': safe_int(request.form.get('scb_count')),
            'mms_type': request.form.get('mms_type', ''),
            'mms_count': safe_int(request.form.get('mms_count')),
            'transformer_type': request.form.get('transformer_type', ''),
            'transformer_count': safe_int(request.form.get('transformer_count')),
            'clamps_connectors_count': safe_int(request.form.get('clamps_connectors_count')),
            'lt_panel_type': request.form.get('lt_panel_type', ''),
            'lt_panel_count': safe_int(request.form.get('lt_panel_count')),
            'ht_panel_type': request.form.get('ht_panel_type', ''),
            'ht_panel_count': safe_int(request.form.get('ht_panel_count')),
            'acdb_count': safe_int(request.form.get('acdb_count')),
            'ups_db_battery_count': safe_int(request.form.get('ups_db_battery_count')),
            'lightning_protection_count': safe_int(request.form.get('lightning_protection_count')),
            'dc_earthing': request.form.get('dc_earthing', ''),
            'ac_earthing': request.form.get('ac_earthing', ''),
        }

        site_detail = session.query(AuditSiteDetail).filter_by(audit_id=audit.id).first()
        if site_detail:
            for key, value in values.items():
                setattr(site_detail, key, value)
        else:
            site_detail = AuditSiteDetail(audit_id=audit.id, **values)
            session.add(site_detail)
        session.commit()

        flash('Site details saved successfully.', 'success')
        return redirect(url_for('audit_equipment', audit_id=str(audit.id)))
    finally:
        session.close()


@app.route('/audit/<audit_id>/equipment')
@login_required
def audit_equipment(audit_id):
    site = current_site()
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        plant = session.query(Plant).filter_by(id=audit.plant_id, site_id=site.site_id).first()
        readonly = audit.status == 'submitted'

        # Get all asset types for dropdown
        asset_types = session.query(AssetType).order_by(AssetType.name).all()
        asset_types_list = [{'id': at.id, 'name': at.name} for at in asset_types]

        assets_raw = session.query(Asset, AssetType).join(AssetType).filter(
            Asset.plant_id == audit.plant_id,
            Asset.site_id == site.site_id,
            Asset.is_active == True
        ).order_by(AssetType.name, Asset.asset_name).all()
        saved_asset_ids = {
            str(item.asset_id) for item in session.query(AuditAsset).filter_by(audit_id=audit.id).all()
        }
        assets = []
        # Keep equipment grouped by the actual AssetType ID, not by the
        # equipment display name. This ensures custom equipment added with
        # an existing Equipment Type always appears under the same header.
        asset_groups_map = {}
        for asset, asset_type in assets_raw:
            item = {
                'id': str(asset.id),
                'asset_name': asset.asset_name,
                'asset_type_id': asset.asset_type_id,
                'asset_type_name': asset_type.name,
                'is_saved': str(asset.id) in saved_asset_ids,
            }
            assets.append(item)
            group = asset_groups_map.setdefault(asset_type.id, {
                'id': asset_type.id,
                'name': asset_type.name,
                'assets': [],
            })
            group['assets'].append(item)
        asset_groups = list(asset_groups_map.values())
        asset_groups.sort(key=lambda g: (g['name'] or '').strip().casefold())
        for group in asset_groups:
            group['assets'].sort(key=lambda a: (a['asset_name'] or '').strip().casefold())

        selected_asset_id = request.args.get('asset_id')
        selected_asset = None
        asset_audit = None
        grouped_questions = []
        responses_map = {}
        photos_map = {}
        obs_map = {}

        if selected_asset_id:
            selected_asset = get_asset_or_404(session, selected_asset_id, site.site_id)
            # An asset that belongs to this site but a DIFFERENT plant is
            # not valid for this audit either — the asset must belong to
            # the same plant the audit was created against.
            if selected_asset and str(selected_asset.plant_id) != str(audit.plant_id):
                selected_asset = None

            if selected_asset:
                asset_audit = session.query(AuditAsset).filter_by(audit_id=audit.id, asset_id=selected_asset.id).first()
                responses_map, photos_map, obs_map = _load_checkpoint_state(session, audit, selected_asset.id)
                answered_ids = [int(k) for k in responses_map]
                # Retired checkpoints are hidden from new work but still shown
                # where an audit already answered them (historical integrity).
                questions_raw = session.query(AuditQuestion, AuditSection).join(AuditSection).filter(
                    AuditQuestion.asset_type_id == selected_asset.asset_type_id,
                    or_(AuditQuestion.is_active.isnot(False), AuditQuestion.id.in_(answered_ids or [-1]))
                ).order_by(AuditSection.section_order, AuditQuestion.question_order, AuditQuestion.id).all()

                section_map = {}
                for question, section in questions_raw:
                    section_name = section.name
                    if section_name not in section_map:
                        section_map[section_name] = []
                    section_map[section_name].append(question)
                grouped_questions = [{'section_name': name, 'questions': section_map[name]} for name in section_map]

        selected_asset_id = str(selected_asset.id) if selected_asset else None
        return render_template('audit_equipment.html',
                               audit=audit,
                               plant=plant,
                               readonly=readonly,
                               assets=assets,
                               asset_groups=asset_groups,
                               asset_types=asset_types_list,
                               selected_asset=selected_asset,
                               selected_asset_id=selected_asset_id,
                               asset_audit=asset_audit,
                               grouped_questions=grouped_questions,
                               responses_map=responses_map,
                               photos_map=photos_map,
                               obs_map=obs_map,
                               selected_type_name=next((a['asset_type_name'] for a in assets
                                                        if a['id'] == selected_asset_id), ''),
                               today=today_str())
    finally:
        session.close()


@app.route('/audit/<audit_id>/add-equipment', methods=['POST'])
@login_required
def add_equipment(audit_id):
    site = current_site()
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            return {'error': 'Audit not found'}, 404
        
        if audit.status == 'submitted':
            return {'error': 'Cannot add equipment to submitted audit'}, 403
        
        equipment_name = request.form.get('equipment_name', '').strip()
        asset_type_id = request.form.get('asset_type_id', '')
        
        if not equipment_name or not asset_type_id:
            return {'error': 'Equipment name and type are required'}, 400
        
        try:
            asset_type_id = int(asset_type_id)
        except ValueError:
            return {'error': 'Invalid asset type'}, 400
        
        # Verify asset type exists
        asset_type = session.query(AssetType).filter_by(id=asset_type_id).first()
        if not asset_type:
            return {'error': 'Asset type not found'}, 404
        
        # Create new asset
        new_asset = Asset(
            plant_id=audit.plant_id,
            asset_type_id=asset_type_id,
            asset_name=equipment_name,
            is_active=True,
            site_id=site.site_id,
        )
        session.add(new_asset)
        session.commit()
        
        return {
            'success': True,
            'asset_id': str(new_asset.id),
            'asset_name': equipment_name,
            'asset_type_name': asset_type.name,
            'redirect_url': url_for('audit_equipment', audit_id=audit_id, asset_id=str(new_asset.id))
        }
    except Exception as e:
        session.rollback()
        return {'error': str(e)}, 500
    finally:
        session.close()


@app.route('/audit/<audit_id>/equipment/photo/upload', methods=['POST'])
@login_required
def audit_equipment_photo_upload(audit_id):
    """Persist a single captured equipment photo immediately.

    The client sends one photo per request, so the server never has to buffer a
    batch of high-resolution photos or wait for the Save Asset Audit button.
    """
    # Reject oversized single-photo requests before Flask parses the multipart
    # body. A little overhead is allowed for fields and multipart boundaries.
    if request.content_length and request.content_length > MAX_PHOTO_BYTES + 2 * 1024 * 1024:
        return jsonify({'ok': False, 'error': 'Each photo must be 50 MB or smaller.'}), 413

    session = Session()
    stored_path = None
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            return jsonify({'ok': False, 'error': 'Audit not found or access denied.'}), 404
        if audit.status == 'submitted':
            return jsonify({'ok': False, 'error': 'This audit is submitted and locked.'}), 409

        asset_id = _valid_uuid(request.form.get('asset_id'))
        question_id = safe_int(request.form.get('question_id'))
        status = (request.form.get('response_status') or '').lower()
        upload = request.files.get('photo')
        client_photo_id = _valid_uuid(request.form.get('client_photo_id'))
        if not client_photo_id:
            return jsonify({'ok': False, 'error': 'Photo queue ID is missing; please retry the capture.'}), 400
        if not asset_id or not question_id:
            return jsonify({'ok': False, 'error': 'Equipment or checkpoint is missing.'}), 400
        if status not in VALID_RESPONSES:
            return jsonify({'ok': False, 'error': 'Select C, NC, or NA before capturing a photo.'}), 400
        if not upload or not upload.filename or not allowed_file(upload.filename):
            return jsonify({'ok': False, 'error': 'Choose a valid image file.'}), 400

        asset_obj = get_asset_or_404(session, asset_id, current_site().site_id)
        if not asset_obj or str(asset_obj.plant_id) != str(audit.plant_id):
            return jsonify({'ok': False, 'error': 'Equipment does not belong to this audit.'}), 403
        question = session.query(AuditQuestion).filter_by(
            id=question_id, asset_type_id=asset_obj.asset_type_id).first()
        if not question:
            return jsonify({'ok': False, 'error': 'Checkpoint does not belong to this equipment type.'}), 400

        try:
            upload.stream.seek(0, os.SEEK_END)
            photo_size = upload.stream.tell()
            upload.stream.seek(0)
        except Exception:
            photo_size = 0
        if photo_size > MAX_PHOTO_BYTES:
            return jsonify({'ok': False, 'error': 'Each photo must be 50 MB or smaller.'}), 413

        response = _get_response(session, audit.id, asset_id, question_id)
        if response is None:
            response = AuditResponse(audit_id=audit.id, asset_id=asset_id,
                                     question_id=question_id, response_status=status,
                                     remarks=request.form.get('remarks', ''))
            session.add(response)
        else:
            response.response_status = status
            response.remarks = request.form.get('remarks', response.remarks or '')
        response.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        response.responded_by = _actor()
        session.flush()
        sync_observation(session, audit, response, current_site().site_id, _actor(), {
            'observation': None,
            'severity': None,  # severity is deliberately not collected during checkpoint/photo capture
            'responsible_person': None,
            'target_date': None,
            'obs_remarks': None,
        })

        # Idempotency: if the browser lost the response after the DB commit, a
        # retry with the same client UUID returns the existing photo, not a duplicate.
        client_prefix = f'q{question_id}_{client_photo_id.replace("-", "")[:20]}'
        prior = session.query(AuditResponsePhoto).filter(
            AuditResponsePhoto.audit_response_id == response.id,
            AuditResponsePhoto.file_path.like(f'%/{client_prefix}_%')).first()
        if prior:
            session.commit()
            return jsonify({'ok': True, 'duplicate': True, 'photo_id': str(prior.id),
                            'photo_url': url_for('uploaded_file', filename=prior.file_path),
                            'message': 'Photo was already saved.'})

        existing = session.query(AuditResponsePhoto).filter_by(
            audit_response_id=response.id).count()
        if existing >= MAX_PHOTOS_PER_QUESTION:
            session.rollback()
            return jsonify({'ok': False, 'error': 'Maximum 1000 photos already captured for this checkpoint.'}), 409

        # Keep generated photo paths below Windows' traditional MAX_PATH limit.
        # The full audit + asset UUID directories combined with the project path
        # under OneDrive can exceed 260 characters, causing FileNotFoundError
        # even when os.makedirs succeeds. Photo paths are DB-relative, so compact
        # stable UUID prefixes are safe and keep photos grouped by audit/equipment.
        short_audit_dir = str(audit.id).replace('-', '')[:12]
        short_asset_dir = str(asset_id).replace('-', '')[:12]
        info = _store_photo(upload, f'{short_audit_dir}/{short_asset_dir}', client_prefix, request.form, question_id)
        if not info:
            session.rollback()
            return jsonify({'ok': False, 'error': 'Photo could not be stored. Check file type and 50 MB limit.'}), 400
        stored_path = os.path.join(UPLOAD_FOLDER, info['file_path'])
        photo = AuditResponsePhoto(audit_response_id=response.id, **info)
        session.add(photo)
        # A photo is durable before we tell the browser the upload succeeded.
        session.commit()
        return jsonify({
            'ok': True,
            'photo_id': str(photo.id),
            'photo_url': url_for('uploaded_file', filename=photo.file_path),
            'uploaded_count': existing + 1,
            'message': 'Photo uploaded and saved.'
        })
    except Exception as exc:
        session.rollback()
        if stored_path and os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass
        app.logger.exception('Immediate equipment photo upload failed')
        return jsonify({'ok': False, 'error': 'Upload failed. Please retry this photo.'}), 500
    finally:
        session.close()


@app.route('/audit/<audit_id>/equipment/save', methods=['POST'])
@login_required
def audit_equipment_save(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        if audit.status == 'submitted':
            flash('This audit is submitted and locked.', 'error')
            return redirect(url_for('audit_equipment', audit_id=audit_id))

        asset_id = request.form.get('asset_id')
        location = request.form.get('location', '')
        rank = request.form.get('rank', 'N/A')
        observation = request.form.get('observation', '')
        remarks = request.form.get('remarks', '')
        is_audited = request.form.get('is_audited', 'no') == 'yes'

        try:
            uuid.UUID(asset_id)  # format-validate only; keep asset_id as a plain string
            asset_uuid = str(asset_id)
        except Exception:
            flash('Selected asset is invalid.', 'error')
            return redirect(url_for('audit_equipment', audit_id=audit_id))

        asset_obj = get_asset_or_404(session, asset_uuid, current_site().site_id)
        if not asset_obj or str(asset_obj.plant_id) != str(audit.plant_id):
            flash('Selected equipment does not belong to this audit\'s plant.', 'error')
            return redirect(url_for('audit_equipment', audit_id=audit_id))

        existing_aa = session.query(AuditAsset).filter_by(
            audit_id=audit.id,
            asset_id=asset_uuid
        ).first()
        if existing_aa:
            existing_aa.location = location
            existing_aa.rank = rank
            existing_aa.observation = observation
            existing_aa.remarks = remarks
            existing_aa.is_audited = is_audited
        else:
            aa = AuditAsset(
                audit_id=audit.id,
                asset_id=asset_uuid,
                location=location,
                rank=rank,
                observation=observation,
                remarks=remarks,
                is_audited=is_audited
            )
            session.add(aa)
        session.commit()

        valid_qids = {r[0] for r in session.query(AuditQuestion.id).filter(
            AuditQuestion.asset_type_id == asset_obj.asset_type_id).all()}
        saved, skipped = _save_checkpoints(session, audit, asset_uuid, valid_qids,
                                           request.form, request.files, current_site().site_id)
        if skipped:
            flash(f'{skipped} photo(s) were not saved: choose Complied / Not Complied / Not Applicable '
                  'for that checkpoint first, then add the photo.', 'error')

        flash('Equipment audit saved successfully.', 'success')
        return redirect(url_for('audit_equipment', audit_id=audit_id, asset_id=asset_id))
    finally:
        session.close()


def _safety_section(session):
    return session.query(AuditSection).filter_by(name=SAFETY_SECTION_NAME).first()


def _safety_questions(session, answered_ids=()):
    section = _safety_section(session)
    if section is None:
        return []
    return session.query(AuditQuestion).filter(
        AuditQuestion.section_id == section.id,
        or_(AuditQuestion.is_active.isnot(False), AuditQuestion.id.in_(list(answered_ids) or [-1]))
    ).order_by(AuditQuestion.question_order, AuditQuestion.id).all()


@app.route('/audit/<audit_id>/safety')
@login_required
def audit_safety(audit_id):
    site = current_site()
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        plant = session.query(Plant).filter_by(id=audit.plant_id, site_id=site.site_id).first()
        responses_map, photos_map, obs_map = _load_checkpoint_state(session, audit, None)
        questions = _safety_questions(session, [int(k) for k in responses_map])
        return render_template('audit_safety.html', audit=audit, plant=plant,
                               readonly=audit.status == 'submitted', questions=questions,
                               responses_map=responses_map, photos_map=photos_map, obs_map=obs_map,
                               today=today_str())
    finally:
        session.close()


@app.route('/audit/<audit_id>/section/photo/upload', methods=['POST'])
@login_required
def audit_section_photo_upload(audit_id):
    """Immediately save one Safety or Civil photo, with durable client retry IDs."""
    if request.content_length and request.content_length > MAX_PHOTO_BYTES + 2 * 1024 * 1024:
        return jsonify({'ok': False, 'error': 'Each photo must be 50 MB or smaller.'}), 413
    section_name = (request.form.get('section') or '').strip().lower()
    if section_name not in ('safety', 'civil'):
        return jsonify({'ok': False, 'error': 'Invalid audit section.'}), 400
    session = Session()
    stored_path = None
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            return jsonify({'ok': False, 'error': 'Audit not found or access denied.'}), 404
        if audit.status == 'submitted':
            return jsonify({'ok': False, 'error': 'This audit is submitted and locked.'}), 409
        qid = safe_int(request.form.get('question_id'))
        status = (request.form.get('response_status') or '').lower()
        upload = request.files.get('photo')
        client_photo_id = _valid_uuid(request.form.get('client_photo_id'))
        if not client_photo_id:
            return jsonify({'ok': False, 'error': 'Photo queue ID is missing; please retry.'}), 400
        if not qid or status not in VALID_RESPONSES:
            return jsonify({'ok': False, 'error': 'Select C, NC or NA before capturing a photo.'}), 400
        section = _safety_section(session) if section_name == 'safety' else _civil_section(session)
        if not section or not session.query(AuditQuestion.id).filter_by(id=qid, section_id=section.id).first():
            return jsonify({'ok': False, 'error': 'Checkpoint does not belong to this audit section.'}), 400
        if not upload or not upload.filename or not allowed_file(upload.filename):
            return jsonify({'ok': False, 'error': 'Choose a valid image file.'}), 400
        try:
            upload.stream.seek(0, os.SEEK_END); photo_size = upload.stream.tell(); upload.stream.seek(0)
        except Exception:
            photo_size = 0
        if photo_size > MAX_PHOTO_BYTES:
            return jsonify({'ok': False, 'error': 'Each photo must be 50 MB or smaller.'}), 413

        resp = _get_response(session, audit.id, None, qid)
        if resp is None:
            resp = AuditResponse(audit_id=audit.id, asset_id=None, question_id=qid,
                                 response_status=status, remarks=request.form.get('remarks', ''))
            session.add(resp)
        else:
            resp.response_status = status
            resp.remarks = request.form.get('remarks', resp.remarks or '')
        resp.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        resp.responded_by = _actor()
        session.flush()
        sync_observation(session, audit, resp, current_site().site_id, _actor(), {
            'observation': None, 'severity': None, 'responsible_person': None,
            'target_date': None, 'obs_remarks': None,
        })
        client_prefix = f'q{qid}_{client_photo_id.replace("-", "")[:20]}'
        prior = session.query(AuditResponsePhoto).filter(
            AuditResponsePhoto.audit_response_id == resp.id,
            AuditResponsePhoto.file_path.like(f'%/{client_prefix}_%')).first()
        if prior:
            session.commit()
            return jsonify({'ok': True, 'duplicate': True, 'photo_id': str(prior.id),
                            'photo_url': url_for('uploaded_file', filename=prior.file_path)})
        existing = session.query(AuditResponsePhoto).filter_by(audit_response_id=resp.id).count()
        if existing >= MAX_PHOTOS_PER_QUESTION:
            session.rollback()
            return jsonify({'ok': False, 'error': 'Maximum 1000 photos reached for this checkpoint.'}), 409
        short_audit_dir = str(audit.id).replace('-', '')[:12]
        section_dir = 'safety' if section_name == 'safety' else 'civil'
        info = _store_photo(upload, f'{short_audit_dir}/{section_dir}', client_prefix, request.form, qid)
        if not info:
            session.rollback()
            return jsonify({'ok': False, 'error': 'Photo could not be stored. Check the file and 50 MB limit.'}), 400
        stored_path = os.path.join(UPLOAD_FOLDER, info['file_path'])
        photo = AuditResponsePhoto(audit_response_id=resp.id, **info)
        session.add(photo); session.commit()
        return jsonify({'ok': True, 'photo_id': str(photo.id),
                        'photo_url': url_for('uploaded_file', filename=photo.file_path),
                        'uploaded_count': existing + 1, 'message': 'Photo uploaded and saved.'})
    except Exception:
        session.rollback()
        if stored_path and os.path.exists(stored_path):
            try: os.remove(stored_path)
            except OSError: pass
        app.logger.exception('Immediate safety/civil photo upload failed')
        return jsonify({'ok': False, 'error': 'Upload failed. Photo remains queued for retry.'}), 500
    finally:
        session.close()


@app.route('/audit/<audit_id>/safety/save', methods=['POST'])
@login_required
def audit_safety_save(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        if audit.status == 'submitted':
            flash('This audit is submitted and locked.', 'error')
            return redirect(url_for('audit_safety', audit_id=audit_id))
        section = _safety_section(session)
        valid_qids = {r[0] for r in session.query(AuditQuestion.id).filter(
            AuditQuestion.section_id == section.id).all()} if section else set()
        saved, skipped = _save_checkpoints(session, audit, None, valid_qids,
                                           request.form, request.files, current_site().site_id)
        if skipped:
            flash(f'{skipped} photo(s) were not saved: choose a response for that checkpoint first, '
                  'then add the photo.', 'error')
        flash(f'Safety audit saved ({saved} checkpoint{"s" if saved != 1 else ""} updated).', 'success')
        return redirect(url_for('audit_safety', audit_id=audit_id))
    finally:
        session.close()



def _civil_section(session):
    return session.query(AuditSection).filter_by(name=CIVIL_SECTION_NAME).first()


def _civil_questions(session, answered_ids=()):
    section = _civil_section(session)
    if section is None:
        return []
    return session.query(AuditQuestion).filter(
        AuditQuestion.section_id == section.id,
        or_(AuditQuestion.is_active.isnot(False), AuditQuestion.id.in_(list(answered_ids) or [-1]))
    ).order_by(AuditQuestion.question_order, AuditQuestion.id).all()


@app.route('/audit/<audit_id>/civil')
@login_required
def audit_civil(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        plant = session.query(Plant).filter_by(id=audit.plant_id, site_id=current_site().site_id).first()
        responses_map, photos_map, obs_map = _load_checkpoint_state(session, audit, None)
        questions = _civil_questions(session, [int(k) for k in responses_map])
        return render_template('audit_civil.html', audit=audit, plant=plant,
                               readonly=audit.status == 'submitted', questions=questions,
                               responses_map=responses_map, photos_map=photos_map, obs_map=obs_map,
                               today=today_str())
    finally:
        session.close()


@app.route('/audit/<audit_id>/civil/save', methods=['POST'])
@login_required
def audit_civil_save(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        if audit.status == 'submitted':
            flash('This audit is submitted and locked.', 'error')
            return redirect(url_for('audit_civil', audit_id=audit_id))
        section = _civil_section(session)
        valid_qids = {r[0] for r in session.query(AuditQuestion.id).filter(
            AuditQuestion.section_id == section.id).all()} if section else set()
        saved, skipped = _save_checkpoints(session, audit, None, valid_qids,
                                           request.form, request.files, current_site().site_id)
        if skipped:
            flash(f'{skipped} photo(s) were not saved: choose a response for that checkpoint first, '
                  'then add the photo.', 'error')
        flash(f'Civil / Infrastructure audit saved ({saved} checkpoint{"s" if saved != 1 else ""} updated).',
              'success')
        return redirect(url_for('audit_civil', audit_id=audit_id))
    finally:
        session.close()


@app.route('/audit/<audit_id>/photo/<photo_id>/delete', methods=['POST'])
@login_required
def delete_response_photo(audit_id, photo_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            return {'success': False, 'error': 'Audit not found'}, 404
        if audit.status == 'submitted':
            return {'success': False, 'error': 'Audit is submitted and locked'}, 403

        try:
            uuid.UUID(photo_id)  # format-validate only; keep as plain string
            photo_uuid = str(photo_id)
        except Exception:
            return {'success': False, 'error': 'Invalid photo id'}, 400

        photo = session.query(AuditResponsePhoto).filter_by(id=photo_uuid).first()
        if not photo:
            return {'success': False, 'error': 'Photo not found'}, 404

        # Confirm this photo actually belongs to a response under this audit
        resp_obj = session.query(AuditResponse).filter_by(id=photo.audit_response_id).first()
        if not resp_obj or resp_obj.audit_id != audit.id:
            return {'success': False, 'error': 'Photo does not belong to this audit'}, 403

        old_file = os.path.join(UPLOAD_FOLDER, photo.file_path)
        if os.path.exists(old_file):
            os.remove(old_file)
        session.delete(photo)
        session.commit()
        return {'success': True}
    finally:
        session.close()


@app.route('/audit/<audit_id>/submit')
@login_required
def audit_submit(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        plant = session.query(Plant).filter_by(id=audit.plant_id).first()
        audit_assets_raw = session.query(AuditAsset, Asset, AssetType).join(
            Asset, AuditAsset.asset_id == Asset.id).join(
            AssetType, Asset.asset_type_id == AssetType.id).filter(
            AuditAsset.audit_id == audit.id).all()
        assets = []
        for aa, asset, atype in audit_assets_raw:
            assets.append({
                'asset_name': asset.asset_name,
                'asset_type_name': atype.name,
                'location': aa.location,
                'rank': aa.rank,
                'is_audited': aa.is_audited,
                'observation': aa.observation,
                'remarks': aa.remarks,
            })
        result = score_audit(session, audit.id)
        if audit.status == 'submitted' and audit.score_pct is not None:
            result['score_pct'], result['compliance_pct'], result['rating'] = (
                audit.score_pct, audit.compliance_pct, audit.site_rating or result['rating'])
        incomplete = incomplete_observations(session, audit.id)
        obs_rows = _observation_rows(session, audit_id=audit.id)
        return render_template('audit_submit.html', audit=audit, plant=plant, assets=assets,
                               total_responses=result['complied'] + result['not_complied'] + result['not_applicable'],
                               pass_count=result['complied'], fail_count=result['not_complied'],
                               na_count=result['not_applicable'], result=result,
                               incomplete=incomplete, obs_rows=obs_rows)
    finally:
        session.close()


@app.route('/audit/<audit_id>/submit', methods=['POST'])
@login_required
def audit_submit_post(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        if audit.status == 'submitted':
            flash('Audit already submitted.', 'error')
            return redirect(url_for('audits'))

        # NC severity and all follow-up details are optional at submission;
        # teams can classify and complete them later in the Observations workflow.
        result = score_audit(session, audit.id)
        audit.status = 'submitted'
        audit.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        audit.compliance_pct = result['compliance_pct']
        audit.score_pct = result['score_pct']
        audit.site_rating = result['rating']
        audit.score_detail = json.dumps(result)
        session.commit()

        # Auto-generate the report on submission. A report failure must never
        # undo a submission - it can always be regenerated from the Reports page.
        msg, cat = 'Audit submitted successfully. It is now locked.', 'success'
        try:
            site = current_site()
            pdf_path, error = generate_pdf(str(audit.id), site_id=site.site_id if site else None)
            if pdf_path:
                msg += ' The report has been generated.'
            else:
                msg += f' (Report could not be generated automatically: {error}. You can retry from Reports.)'
        except Exception as exc:  # pragma: no cover - defensive
            msg += f' (Report could not be generated automatically: {exc}. You can retry from Reports.)'
        flash(msg, cat)
        return redirect(url_for('audit_submit', audit_id=audit_id))
    finally:
        session.close()


@app.route('/audit/<audit_id>/report')
@login_required
def audit_report(audit_id):
    # Validate both site scope and plant assignment before invoking the PDF
    # generator. generate_pdf's site_id check alone is insufficient for
    # Engineer/User accounts restricted to assigned plants.
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            return '', 404
        site = current_site()
        pdf_path, error = generate_pdf(
            str(audit.id), site_id=site.site_id if site else None
        )
    finally:
        session.close()

    if not pdf_path:
        flash(f'Could not generate report: {error or "unknown error"}', 'error')
        return redirect(url_for('audits'))
    if not os.path.exists(pdf_path):
        flash('Report file went missing right after being generated. This is usually '
              'antivirus/security software deleting the new PDF — try moving this project '
              'out of Downloads, or add an exclusion for the "uploads" folder.', 'error')
        return redirect(url_for('audits'))
    try:
        return send_file(pdf_path, as_attachment=True,
                         download_name=f'solar_audit_report_{str(audit_id)[:8]}.pdf',
                         mimetype='application/pdf')
    except FileNotFoundError:
        flash('Report file went missing right after being generated. This is usually '
              'antivirus/security software deleting the new PDF — try moving this project '
              'out of Downloads, or add an exclusion for the "uploads" folder.', 'error')
        return redirect(url_for('audits'))


@app.route('/audit/<audit_id>/delete', methods=['POST'])
@login_required
@admin_required
def audit_delete(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))
        
        audit_date = audit.audit_date
        audit.is_deleted = True
        session.commit()
        
        flash(f'Audit from {audit_date} has been deleted.', 'success')
        return redirect(url_for('audits'))
    except Exception as e:
        session.rollback()
        flash(f'Error deleting audit: {str(e)}', 'error')
        return redirect(url_for('audits'))
    finally:
        session.close()


@app.route('/report/<audit_id>/delete', methods=['POST'])
@login_required
@admin_required
def report_delete(audit_id):
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Report not found.', 'error')
            return redirect(url_for('reports'))
        
        audit_date = audit.audit_date
        audit.is_deleted = True
        session.commit()
        
        flash(f'Report from {audit_date} has been deleted.', 'success')
        return redirect(url_for('reports'))
    except Exception as e:
        session.rollback()
        flash(f'Error deleting report: {str(e)}', 'error')
        return redirect(url_for('reports'))
    finally:
        session.close()


def _plant_scope(query, session):
    allowed = assigned_plant_ids(session, current_user)
    if allowed is not None:
        query = query.filter(Audit.plant_id.in_(allowed))
    return query


def _observation_rows(session, audit_id=None):
    """Observations visible to the current user, as plain dicts. Joins pin each one to
    its audit, plant, checkpoint and (for technical checkpoints) equipment."""
    site = current_site()
    q = (session.query(Observation, Audit, Plant, AuditResponse, AuditQuestion, Asset)
         .join(Audit, Observation.audit_id == Audit.id)
         .join(Plant, Audit.plant_id == Plant.id)
         .join(AuditResponse, Observation.audit_response_id == AuditResponse.id)
         .join(AuditQuestion, AuditResponse.question_id == AuditQuestion.id)
         .outerjoin(Asset, AuditResponse.asset_id == Asset.id)
         .filter(Observation.site_id == site.site_id, Audit.is_deleted == False,
                 Observation.status != 'withdrawn'))
    if audit_id:
        q = q.filter(Observation.audit_id == audit_id)
    q = _plant_scope(q, session)
    rows = []
    for obs, audit, plant, resp, question, asset in q.all():
        effective_type = 'OFI' if resp and (resp.response_status or '').lower() == 'ofi' else (obs.finding_type or 'NC').upper()
        if obs.finding_type != effective_type:
            obs.finding_type = effective_type
        rows.append({
            'obs': obs, 'audit': audit, 'plant': plant, 'question': question, 'asset': asset,
            'equipment': asset.asset_name if asset else 'Safety',
            'plant_name': plant.name if plant else 'Unknown',
            'audit_href': 'audit_submit',
            'overdue': is_overdue(obs), 'age_days': age_days(obs),
            'status_label': display_status(obs),
        })
    sev_rank = {'critical': 0, 'major': 1, 'minor': 2}
    rows.sort(key=lambda r: (sev_rank.get(r['obs'].severity, 3), r['obs'].target_date or '9999'))
    return rows


def get_observation_or_404(session, obs_id):
    """Site- and plant-scoped (same rules as audits); an observation under a
    deleted audit or another plant 404s like a missing one."""
    obs_id = _valid_uuid(obs_id)
    site = current_site()
    if not obs_id or site is None:
        return None, None
    obs = session.query(Observation).filter_by(id=obs_id, site_id=site.site_id).first()
    if not obs:
        return None, None
    audit = get_audit_or_404(session, obs.audit_id)
    if not audit or audit.is_deleted:
        return None, None
    return obs, audit


@app.route('/observations')
@login_required
def observations_list():
    session = Session()
    try:
        rows = _observation_rows(session)
        f_status = request.args.get('status', '')
        f_sev = request.args.get('severity', '')
        f_type = request.args.get('finding_type', '').upper()
        counts = {
            'all': len(rows),
            'overdue': sum(1 for r in rows if r['overdue']),
            'NC': sum(1 for r in rows if (r['obs'].finding_type or 'NC').upper() == 'NC'),
            'OFI': sum(1 for r in rows if (r['obs'].finding_type or 'NC').upper() == 'OFI'),
        }
        for st in STATUS_LABELS:
            counts[st] = sum(1 for r in rows if r['obs'].status == st)
        if f_status == 'overdue':
            rows = [r for r in rows if r['overdue']]
        elif f_status == 'unfinished':
            rows = [r for r in rows if r['obs'].status in UNFINISHED]
        elif f_status in STATUS_LABELS:
            rows = [r for r in rows if r['obs'].status == f_status]
        if f_sev in SEVERITY_LABELS:
            rows = [r for r in rows if r['obs'].severity == f_sev]
        if f_type in ('NC', 'OFI'):
            rows = [r for r in rows if (r['obs'].finding_type or 'NC').upper() == f_type]
        return render_template('observations.html', rows=rows, counts=counts,
                               f_status=f_status, f_sev=f_sev, f_type=f_type)
    finally:
        session.close()


@app.route('/observation/<obs_id>')
@login_required
def observation_detail(obs_id):
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            flash('Observation not found.', 'error')
            return redirect(url_for('observations_list'))
        resp = session.query(AuditResponse).filter_by(id=obs.audit_response_id).first()
        question = session.query(AuditQuestion).filter_by(id=resp.question_id).first() if resp else None
        asset = session.query(Asset).filter_by(id=resp.asset_id).first() if resp and resp.asset_id else None
        plant = session.query(Plant).filter_by(id=audit.plant_id).first()
        finding_photos = session.query(AuditResponsePhoto).filter_by(
            audit_response_id=obs.audit_response_id).order_by(AuditResponsePhoto.uploaded_at).all()
        closure_photos = session.query(ObservationPhoto).filter_by(
            observation_id=obs.id, kind='closure').order_by(ObservationPhoto.uploaded_at).all()
        events = session.query(ObservationEvent).filter_by(
            observation_id=obs.id).order_by(ObservationEvent.created_at.desc(), ObservationEvent.id).all()
        return render_template('observation_detail.html', obs=obs, audit=audit, resp=resp, question=question,
                               asset=asset, plant=plant, finding_photos=finding_photos,
                               closure_photos=closure_photos, events=events,
                               overdue=is_overdue(obs), age_days=age_days(obs),
                               working=obs.status in WORKING)
    finally:
        session.close()


@app.route('/observation/<obs_id>/update', methods=['POST'])
@login_required
def observation_update(obs_id):
    """Owner side of the workflow: record action taken, closure date and closure
    evidence; start work; submit for review."""
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            flash('Observation not found.', 'error')
            return redirect(url_for('observations_list'))
        do = request.form.get('do', 'save')
        actor = _actor()
        try:
            if obs.status not in WORKING:
                raise WorkflowError('This observation is not open for updates '
                                          f'(status: {STATUS_LABELS.get(obs.status, obs.status)}).')
            if do == 'start' and obs.status != 'in_progress':
                start_work(session, obs, actor)
            if do in ('save', 'submit') or request.form.get('action_taken') or request.form.get('closure_date'):
                if 'action_taken' in request.form or 'closure_date' in request.form:
                    save_action(session, obs, actor, request.form.get('action_taken'),
                                      request.form.get('closure_date'))
            # closure evidence (camera or gallery)
            uploaded = [f for f in request.files.getlist('closure_photos') if f and f.filename]
            have = session.query(ObservationPhoto).filter_by(observation_id=obs.id, kind='closure').count()
            added = 0
            for f in uploaded[:max(0, 8 - have)]:
                info = _store_photo(f, f'{str(audit.id).replace("-", "")[:12]}/o{str(obs.id).replace("-", "")[:10]}', 'closure', request.form, 'closure')
                if info:
                    session.add(ObservationPhoto(observation_id=obs.id, kind='closure',
                                                 uploaded_by=actor, **info))
                    added += 1
            if added:
                obs_log(session, obs, 'closure_evidence_added', actor, f'{added} photo(s) added')
            session.commit()
            if do == 'submit':
                submit_for_review(session, obs, actor)
                session.commit()
                flash('Submitted for review. A Quality/Engineering reviewer will accept or reject it.', 'success')
            else:
                flash('Saved.', 'success')
        except WorkflowError as exc:
            session.commit()  # keep whatever was saved before the rule that stopped us
            flash(str(exc), 'error')
        return redirect(url_for('observation_detail', obs_id=obs.id))
    finally:
        session.close()


@app.route('/observation/<obs_id>/review', methods=['POST'])
@login_required
def observation_review(obs_id):
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            flash('Observation not found.', 'error')
            return redirect(url_for('observations_list'))
        try:
            review_observation(session, obs, _actor(), current_user,
                         request.form.get('decision', ''), request.form.get('reviewer_comments'))
            session.commit()
            decision = request.form.get('decision')
            report_note = ''
            if decision in ('accept', 'reopen', 'reject'):
                try:
                    site = current_site()
                    pdf_path, pdf_error = generate_pdf(str(audit.id), site_id=site.site_id if site else None)
                    if pdf_path:
                        report_note = ' The audit report has been refreshed with the latest observation status.'
                    else:
                        app.logger.warning('Report refresh after observation review failed: %s', pdf_error)
                except Exception:
                    app.logger.exception('Report refresh after observation review failed')
            flash({'accept': 'Accepted - observation is now closed.',
                   'reject': 'Rejected - sent back to the responsible person.',
                   'reopen': 'Reopened.'}[decision] + report_note, 'success')
        except WorkflowError as exc:
            session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('observation_detail', obs_id=obs_id))
    finally:
        session.close()


@app.route('/observation/<obs_id>/reassign', methods=['POST'])
@login_required
def observation_reassign(obs_id):
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            flash('Observation not found.', 'error')
            return redirect(url_for('observations_list'))
        try:
            changes = reassign_observation(session, obs, _actor(), current_user, request.form.get('severity'),
                                     request.form.get('responsible_person'), request.form.get('target_date'))
            session.commit()
            flash('Updated: ' + '; '.join(changes) if changes else 'No changes.', 'success')
        except WorkflowError as exc:
            session.rollback()
            flash(str(exc), 'error')
        return redirect(url_for('observation_detail', obs_id=obs_id))
    finally:
        session.close()


@app.route('/observation/<obs_id>/details', methods=['POST'])
@login_required
def observation_details_update(obs_id):
    """Edit finding classification/details outside the locked audit checkpoint flow."""
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            flash('Observation not found.', 'error')
            return redirect(url_for('observations_list'))
        if not can_access_plant(session, current_user, audit.plant_id):
            flash('You do not have access to this plant.', 'error')
            return redirect(url_for('observations_list'))
        if obs.status in ('closed', 'withdrawn'):
            flash('This observation is closed. Reopen it before changing finding details.', 'error')
            return redirect(url_for('observation_detail', obs_id=obs_id))

        severity = (request.form.get('severity') or '').strip().lower()
        if severity and severity not in SEVERITY_LABELS:
            flash('Choose a valid severity or leave it unclassified.', 'error')
            return redirect(url_for('observation_detail', obs_id=obs_id))
        target_date = (request.form.get('target_date') or '').strip()
        if target_date:
            try:
                datetime.strptime(target_date, '%Y-%m-%d')
            except ValueError:
                flash('Target date must be a valid date.', 'error')
                return redirect(url_for('observation_detail', obs_id=obs_id))

        changes = []
        new_observation = (request.form.get('observation') or '').strip() or None
        new_remarks = (request.form.get('remarks') or '').strip() or None
        new_owner = (request.form.get('responsible_person') or '').strip() or None
        new_target = target_date or None
        new_severity = severity or None
        for label, old, new in [
            ('observation text', obs.observation, new_observation),
            ('remarks', obs.remarks, new_remarks),
            ('severity', obs.severity, new_severity),
            ('responsible person/team', obs.responsible_person, new_owner),
            ('target date', obs.target_date, new_target),
        ]:
            if old != new:
                changes.append(f'{label}: {old or "—"} → {new or "—"}')
        obs.observation = new_observation
        obs.remarks = new_remarks
        obs.severity = new_severity
        obs.responsible_person = new_owner
        obs.target_date = new_target
        if changes:
            obs_log(session, obs, 'finding_details_updated', _actor(), '; '.join(changes))
            session.commit()
            flash('Observation details updated.', 'success')
        else:
            session.rollback()
            flash('No changes to save.', 'info')
        return redirect(url_for('observation_detail', obs_id=obs_id))
    except Exception:
        session.rollback()
        app.logger.exception('Could not update observation details')
        flash('Could not update observation details. Please retry.', 'error')
        return redirect(url_for('observation_detail', obs_id=obs_id))
    finally:
        session.close()


@app.route('/observation/<obs_id>/photo/<photo_id>/delete', methods=['POST'])
@login_required
def observation_photo_delete(obs_id, photo_id):
    session = Session()
    try:
        obs, audit = get_observation_or_404(session, obs_id)
        if not obs:
            return {'success': False, 'error': 'Observation not found'}, 404
        if obs.status not in WORKING:
            return {'success': False, 'error': 'Evidence is locked once submitted for review'}, 403
        photo = session.query(ObservationPhoto).filter_by(id=_valid_uuid(photo_id) or '', observation_id=obs.id).first()
        if not photo:
            return {'success': False, 'error': 'Photo not found'}, 404
        old_file = os.path.join(UPLOAD_FOLDER, photo.file_path)
        if os.path.exists(old_file):
            os.remove(old_file)
        session.delete(photo)
        obs_log(session, obs, 'closure_evidence_removed', _actor(), 'Photo removed before submission')
        session.commit()
        return {'success': True}
    finally:
        session.close()


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    """Serve a stored photo only when its parent audit is accessible.

    Both checkpoint evidence (AuditResponsePhoto) and observation closure
    evidence (ObservationPhoto) are supported. The URL and stored relative
    path format remain unchanged for the web and Android clients.
    """
    normalized = str(filename or '').replace('\\', '/').lstrip('/')
    if not normalized or any(part in ('.', '..') for part in normalized.split('/')):
        return '', 404

    candidate_paths = {normalized, normalized.replace('/', '\\')}
    session = Session()
    try:
        audit_ids = {
            row[0] for row in session.query(AuditResponse.audit_id)
            .join(AuditResponsePhoto,
                  AuditResponsePhoto.audit_response_id == AuditResponse.id)
            .filter(AuditResponsePhoto.file_path.in_(candidate_paths)).all()
        }
        audit_ids.update(
            row[0] for row in session.query(Observation.audit_id)
            .join(ObservationPhoto,
                  ObservationPhoto.observation_id == Observation.id)
            .filter(ObservationPhoto.file_path.in_(candidate_paths)).all()
        )

        if not audit_ids:
            return '', 404

        # This helper enforces active-site and assigned-plant authorization.
        if not any(get_audit_or_404(session, audit_id) is not None
                   for audit_id in audit_ids):
            return '', 404

        return send_from_directory(UPLOAD_FOLDER, normalized)
    finally:
        session.close()


def _build_audit_json(session, audit, site):
    """Structured export per requirement #15 — everything a central-office
    importer needs, derived only from fields that actually exist on the
    models (no invented fields)."""
    plant = session.query(Plant).filter_by(id=audit.plant_id, site_id=site.site_id).first()

    responses_raw = session.query(AuditResponse, Asset, AuditQuestion).outerjoin(
        Asset, AuditResponse.asset_id == Asset.id).join(
        AuditQuestion, AuditResponse.question_id == AuditQuestion.id).filter(
        AuditResponse.audit_id == audit.id).all()

    responses = []
    photos = []
    corrective_actions = []
    observations_json = []
    passed = 0
    failed = 0
    for resp, asset, question in responses_raw:
        status = (resp.response_status or '').lower()
        if status == 'pass':
            passed += 1
        elif status == 'fail':
            failed += 1
        asset_name = asset.asset_name if asset else 'Safety'
        responses.append({
            'asset_name': asset_name,
            'question': question.question_text,
            'response_status': resp.response_status,
            'response_label': RESPONSE_LABELS.get((resp.response_status or 'na').lower(), 'Not Applicable'),
            'remarks': resp.remarks,
        })
        resp_photos = session.query(AuditResponsePhoto).filter_by(audit_response_id=resp.id).all()
        for p in resp_photos:
            photos.append({
                'asset_name': asset_name,
                'question': question.question_text,
                'file_name': os.path.basename(p.file_path),
                'relative_path': p.file_path,
            })
        ob = session.query(Observation).filter_by(audit_response_id=resp.id).first()
        if ob and ob.status != 'withdrawn':
            observations_json.append({
                'equipment': asset_name, 'checkpoint': question.question_text,
                'observation': ob.observation, 'severity': ob.severity,
                'responsible_person': ob.responsible_person, 'target_date': ob.target_date,
                'remarks': ob.remarks, 'action_taken': ob.action_taken, 'closure_date': ob.closure_date,
                'status': ob.status, 'review_status': ob.review_status,
                'reviewer_comments': ob.reviewer_comments,
                'closure_photos': [os.path.basename(x.file_path) for x in session.query(ObservationPhoto).filter_by(observation_id=ob.id).all()],
                'history': [{'event': e.event_type, 'from': e.from_status, 'to': e.to_status, 'by': e.actor,
                             'comment': e.comment, 'at': e.created_at.isoformat() if e.created_at else None}
                            for e in session.query(ObservationEvent).filter_by(observation_id=ob.id).order_by(ObservationEvent.created_at).all()],
            })
        ca = session.query(CorrectiveAction).filter_by(audit_response_id=resp.id).first()
        if ca:
            corrective_actions.append({
                'asset_name': asset_name,
                'question': question.question_text,
                'description': ca.description,
                'priority': ca.priority,
                'status': ca.status,
            })

    total = passed + failed
    score = round((passed / total) * 100) if total else None

    return {
        'schema_version': EXPORT_SCHEMA_VERSION,
        'application_version': APP_VERSION,
        'site_id': site.site_id,
        'site_name': site.site_name,
        'audit_id': audit.audit_number or audit.id,
        'audit_internal_id': audit.id,
        'plant_name': plant.name if plant else None,
        'audit_date': audit.audit_date,
        'audit_type': audit.audit_type,
        'auditor': audit.auditor_name,
        'auditee': audit.auditee_name,
        'status': audit.status,
        'score': score,
        'total_questions': total,
        'passed': passed,
        'failed': failed,
        'responses': responses,
        'corrective_actions': corrective_actions,
        'observations': observations_json,
        'audit_score_pct': audit.score_pct,
        'compliance_pct': audit.compliance_pct,
        'site_rating': audit.site_rating,
        'photos': photos,
        'exported_at': datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + 'Z',
    }


@app.route('/audit/<audit_id>/export')
@login_required
def audit_export(audit_id):
    """
    Requirement #14: a self-contained, transferable package (USB / email /
    shared drive) containing the PDF report, a structured JSON (so the
    central office can import data without scraping the PDF), and the
    audit's photos — nothing else from the application is included.
    """
    site = current_site()
    session = Session()
    try:
        audit = get_audit_or_404(session, audit_id)
        if not audit:
            flash('Audit not found.', 'error')
            return redirect(url_for('audits'))

        pdf_path, error = generate_pdf(str(audit.id), site_id=site.site_id)
        if not pdf_path or not os.path.exists(pdf_path):
            flash(f'Could not generate report for export: {error or "unknown error"}', 'error')
            return redirect(url_for('audits'))

        export_data = _build_audit_json(session, audit, site)
        package_name = audit.audit_number or str(audit.id)

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(pdf_path, arcname=f'{package_name}/audit_report.pdf')
            zf.writestr(f'{package_name}/audit_data.json', json.dumps(export_data, indent=2, default=str))
            for photo in export_data['photos']:
                src = os.path.join(UPLOAD_FOLDER, photo['relative_path'])
                if os.path.exists(src):
                    zf.write(src, arcname=f'{package_name}/photos/{photo["file_name"]}')
        buffer.seek(0)

        return send_file(buffer, as_attachment=True,
                         download_name=f'{package_name}.zip',
                         mimetype='application/zip')
    finally:
        session.close()


def _safe_extract(zf, target_dir):
    """Extract a ZIP while refusing any member path that would escape
    target_dir (zip-slip protection) — required before trusting a
    user-supplied backup/restore file."""
    target_dir = os.path.abspath(target_dir)
    for member in zf.namelist():
        member_path = os.path.abspath(os.path.join(target_dir, member))
        if not member_path.startswith(target_dir + os.sep) and member_path != target_dir:
            raise ValueError(f'Unsafe path in archive: {member}')
    zf.extractall(target_dir)


@app.route('/settings')
@login_required
@admin_required
def settings():
    site = current_site()
    backups = sorted(
        [f for f in os.listdir(str(backups_dir())) if f.endswith('.zip')],
        reverse=True
    )
    session = Session()
    try:
        asset_type_count = session.query(AssetType).count()
        question_count = session.query(AuditQuestion).count()
    finally:
        session.close()
    return render_template('settings.html', site=site, app_version=APP_VERSION, backups=backups,
                          asset_type_count=asset_type_count, question_count=question_count)


@app.route('/settings/reseed-checklist', methods=['POST'])
@login_required
@admin_required
def settings_reseed_checklist():
    """
    Safety-net action: if this install's checklist taxonomy (asset types /
    questions) somehow ended up empty or incomplete -- e.g. an older build
    was launched before the auto-seed-on-startup fix existed -- this lets an
    admin recover without deleting the whole database by hand. Idempotent:
    does nothing if the taxonomy is already seeded.
    """
    seeded = ensure_seeded()
    ensure_safety_checklist()
    ensure_civil_checklist()
    if seeded:
        flash('Checklist taxonomy seeded successfully.', 'success')
    else:
        flash('Checklist taxonomy was already present — nothing to do.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/backup', methods=['POST'])
@login_required
@admin_required
def settings_backup():
    """
    Requirement #23: a single ZIP containing the local database, photos,
    and site config — enough to fully restore this install. Never
    overwrites an existing backup file (timestamped name).
    """
    if CENTRAL_MODE:
        abort(403)
    site = current_site()
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')
    backup_name = f'solarudit_backup_{site.site_id}_{timestamp}.zip'
    backup_path = os.path.join(str(backups_dir()), backup_name)

    # Timestamp collisions are effectively impossible, but guard anyway
    # per requirement: never silently overwrite an existing backup.
    suffix = 1
    while os.path.exists(backup_path):
        backup_path = os.path.join(str(backups_dir()), f'solarudit_backup_{site.site_id}_{timestamp}_{suffix}.zip')
        suffix += 1

    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        db_file = str(db_path())
        if os.path.exists(db_file):
            zf.write(db_file, arcname='data/solar_audit.db')
        config_file = os.path.join(str(data_dir()), 'config', 'site_config.json')
        if os.path.exists(config_file):
            zf.write(config_file, arcname='config/site_config.json')
        photos_root = str(photos_dir())
        for root, _dirs, files in os.walk(photos_root):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, photos_root)
                zf.write(full, arcname=os.path.join('photos', rel))
        zf.writestr('backup_manifest.json', json.dumps({
            'site_id': site.site_id,
            'site_name': site.site_name,
            'application_version': APP_VERSION,
            'created_at': datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + 'Z',
        }, indent=2))

    flash(f'Backup created: {os.path.basename(backup_path)}', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/backup/<filename>/download')
@login_required
@admin_required
def settings_backup_download(filename):
    if CENTRAL_MODE:
        abort(403)
    filename = os.path.basename(filename)  # no path traversal
    path = os.path.join(str(backups_dir()), filename)
    if not filename.endswith('.zip') or not os.path.exists(path):
        flash('Backup file not found.', 'error')
        return redirect(url_for('settings'))
    return send_file(path, as_attachment=True, download_name=filename, mimetype='application/zip')


@app.route('/settings/restore', methods=['POST'])
@login_required
@admin_required
def settings_restore():
    """
    Requirement #24: validate the backup, verify site ID, take a safety
    backup of current data first, then replace. Refuses (does not silently
    override) a backup whose site_id doesn't match this install.
    """
    if CENTRAL_MODE:
        abort(403)
    file = request.files.get('backup_file')
    if not file or not file.filename:
        flash('Please choose a backup ZIP file to restore.', 'error')
        return redirect(url_for('settings'))

    site = current_site()
    with tempfile.TemporaryDirectory() as tmp:
        upload_path = os.path.join(tmp, 'upload.zip')
        file.save(upload_path)

        try:
            with zipfile.ZipFile(upload_path) as zf:
                if 'config/site_config.json' not in zf.namelist():
                    flash('Invalid backup: missing site configuration.', 'error')
                    return redirect(url_for('settings'))
                manifest_site_id = json.loads(zf.read('config/site_config.json'))['site_id']
                if manifest_site_id.strip().upper() != site.site_id.strip().upper():
                    flash(f'Refused: this backup belongs to site "{manifest_site_id}", '
                          f'not this installation ("{site.site_id}"). Restoring it here '
                          f'would mix another site\'s data into this install.', 'error')
                    return redirect(url_for('settings'))

                extract_dir = os.path.join(tmp, 'extracted')
                os.makedirs(extract_dir, exist_ok=True)
                _safe_extract(zf, extract_dir)
        except (zipfile.BadZipFile, KeyError, ValueError, json.JSONDecodeError) as e:
            flash(f'Invalid or corrupt backup file: {e}', 'error')
            return redirect(url_for('settings'))

        # Safety backup of current data before destructive replace.
        safety_name = f'pre_restore_safety_{datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")}.zip'
        safety_path = os.path.join(str(backups_dir()), safety_name)
        with zipfile.ZipFile(safety_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            db_file = str(db_path())
            if os.path.exists(db_file):
                zf.write(db_file, arcname='data/solar_audit.db')
            photos_root = str(photos_dir())
            for root, _dirs, files in os.walk(photos_root):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, photos_root)
                    zf.write(full, arcname=os.path.join('photos', rel))

        # Replace database file.
        extracted_db = os.path.join(extract_dir, 'data', 'solar_audit.db')
        if os.path.exists(extracted_db):
            engine.dispose()  # release any pooled connections before swapping the file
            shutil.copy2(extracted_db, str(db_path()))

        # Replace photos directory contents.
        extracted_photos = os.path.join(extract_dir, 'photos')
        if os.path.isdir(extracted_photos):
            photos_root = str(photos_dir())
            if os.path.isdir(photos_root):
                shutil.rmtree(photos_root)
            shutil.copytree(extracted_photos, photos_root)

    flash(f'Restore complete. A safety backup of the previous data was saved as '
          f'"{safety_name}". Please restart the application.', 'success')
    return redirect(url_for('settings'))


@app.route('/about')
@login_required
def about():
    site = current_site()
    return render_template('about.html', site=site, app_version=APP_VERSION,
                          schema_version=EXPORT_SCHEMA_VERSION)


@app.route('/reports')
@login_required
def reports():
    site = current_site()
    session = Session()
    try:
        allowed = assigned_plant_ids(session, current_user)
        query = session.query(Audit, Plant).join(Plant, Audit.plant_id == Plant.id).filter(
            Audit.status == 'submitted', Audit.is_deleted == False,
            Audit.site_id == site.site_id)
        if allowed is not None:
            query = query.filter(Audit.plant_id.in_(allowed))
        audits_raw = query.order_by(Audit.created_at.desc()).all()
        reports = []
        for audit, plant in audits_raw:
            reports.append({
                'id': str(audit.id),
                'audit_number': audit.audit_number,
                'plant_name': plant.name,
                'audit_date': audit.audit_date,
                'auditor_name': audit.auditor_name,
                'status': audit.status,
                'created_at': audit.created_at,
            })
        return render_template('reports.html', reports=reports)
    finally:
        session.close()


if __name__ == '__main__':
    os.environ.setdefault('MOBILE_API_ENABLED', '1')
    os.environ.setdefault(
        'MOBILE_API_SIGNING_SECRET',
        os.environ.get('SECRET_KEY') or 'solar-audit-mobile-api-signing-secret-v1-local-dev-key-2026',
    )
    port = int(os.environ.get('PORT', 5000))
    try:
        from waitress import serve
    except ImportError:
        # Still allow a clean local development start if requirements have not
        # been installed yet. Explicit threading keeps slow uploads from blocking
        # CSS, status and other browser requests.
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
    else:
        print(f'Solar Audit server starting on http://127.0.0.1:{port} (Waitress, 8 threads)', flush=True)
        serve(app, host='0.0.0.0', port=port, threads=8, channel_timeout=180)