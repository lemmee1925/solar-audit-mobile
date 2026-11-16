"""
Seeds shared reference data that every installation needs regardless of
which site it belongs to: the equipment asset types and their audit
question checklist (from question_data.py).

This is NOT site-specific data (asset_types/audit_questions/audit_sections
have no site_id â€” they're the shared checklist definition), so it's safe
and correct to run this on every install, and it's idempotent (skips if
already seeded). It deliberately does NOT create any demo Plant/Asset rows
â€” those belong to a real site and are created by real users through the
app after setup, not seeded at install time.

Called automatically once at app startup (see app.py) so a fresh install
has a working checklist immediately, with no manual step required.
"""
from models import Session, AssetType, AuditSection, AuditQuestion
from question_data import (QUESTION_TAXONOMY, SAFETY_SECTION_NAME, SAFETY_SOURCE,
                           SAFETY_CHECKPOINTS, CIVIL_SECTION_NAME, CIVIL_SOURCE,
                           CIVIL_CHECKPOINTS)


def ensure_seeded():
    """Idempotently ensure every shared equipment type and its questions exist.

    Older installations may have been seeded only partially. Do not return just
    because one AssetType exists: add missing types and missing question text
    without deleting or renumbering existing records.
    """
    session = Session()
    try:
        section = session.query(AuditSection).filter_by(name='Checklist').first()
        if section is None:
            section = AuditSection(name='Checklist', section_order=1)
            session.add(section)
            session.commit()

        existing_types = {item.name.strip().casefold(): item
                          for item in session.query(AssetType).all()}
        added_types = 0
        added_questions = 0
        reconciled_questions = 0
        for asset_type_name, questions in QUESTION_TAXONOMY:
            key = asset_type_name.strip().casefold()
            asset_type = existing_types.get(key)
            if asset_type is None:
                asset_type = AssetType(name=asset_type_name)
                session.add(asset_type)
                session.commit()
                existing_types[key] = asset_type
                added_types += 1

            existing_rows = session.query(AuditQuestion).filter_by(
                asset_type_id=asset_type.id
            ).order_by(AuditQuestion.id).all()

            if key == 'inverters':
                # Inverters is a canonical, version-controlled checklist.
                # Keep historical question rows for existing audit responses;
                # retire obsolete/duplicate rows rather than deleting them.
                from datetime import datetime, timezone
                rows_by_text = {}
                for row in existing_rows:
                    rows_by_text.setdefault(
                        row.question_text.strip().casefold(), []
                    ).append(row)

                expected_texts = {
                    text.strip().casefold()
                    for text in questions
                }
                retired_at = datetime.now(timezone.utc).replace(tzinfo=None)
                canonical_ids = set()

                for order, text in enumerate(questions, start=1):
                    normalized = text.strip().casefold()
                    matches = rows_by_text.get(normalized, [])

                    if not matches:
                        session.add(AuditQuestion(
                            asset_type_id=asset_type.id,
                            section_id=section.id,
                            question_text=text,
                            question_order=order,
                            is_active=True,
                        ))
                        added_questions += 1
                        continue

                    # Retain the oldest matching row as the canonical record.
                    canonical = matches[0]
                    canonical_ids.add(canonical.id)
                    canonical.section_id = section.id
                    canonical.question_order = order
                    canonical.is_active = True
                    canonical.retired_at = None

                    # Duplicate rows with the same canonical text are retired.
                    for duplicate in matches[1:]:
                        duplicate.is_active = False
                        if duplicate.retired_at is None:
                            duplicate.retired_at = retired_at

                # Retire old Inverter questions not present in question_data.py.
                for row in existing_rows:
                    if row.id not in canonical_ids and (
                        row.question_text.strip().casefold() not in expected_texts
                    ):
                        if row.is_active:
                            row.is_active = False
                            row.retired_at = retired_at
                            reconciled_questions += 1
            else:
                # Preserve the existing add-only behavior for all other
                # equipment checklists.
                existing_question_texts = {
                    row.question_text.strip().casefold()
                    for row in existing_rows
                }
                for order, text in enumerate(questions, start=1):
                    if text.strip().casefold() in existing_question_texts:
                        continue
                    session.add(AuditQuestion(
                        asset_type_id=asset_type.id,
                        section_id=section.id,
                        question_text=text,
                        question_order=order,
                    ))
                    existing_question_texts.add(text.strip().casefold())
                    added_questions += 1
            session.commit()

        print(f'Checklist reconciliation complete: {added_types} asset types added, '
              f'{added_questions} questions added, '
              f'{reconciled_questions} obsolete Inverter questions retired; '
              f'existing records preserved.')
        return bool(added_types or added_questions or reconciled_questions)
    finally:
        session.close()


def ensure_safety_checklist():
    """
    Idempotently make sure the Safety Audit has a checklist. Safety questions
    live in their own AuditSection ('Safety') with no equipment type, which
    keeps the technical equipment -> checkpoint structure untouched. Runs on
    existing installs too (ensure_seeded() above returns early once equipment
    exists, so it could never add this). Never overwrites or removes anything.
    """
    session = Session()
    try:
        section = session.query(AuditSection).filter_by(name=SAFETY_SECTION_NAME).first()
        if section is None:
            order = (session.query(AuditSection).count() or 0) + 1
            section = AuditSection(name=SAFETY_SECTION_NAME, section_order=order)
            session.add(section)
            session.commit()
        existing = session.query(AuditQuestion).filter_by(section_id=section.id).count()
        if existing:
            return 0
        for i, text in enumerate(SAFETY_CHECKPOINTS, start=1):
            session.add(AuditQuestion(section_id=section.id, asset_type_id=None, question_text=text,
                                      question_order=i, is_active=True, source=SAFETY_SOURCE))
        session.commit()
        print(f'Seeded Safety checklist: {len(SAFETY_CHECKPOINTS)} checkpoints.')
        return len(SAFETY_CHECKPOINTS)
    finally:
        session.close()


def ensure_civil_checklist():
    """Ensure Civil / Infrastructure exists as a site-level checkpoint audit."""
    session = Session()
    try:
        section = session.query(AuditSection).filter_by(name=CIVIL_SECTION_NAME).first()
        if section is None:
            order = (session.query(AuditSection).count() or 0) + 1
            section = AuditSection(name=CIVIL_SECTION_NAME, section_order=order)
            session.add(section)
            session.commit()
        existing = session.query(AuditQuestion).filter_by(section_id=section.id).count()
        if existing:
            return 0
        for i, text in enumerate(CIVIL_CHECKPOINTS, start=1):
            session.add(AuditQuestion(
                section_id=section.id, asset_type_id=None,
                question_text=text, question_order=i,
                is_active=True, source=CIVIL_SOURCE
            ))
        session.commit()
        print(f'Seeded Civil / Infrastructure checklist: {len(CIVIL_CHECKPOINTS)} checkpoints.')
        return len(CIVIL_CHECKPOINTS)
    finally:
        session.close()


# Reference-data provisioning is intentionally invoked through
# provision_reference_data.py with an explicit confirmation flag.
