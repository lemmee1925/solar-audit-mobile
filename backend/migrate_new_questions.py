"""
One-time migration: replace the old demo asset types/questions with the new
question taxonomy (question_data.py), on a database that has already been
seeded once.

WHAT THIS DELETES:
  - All AuditResponsePhoto, AuditResponse, AuditAsset rows (equipment
    inspection data recorded so far)
  - All Asset rows (equipment instances like "Inverter-RSP-001")
  - All AuditQuestion, AssetType, AuditSection rows (the old question set)

WHAT THIS KEEPS:
  - Plant rows
  - User rows
  - Audit rows themselves (the header: plant, date, status, auditor) — but
    since their equipment/response detail is gone, an already-submitted
    audit's PDF report will now show no equipment data. Only run this if
    you're OK with that for any existing submitted audits.

Run this ONCE against your real database:
    python migrate_new_questions.py

------------------------------------------------------------------------------
ADDITIVE EXCEL IMPORT (does NOT delete anything) - same file, pass Excel files:
Import audit checkpoints from Excel into the existing checklist.

    python import_checkpoints.py FILE.xlsx [FILE2.xlsx ...]            # DRY RUN (default, changes nothing)
    python import_checkpoints.py FILE.xlsx FILE2.xlsx --apply          # write to the database
    python import_checkpoints.py SAFETY.xlsx --safety --apply          # load Safety checkpoints
    python import_checkpoints.py FILE.xlsx --create-type PSS --apply   # explicitly create a new equipment type

What it does
  * Reads every sheet. Finds the header row and the columns itself (equipment /
    checkpoint / optional weight), so exact Excel layout does not matter. If a
    sheet has no equipment column, the sheet name is used as the equipment.
  * Maps each row to an EXISTING equipment type: exact match (ignoring case,
    punctuation, spacing) -> alias in config/checkpoint_aliases.json -> a very
    close spelling match. Anything else is FLAGGED, never guessed.
  * Skips exact duplicates (same equipment + same normalised text) and flags
    near-duplicates for a human decision.
  * Never discards a row silently: every row ends up as ADDED, DUPLICATE,
    POSSIBLE_DUPLICATE, UNMAPPED, or EMPTY in the report written next to the DB.
  * Never deletes or edits existing checkpoints. New ones are appended after
    the existing ones for that equipment, with source = 'excel:<filename>'.
  * No Section/Category hierarchy is created: equipment -> checkpoints only.

Resolving flags: add `"Excel name": "Existing equipment type"` to
<config dir>/checkpoint_aliases.json (created on first run) or pass
--create-type NAME for a genuinely new equipment type (e.g. PSS), then re-run;
re-running is safe because duplicates are skipped.
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
from datetime import datetime

from models import (CorrectiveAction, Session, AssetType, AuditSection, AuditQuestion,
                     Asset, AuditAsset, AuditResponse, AuditResponsePhoto, Plant)
from paths import config_dir, data_dir
from question_data import QUESTION_TAXONOMY, SAFETY_SECTION_NAME


def confirm():
    print("This will permanently delete all existing equipment, questions,")
    print("and recorded inspection data (Plants, Users, and Audit headers are kept).")
    answer = input("Type YES to continue: ").strip()
    return answer == 'YES'


def migrate():
    if not confirm():
        print("Aborted — nothing was changed.")
        return

    session = Session()
    try:
        print("Deleting old data...")
        session.query(AuditResponsePhoto).delete()
        session.query(CorrectiveAction).delete()
        session.query(AuditResponse).delete()
        session.query(AuditAsset).delete()
        session.query(Asset).delete()
        session.query(AuditQuestion).delete()
        session.query(AssetType).delete()
        session.query(AuditSection).delete()
        session.commit()

        print("Loading new question taxonomy...")
        section = AuditSection(name='Checklist', section_order=1)
        session.add(section)
        session.commit()

        asset_types = {}
        question_count = 0
        for asset_type_name, questions in QUESTION_TAXONOMY:
            at = AssetType(name=asset_type_name)
            session.add(at)
            session.commit()
            asset_types[asset_type_name] = at

            for i, text in enumerate(questions, start=1):
                q = AuditQuestion(
                    asset_type_id=at.id,
                    section_id=section.id,
                    question_text=text,
                    question_order=i,
                )
                session.add(q)
                question_count += 1
            session.commit()

        # Re-create one demo equipment instance per existing plant per new
        # asset type, so the equipment dropdown isn't empty. Rename/replace
        # these with real equipment names via "Add Custom Equipment" in the app.
        plants = session.query(Plant).all()
        asset_count = 0
        for plant in plants:
            code = ''.join(w[0] for w in plant.name.split()[:3]).upper()
            for asset_type in asset_types.values():
                asset_name = f"{asset_type.name} - {code}"
                session.add(Asset(plant_id=plant.id, asset_type_id=asset_type.id, asset_name=asset_name))
                asset_count += 1
            session.commit()

        print(f"Done. {len(asset_types)} asset types, {question_count} questions, "
              f"{asset_count} demo equipment instances across {len(plants)} plants.")

    finally:
        session.close()


IMPORT_HELP = """Additive Excel checkpoint import (dry run unless --apply)."""

# ---------------------------------------------------------------------------
# Additive Excel import
# ---------------------------------------------------------------------------
EQUIP_HEADERS = ('equipment', 'asset', 'asset type', 'equipment type', 'equipment name', 'system',
                 'component', 'category', 'area', 'description of equipment')
CHECK_HEADERS = ('checkpoint', 'check point', 'checkpoints', 'check points', 'question', 'questions',
                 'checklist', 'check list', 'parameter', 'parameters', 'audit point', 'audit checkpoint',
                 'inspection point', 'description', 'observation point', 'point to check', 'check')
WEIGHT_HEADERS = ('weight', 'weightage', 'weightage %', 'score', 'marks', 'points')
SAFETY_HEADERS = ('safety', 'is safety', 'audit type', 'type')


def _norm_text(text):
    t = re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()
    return re.sub(r'\s+', ' ', t)


def clean(v):
    return re.sub(r'\s+', ' ', str(v).strip()) if v is not None else ''


def find_columns(row_values):
    cols = {}
    for i, v in enumerate(row_values):
        n = _norm_text(clean(v))
        if not n:
            continue
        if 'equip' not in cols and n in EQUIP_HEADERS:
            cols['equip'] = i
        elif 'check' not in cols and n in CHECK_HEADERS:
            cols['check'] = i
        elif 'weight' not in cols and n in WEIGHT_HEADERS:
            cols['weight'] = i
        elif 'safety' not in cols and n in SAFETY_HEADERS:
            cols['safety'] = i
    return cols


def read_rows(path):
    """Yield dicts: file, sheet, row, equipment, text, weight, safety_flag."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        values = [list(r) for r in ws.iter_rows(values_only=True)]
        header_idx, cols = None, {}
        for idx, r in enumerate(values[:30]):
            c = find_columns(r)
            if 'check' in c:
                header_idx, cols = idx, c
                break
        if header_idx is None:
            # no recognisable header: treat the widest text column as the checkpoint column
            yield {'file': os.path.basename(path), 'sheet': ws.title, 'row': 0, 'equipment': '', 'text': '',
                   'weight': None, 'safety_flag': '', 'problem': 'No header row with a checkpoint/question column found in this sheet'}
            continue
        last_equipment = ''
        for idx in range(header_idx + 1, len(values)):
            r = values[idx]
            get = lambda k: clean(r[cols[k]]) if k in cols and cols[k] < len(r) else ''
            equip = get('equip') or ''
            if equip:
                last_equipment = equip  # merged / "fill-down" equipment cells
            elif 'equip' in cols:
                equip = last_equipment
            else:
                equip = ws.title
            text = get('check')
            if not text and not any(clean(x) for x in r):
                continue  # fully blank line
            w = None
            raw_w = get('weight')
            if raw_w:
                try:
                    w = float(str(raw_w).replace('%', ''))
                except ValueError:
                    w = None
            yield {'file': os.path.basename(path), 'sheet': ws.title, 'row': idx + 1, 'equipment': equip,
                   'text': text, 'weight': w, 'safety_flag': get('safety'), 'problem': ''}


def load_aliases():
    path = os.path.join(str(config_dir()), 'checkpoint_aliases.json')
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({"_comment": "Map an Excel equipment name to an existing equipment type, e.g. "
                                   "\"Solar Modules\": \"Module Array & MMS\"", }, fh, indent=2)
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            return {_norm_text(k): v for k, v in json.load(fh).items() if not k.startswith('_')}
    except (OSError, ValueError):
        return {}


def map_equipment(name, types_by_norm, aliases):
    """-> (AssetType or None, how). Conservative on purpose."""
    n = _norm_text(name)
    if not n:
        return None, 'no equipment name'
    if n in types_by_norm:
        return types_by_norm[n], 'exact'
    if n in aliases and _norm_text(aliases[n]) in types_by_norm:
        return types_by_norm[_norm_text(aliases[n])], 'alias'
    close = difflib.get_close_matches(n, list(types_by_norm), n=2, cutoff=0.92)
    if len(close) == 1:
        return types_by_norm[close[0]], f'close spelling ("{close[0]}")'
    return None, 'no confident match'


def import_run(files, apply=False, safety=False, create_types=(), replace_safety=False):
    session = Session()
    results = []
    counts = {}
    try:
        aliases = load_aliases()
        types = session.query(AssetType).all()
        types_by_norm = {_norm_text(t.name): t for t in types}

        for name in create_types:
            if _norm_text(name) not in types_by_norm:
                if apply:
                    t = AssetType(name=name.strip())
                    session.add(t)
                    session.flush()
                    types_by_norm[_norm_text(name)] = t
                else:
                    types_by_norm[_norm_text(name)] = AssetType(id=-1, name=name.strip())  # placeholder for dry run

        section = None
        if safety:
            section = session.query(AuditSection).filter_by(name=SAFETY_SECTION_NAME).first()
            if section is None:
                section = AuditSection(name=SAFETY_SECTION_NAME, section_order=99)
                if apply:
                    session.add(section)
                    session.flush()
        else:
            section = session.query(AuditSection).filter_by(name='Checklist').first() or \
                session.query(AuditSection).order_by(AuditSection.id).first()

        # existing normalised texts per scope: asset_type_id (or 'safety') -> {norm_text: question}
        existing, next_order = {}, {}

        def scope_key(t):
            return 'safety' if safety else t.id

        def load_scope(key, t):
            if key in existing:
                return
            q = session.query(AuditQuestion)
            if safety:
                q = q.filter(AuditQuestion.section_id == (section.id if section.id else -1))
            else:
                q = q.filter(AuditQuestion.asset_type_id == t.id)
            rows = q.all()
            existing[key] = {_norm_text(x.question_text): x for x in rows}
            next_order[key] = max([x.question_order or 0 for x in rows] + [0]) + 1

        if safety and replace_safety and apply and section is not None and section.id:
            for q in session.query(AuditQuestion).filter(AuditQuestion.section_id == section.id,
                                                         AuditQuestion.source.like('seed:%')).all():
                if q.is_active is not False:
                    q.is_active = False
                    q.retired_at = datetime.utcnow()
            session.flush()

        for path in files:
            src = 'excel:' + os.path.basename(path)
            for r in read_rows(path):
                res = dict(r)
                res.update({'status': '', 'mapped_to': '', 'detail': ''})
                results.append(res)
                if r['problem']:
                    res.update(status='UNMAPPED', detail=r['problem'])
                    continue
                if not r['text']:
                    res.update(status='EMPTY', detail='Row has no checkpoint text (equipment/other cells present)')
                    continue

                if safety:
                    t, how = None, 'safety'
                    res['mapped_to'] = SAFETY_SECTION_NAME
                    key = 'safety'
                else:
                    t, how = map_equipment(r['equipment'], types_by_norm, aliases)
                    if t is None:
                        res.update(status='UNMAPPED', detail=f'Equipment "{r["equipment"]}": {how}. '
                                   'Add an alias or use --create-type.')
                        continue
                    res['mapped_to'] = f'{t.name} ({how})'
                    key = scope_key(t)
                if (t is not None and t.id == -1) and not apply:
                    existing.setdefault(key, {})
                    next_order.setdefault(key, 1)
                else:
                    load_scope(key, t)

                n = _norm_text(r['text'])
                if n in existing[key]:
                    res.update(status='DUPLICATE', detail='Identical checkpoint already exists for this equipment'
                               + (' (retired)' if existing[key][n].is_active is False else ''))
                    continue
                near = difflib.get_close_matches(n, list(existing[key]), n=1, cutoff=0.93)
                if near:
                    res.update(status='POSSIBLE_DUPLICATE', detail=f'Very similar to existing: "{existing[key][near[0]].question_text}". '
                               'Not added; add manually if it is genuinely different.')
                    continue

                if apply:
                    q = AuditQuestion(section_id=section.id if section else None,
                                      asset_type_id=None if safety else t.id,
                                      question_text=r['text'], question_order=next_order[key],
                                      is_active=True, source=src, weight=r['weight'])
                    session.add(q)
                    existing[key][n] = q
                else:
                    existing[key][n] = type('Q', (), {'question_text': r['text'], 'is_active': True})()
                next_order[key] += 1
                res.update(status='ADDED' if apply else 'WOULD_ADD')

        if apply:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return results, counts


def write_report(results, apply):
    out = os.path.join(str(data_dir()), f'checkpoint_import_{"APPLIED" if apply else "DRYRUN"}_'
                       f'{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
    with open(out, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['status', 'file', 'sheet', 'excel_row', 'equipment_in_excel', 'mapped_to', 'checkpoint', 'weight', 'detail'])
        for r in results:
            w.writerow([r['status'], r['file'], r['sheet'], r['row'], r['equipment'], r['mapped_to'],
                        r['text'], r['weight'] if r['weight'] is not None else '', r['detail']])
    return out


def import_main(argv=None):
    ap = argparse.ArgumentParser(description=IMPORT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--apply', action='store_true', help='write to the database (default is a dry run)')
    ap.add_argument('--safety', action='store_true', help='load these files as Safety checkpoints')
    ap.add_argument('--replace-safety', action='store_true',
                    help='with --safety --apply: retire (soft-delete) the built-in default safety checkpoints first')
    ap.add_argument('--create-type', action='append', default=[], metavar='NAME',
                    help='create a new equipment type with this name if it does not exist (repeatable)')
    a = ap.parse_args(argv)
    for f in a.files:
        if not os.path.exists(f):
            sys.exit(f'File not found: {f}')
    results, counts = import_run(a.files, a.apply, a.safety, a.create_type, a.replace_safety)
    report = write_report(results, a.apply)
    print(('APPLIED' if a.apply else 'DRY RUN (nothing written; add --apply to import)'))
    total = len(results)
    print(f'Rows read: {total}')
    for k in ('ADDED', 'WOULD_ADD', 'DUPLICATE', 'POSSIBLE_DUPLICATE', 'UNMAPPED', 'EMPTY'):
        if counts.get(k):
            print(f'  {k:<20} {counts[k]}')
    per_file = {}
    for r in results:
        if r['status'] in ('ADDED', 'WOULD_ADD'):
            per_file[r['file']] = per_file.get(r['file'], 0) + 1
    for f, n in per_file.items():
        print(f'  new checkpoints from {f}: {n}')
    print(f'Report (every row, with reasons): {report}')
    if counts.get('UNMAPPED'):
        print('Some rows could not be mapped confidently - see UNMAPPED rows in the report.')


if __name__ == '__main__':
    # With Excel file arguments: additive import (safe, dry-run by default).
    # With no arguments: the original destructive migrate() exactly as before.
    if len(sys.argv) > 1:
        import_main()
    else:
        migrate()
