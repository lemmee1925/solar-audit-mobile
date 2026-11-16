import os
import uuid
from sqlalchemy import (create_engine, Column, String, Integer, Float, Boolean,
                        Text, DateTime, ForeignKey, UniqueConstraint, Index)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func

from paths import db_path

# ---------------------------------------------------------------------------
# Database engine
#
# Offline, single-site deployment: SQLite, one file per install, stored in
# the writable data directory (see paths.py) â€” never inside the read-only
# application/Program Files directory.
#
# An operator can still point at a different database (e.g. Postgres, for
# development against a shared server) by setting DATABASE_URL explicitly,
# but the default and the supported offline deployment path is SQLite.
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get('DATABASE_URL') or f"sqlite:///{db_path()}"

_engine_kwargs = {'echo': False}
if DATABASE_URL.startswith('sqlite'):
    # check_same_thread=False: Flask's dev server / installed launcher may
    # hand requests to different threads; each request still opens and
    # closes its own Session(), so this is safe.
    _engine_kwargs['connect_args'] = {'check_same_thread': False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)
Base = declarative_base()
Session = sessionmaker(bind=engine)


def new_uuid() -> str:
    return str(uuid.uuid4())


# Portable "UUID" columns: plain 36-char strings everywhere (Postgres, MySQL,
# and SQLite all handle this identically â€” no dialect-specific type needed).
def UUIDColumn(**kwargs):
    return Column(String(36), **kwargs)


def _enable_sqlite_foreign_keys():
    if DATABASE_URL.startswith('sqlite'):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk_pragma_on_connect(dbapi_con, con_record):
            cursor = dbapi_con.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()


_enable_sqlite_foreign_keys()


from flask_login import UserMixin  # noqa: E402  (after Base/engine setup, no cycle)




# ---------------------------------------------------------------------------
# Central organization/site registry.
#
# These tables are shared by central deployments. Existing single-site
# deployments continue to use site_config.json; their business tables remain
# site_id-scoped as before.
# ---------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = 'organizations'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default='1')
    created_at = Column(DateTime, server_default=func.now())


class Site(Base):
    __tablename__ = 'sites'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    site_id = Column(String(20), nullable=False, unique=True, index=True)
    site_name = Column(String(200), nullable=False)
    organization_id = Column(String(36), ForeignKey('organizations.id'), index=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default='1')
    created_at = Column(DateTime, server_default=func.now())


class AuditNumberCounter(Base):
    __tablename__ = 'audit_number_counters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(20), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    last_sequence = Column(Integer, nullable=False, default=0, server_default='0')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint('site_id', 'year', name='uq_audit_counter_site_year'),
    )


class User(Base, UserMixin):
    __tablename__ = 'users'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    username = Column(String(200), nullable=False, unique=True)
    full_name = Column(String(200), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default='user')
    is_active = Column(Boolean, default=True)
    # Every user belongs to the site this install serves. Always set from
    # the trusted site_config.json at creation time, never from user input.
    site_id = Column(String(20), nullable=True, index=True)
    organization_id = Column(String(36), ForeignKey('organizations.id'), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())


class MobileAPIToken(Base):
    """Hashed, revocable device token record for native mobile sessions.

    The token itself must never be stored here; only its SHA-256 hash is
    persisted. Token issuance/validation is wired in a later API milestone.
    """
    __tablename__ = 'mobile_api_tokens'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'),
                     nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    device_name = Column(String(120), nullable=False, default='Android device',
                         server_default='Android device')
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)




class UserSiteAccess(Base):
    """Explicit site grants for HQ administrators in central mode."""
    __tablename__ = 'user_site_access'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    site_id = Column(String(20), ForeignKey('sites.site_id', ondelete='CASCADE'), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default='1')
    granted_by = Column(String(36), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint('user_id', 'site_id', name='uq_user_site_access'),)

class Plant(Base):
    __tablename__ = 'plants'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    location = Column(String(200))
    capacity_mw = Column(Float)
    site_id = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())

    # ---- Master specification, entered once per plant (admin only), and
    # copied as the starting point into every new audit created against
    # this plant. The audit's own copies of these fields remain editable
    # per-audit afterward if something genuinely changed on-site â€” this is
    # just the default, not a locked value. Mirrors the fields that used to
    # be re-typed from scratch on every single audit. ----
    building_name = Column(String(200))
    roof_type = Column(String(100))
    city = Column(String(100))
    state = Column(String(100))
    no_of_blocks = Column(Integer)
    dc_capacity = Column(Float)
    ac_capacity = Column(Float)
    central_inverter_type = Column(String(200))
    central_inverter_count = Column(Integer)
    string_inverter_type = Column(String(200))
    string_inverter_count = Column(Integer)
    scb_count = Column(Integer)
    mms_type = Column(String(200))
    mms_count = Column(Integer)
    transformer_type = Column(String(200))
    transformer_count = Column(Integer)
    clamps_connectors_count = Column(Integer)
    lt_panel_type = Column(String(200))
    lt_panel_count = Column(Integer)
    ht_panel_type = Column(String(200))
    ht_panel_count = Column(Integer)
    acdb_count = Column(Integer)
    ups_db_battery_count = Column(Integer)
    lightning_protection_count = Column(Integer)
    dc_earthing = Column(String(200))
    ac_earthing = Column(String(200))


class UserPlantAssignment(Base):
    """
    Which plant(s) a non-admin user (engineer/technician) is allowed to see
    and audit at this site. Admin is always unrestricted regardless of any
    rows here (checked in code, not by giving admin rows for every plant).

    A site with only one plant doesn't need this table populated at all â€”
    it only matters once a site has more than one plant and different
    people are responsible for different plants.
    """
    __tablename__ = 'user_plant_assignments'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    plant_id = Column(String(36), ForeignKey('plants.id'), nullable=False, index=True)
    site_id = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'plant_id', name='uq_user_plant'),
    )


class AssetType(Base):
    __tablename__ = 'asset_types'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    created_at = Column(DateTime, server_default=func.now())


class Asset(Base):
    __tablename__ = 'assets'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    plant_id = Column(String(36), ForeignKey('plants.id'))
    asset_type_id = Column(Integer, ForeignKey('asset_types.id'))
    asset_name = Column(String(200))
    is_active = Column(Boolean, default=True)
    site_id = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())


class AuditSection(Base):
    __tablename__ = 'audit_sections'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    section_order = Column(Integer, default=0)


class AuditQuestion(Base):
    __tablename__ = 'audit_questions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    section_id = Column(Integer, ForeignKey('audit_sections.id'))
    asset_type_id = Column(Integer, ForeignKey('asset_types.id'))
    question_text = Column(Text, nullable=False)
    is_mandatory = Column(Boolean, default=True)
    question_order = Column(Integer, default=0)
    # ---- Checklist versioning / traceability ----
    # Questions are never deleted once audits reference them. Retiring a
    # checkpoint sets is_active=False (hidden from NEW audits, still resolvable
    # for historical audits and reports). `source` records where it came from
    # ('seed', 'excel:<file>'), `weight` is an optional per-checkpoint weight
    # (e.g. from the Excel) used by the scoring engine when present.
    is_active = Column(Boolean, default=True)
    source = Column(String(200))
    weight = Column(Float)
    added_at = Column(DateTime, server_default=func.now())
    retired_at = Column(DateTime)


class Audit(Base):
    __tablename__ = 'audits'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    # Human-readable, globally-meaningful identifier: AUD-<year>-<site>-<seq>
    # e.g. AUD-2026-MH001-000123. The string id above remains the real
    # primary key everywhere internally; audit_number is purely for humans
    # and for the central office to identify site/year/sequence at a glance.
    audit_number = Column(String(40), unique=True, index=True)
    plant_id = Column(String(36), ForeignKey('plants.id'))
    site_id = Column(String(20), nullable=False, index=True)
    audit_date = Column(String(20))
    audit_time = Column(String(10))
    status = Column(String(50), default='draft')
    building_name = Column(String(200))
    capacity_mw = Column(String(50))
    roof_type = Column(String(100))
    city = Column(String(100))
    state = Column(String(100))
    audit_type = Column(String(100))
    auditor_name = Column(String(200))
    auditee_name = Column(String(200))
    asset_id_ref = Column(String(100))
    is_deleted = Column(Boolean, default=False)
    # Score snapshot frozen at submission (see scoring.py). Kept on the audit
    # so historical scores never change when weightages/config change later.
    submitted_at = Column(DateTime)
    compliance_pct = Column(Float)
    score_pct = Column(Float)
    site_rating = Column(String(50))
    score_detail = Column(Text)  # JSON: counts + the weights used
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditSiteDetail(Base):
    __tablename__ = 'audit_site_details'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_id = Column(String(36), ForeignKey('audits.id'), unique=True)
    no_of_blocks = Column(Integer)
    dc_capacity = Column(Float)
    ac_capacity = Column(Float)
    central_inverter_type = Column(String(200))
    central_inverter_count = Column(Integer)
    string_inverter_type = Column(String(200))
    string_inverter_count = Column(Integer)
    scb_count = Column(Integer)
    mms_type = Column(String(200))
    mms_count = Column(Integer)
    transformer_type = Column(String(200))
    transformer_count = Column(Integer)
    clamps_connectors_count = Column(Integer)
    lt_panel_type = Column(String(200))
    lt_panel_count = Column(Integer)
    ht_panel_type = Column(String(200))
    ht_panel_count = Column(Integer)
    acdb_count = Column(Integer)
    ups_db_battery_count = Column(Integer)
    lightning_protection_count = Column(Integer)
    dc_earthing = Column(String(200))
    ac_earthing = Column(String(200))
    created_at = Column(DateTime, server_default=func.now())


class AuditAsset(Base):
    __tablename__ = 'audit_assets'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_id = Column(String(36), ForeignKey('audits.id'))
    asset_id = Column(String(36), ForeignKey('assets.id'))
    location = Column(String(300))
    rank = Column(String(50))
    observation = Column(Text)
    remarks = Column(Text)
    is_audited = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint('audit_id', 'asset_id', name='uq_audit_asset'),)


class AuditResponse(Base):
    __tablename__ = 'audit_responses'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_id = Column(String(36), ForeignKey('audits.id'))
    asset_id = Column(String(36), ForeignKey('assets.id'))
    question_id = Column(Integer, ForeignKey('audit_questions.id'))
    response_status = Column(String(50))
    remarks = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime)
    responded_by = Column(String(200))
    __table_args__ = (UniqueConstraint('audit_id', 'asset_id', 'question_id', name='uq_audit_response'),)


class AuditResponsePhoto(Base):
    __tablename__ = 'audit_response_photos'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_response_id = Column(String(36), ForeignKey('audit_responses.id'))
    file_path = Column(String(500))
    latitude = Column(Float)
    longitude = Column(Float)
    gps_accuracy = Column(Float)
    captured_at = Column(DateTime)
    uploaded_at = Column(DateTime, server_default=func.now())


class CorrectiveAction(Base):
    __tablename__ = 'corrective_actions'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_response_id = Column(String(36), ForeignKey('audit_responses.id'))
    description = Column(Text)
    priority = Column(String(50), default='high')
    status = Column(String(50), default='open')
    created_at = Column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Observations & closure workflow
#
# One Observation per Not-Complied response (audit_response_id is unique).
# The response row already pins audit + equipment + checkpoint, so every
# observation, and every photo hanging off it, is traceable to the exact
# audit / equipment / checkpoint. Observations are never hard-deleted by the
# app; history lives in ObservationEvent.
#
# status values: open, in_progress, submitted, closed, rejected, reopened
# ("overdue" is derived at read time from target_date, see observations.py)
# ---------------------------------------------------------------------------
class Observation(Base):
    __tablename__ = 'observations'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    audit_id = Column(String(36), ForeignKey('audits.id'), nullable=False, index=True)
    audit_response_id = Column(String(36), ForeignKey('audit_responses.id'), nullable=False, unique=True)
    site_id = Column(String(20), nullable=False, index=True)
    observation = Column(Text)
    severity = Column(String(20))            # critical | major | minor
    responsible_person = Column(String(200))  # person or team
    target_date = Column(String(20))          # YYYY-MM-DD
    remarks = Column(Text)
    finding_type = Column(String(10), nullable=False, default='NC')  # NC | OFI
    action_taken = Column(Text)
    closure_date = Column(String(20))         # date the owner reports the fix done
    reviewer_comments = Column(Text)
    review_status = Column(String(20), default='pending')  # pending | accepted | rejected
    status = Column(String(30), default='open', index=True)
    reviewed_by = Column(String(200))
    reviewed_at = Column(DateTime)
    closed_at = Column(DateTime)              # set only on reviewer acceptance
    reopen_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ObservationPhoto(Base):
    """Closure evidence uploaded by the responsible person. (The *finding*
    photos are the checkpoint's AuditResponsePhoto rows, reached through
    Observation.audit_response_id.)"""
    __tablename__ = 'observation_photos'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    observation_id = Column(String(36), ForeignKey('observations.id'), nullable=False, index=True)
    kind = Column(String(20), default='closure')
    file_path = Column(String(500))
    latitude = Column(Float)
    longitude = Column(Float)
    gps_accuracy = Column(Float)
    captured_at = Column(DateTime)
    uploaded_by = Column(String(200))
    uploaded_at = Column(DateTime, server_default=func.now())


class ObservationEvent(Base):
    """Append-only audit trail of everything that happens to an observation."""
    __tablename__ = 'observation_events'
    id = UUIDColumn(primary_key=True, default=new_uuid)
    observation_id = Column(String(36), ForeignKey('observations.id'), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)
    from_status = Column(String(30))
    to_status = Column(String(30))
    comment = Column(Text)
    actor = Column(String(200))
    created_at = Column(DateTime, server_default=func.now())


Index('ix_audits_site_status', Audit.site_id, Audit.status)

# SQLite remains the supported self-initializing offline deployment.
# PostgreSQL schema changes must be applied explicitly through Alembic.
if DATABASE_URL.startswith('sqlite'):
    Base.metadata.create_all(engine)

# Small forward-only migration for existing SQLite installs. create_all()
# does not add columns to tables that already exist, so the mobile GPS fields
# need an explicit upgrade the first time this build starts.
def _ensure_photo_gps_columns():
    if not DATABASE_URL.startswith('sqlite'):
        return
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if 'audit_response_photos' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('audit_response_photos')}
    additions = {
        'latitude': 'FLOAT',
        'longitude': 'FLOAT',
        'gps_accuracy': 'FLOAT',
        'captured_at': 'DATETIME',
    }
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE audit_response_photos ADD COLUMN {name} {sql_type}'))

_ensure_photo_gps_columns()


# Generic forward-only column upgrades for existing SQLite installs
# (create_all() only creates missing TABLES, never missing columns).
def _ensure_columns(table, additions):
    if not DATABASE_URL.startswith('sqlite'):
        return
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns(table)}
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))


def _ensure_checklist_versioning_columns():
    _ensure_columns('audit_questions', {
        'is_active': 'BOOLEAN DEFAULT 1', 'source': 'VARCHAR(200)', 'weight': 'FLOAT',
        'added_at': 'DATETIME', 'retired_at': 'DATETIME',
    })
    _ensure_columns('audits', {
        'submitted_at': 'DATETIME', 'compliance_pct': 'FLOAT', 'score_pct': 'FLOAT',
        'site_rating': 'VARCHAR(50)', 'score_detail': 'TEXT',
    })
    _ensure_columns('audit_responses', {'updated_at': 'DATETIME', 'responded_by': 'VARCHAR(200)'})
    _ensure_columns('observations', {
        "finding_type": "VARCHAR(10) NOT NULL DEFAULT 'NC'",
    })
    _ensure_columns('users', {
        'organization_id': 'VARCHAR(36)',
    })
    _ensure_columns('sites', {
        'organization_id': 'VARCHAR(36)',
    })
    # Ensure any remaining model columns (such as plant specification fields)
    # are present on older SQLite databases without destructive recreation.
    if DATABASE_URL.startswith('sqlite'):
        from sqlalchemy import inspect, text
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        with engine.begin() as conn:
            for table_name, table_obj in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue
                existing_cols = {c['name'] for c in inspector.get_columns(table_name)}
                for col in table_obj.columns:
                    if col.name not in existing_cols:
                        col_type = col.type.compile(dialect=engine.dialect)
                        conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}'))
            conn.execute(text("UPDATE audit_questions SET is_active = 1 WHERE is_active IS NULL"))


_ensure_checklist_versioning_columns()

