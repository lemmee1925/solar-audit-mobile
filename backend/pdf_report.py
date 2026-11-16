import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether, HRFlowable
from reportlab.lib.colors import HexColor
from sqlalchemy import func

from models import (
    Session, Audit, Plant, AuditSiteDetail, AuditAsset, Asset, AssetType,
    AuditResponse, AuditResponsePhoto, AuditQuestion, AuditSection,
    CorrectiveAction, Observation, ObservationPhoto
)
from site_scope import (SEVERITY_LABELS, UNFINISHED, display_status, backfill_observations,
                        score_audit)
from question_data import SAFETY_SECTION_NAME, CIVIL_SECTION_NAME
from paths import photos_dir, reports_dir, static_dir
from version import APP_VERSION

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = str(photos_dir())
LOGO_PATH = str(static_dir() / 'cleanmax_logo.png')

# Clean, systematic report palette
GREEN = HexColor('#7CB859')
GREEN_DARK = HexColor('#4C7D37')
GREEN_LIGHT = HexColor('#EAF4E3')
NAVY = HexColor('#14243A')
NAVY_2 = HexColor('#20344E')
GRAY_DARK = HexColor('#343A40')
GRAY = HexColor('#69727D')
GRAY_LIGHT = HexColor('#F3F5F7')
BORDER = HexColor('#DCE1E6')
RED = HexColor('#D64545')
RED_LIGHT = HexColor('#FCEAEA')
AMBER = HexColor('#D99A22')
WHITE = colors.white


RESP_LABEL = {'pass': 'Complied', 'fail': 'Not Complied', 'na': 'N/A', 'ofi': 'Opportunity for Improvement'}


def resp_label(status):
    return RESP_LABEL.get((status or 'na').lower(), 'N/A')


def sev_color(sev):
    return {'critical': RED, 'major': AMBER}.get((sev or '').lower(), GRAY)


def safe(value, fallback='N/A'):
    if value is None or str(value).strip() == '':
        return fallback
    return str(value)


def score_color(pct):
    if pct >= 85:
        return GREEN_DARK
    if pct >= 60:
        return AMBER
    return RED


def make_bar(pct, width=5.5 * cm, height=0.22 * cm):
    pct = max(0, min(100, float(pct)))
    filled = width * pct / 100.0
    empty = width - filled
    if filled <= 0:
        t = Table([['']], colWidths=[width], rowHeights=[height])
        t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), GRAY_LIGHT), ('BOX',(0,0),(-1,-1),0.4,BORDER)]))
        return t
    if empty <= 0:
        t = Table([['']], colWidths=[width], rowHeights=[height])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),score_color(pct))]))
        return t
    t = Table([['','']], colWidths=[filled, empty], rowHeights=[height])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(0,0),score_color(pct)),
        ('BACKGROUND',(1,0),(1,0),GRAY_LIGHT),
    ]))
    return t


def styles():
    return {
        'section': ParagraphStyle('section', fontSize=15, leading=18, textColor=NAVY, fontName='Helvetica-Bold'),
        'subtitle': ParagraphStyle('subtitle', fontSize=8.5, leading=11, textColor=GRAY, fontName='Helvetica'),
        'body': ParagraphStyle('body', fontSize=8.3, leading=11, textColor=GRAY_DARK, fontName='Helvetica'),
        'small': ParagraphStyle('small', fontSize=7.2, leading=9.2, textColor=GRAY_DARK, fontName='Helvetica'),
        'label': ParagraphStyle('label', fontSize=7, leading=8.5, textColor=GRAY, fontName='Helvetica-Bold'),
        'header': ParagraphStyle('header', fontSize=7, leading=8.5, textColor=WHITE, fontName='Helvetica-Bold'),
        'equip': ParagraphStyle('equip', fontSize=9.5, leading=11, textColor=WHITE, fontName='Helvetica-Bold'),
        'cover_title': ParagraphStyle('cover_title', fontSize=23, leading=27, textColor=WHITE, fontName='Helvetica-Bold'),
        'cover_sub': ParagraphStyle('cover_sub', fontSize=12, leading=14, textColor=GREEN, fontName='Helvetica-Bold'),
        'cover_meta': ParagraphStyle('cover_meta', fontSize=8.5, leading=11, textColor=HexColor('#C7CDD4'), fontName='Helvetica'),
        'kpi_num': ParagraphStyle('kpi_num', fontSize=18, leading=20, textColor=NAVY, fontName='Helvetica-Bold', alignment=TA_CENTER),
        'kpi_lbl': ParagraphStyle('kpi_lbl', fontSize=6.6, leading=8, textColor=GRAY, fontName='Helvetica-Bold', alignment=TA_CENTER),
    }


def section_heading(number, title, subtitle, st):
    return [
        Paragraph(f'<font color="#7CB859">{number}</font>&nbsp;&nbsp;{title}', st['section']),
        Spacer(1, 2),
        Paragraph(subtitle, st['subtitle']),
        Spacer(1, 5),
        HRFlowable(width='100%', thickness=1, color=GREEN, spaceAfter=7),
    ]


def rank_legend(content_width, st):
    # ONE compact ranking legend for the entire report. Never repeat per equipment.
    labels = [
        ('5', 'Excellent', GREEN_DARK), ('4', 'Good', GREEN_DARK),
        ('3', 'Fair', AMBER), ('2', 'Poor', RED),
        ('1', 'Unacceptable', RED), ('N/A', 'Not Applicable', GRAY)
    ]
    row = []
    for number, text, color in labels:
        row.append(Paragraph(f'<font color="{color.hexval()}"><b>{number}</b></font> {text}',
                             ParagraphStyle(f'r{number}', fontSize=7.2, leading=8.5, textColor=GRAY_DARK, alignment=TA_CENTER)))
    t = Table([row], colWidths=[content_width/6.0]*6)
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),GRAY_LIGHT), ('BOX',(0,0),(-1,-1),0.5,BORDER),
        ('INNERGRID',(0,0),(-1,-1),0.3,BORDER), ('TOPPADDING',(0,0),(-1,-1),4),
        ('BOTTOMPADDING',(0,0),(-1,-1),4), ('VALIGN',(0,0),(-1,-1),'MIDDLE')
    ]))
    return t



def _resolve_photo_path(photo):
    """Resolve AuditResponsePhoto.file_path to the real local image file."""
    if not photo or not photo.file_path:
        return None

    stored = str(photo.file_path).replace("\\", "/").lstrip("/")
    parent_dir = os.path.dirname(BASE_DIR)
    candidates = [
        stored,
        os.path.join(UPLOAD_FOLDER, stored),
        os.path.join(BASE_DIR, "uploads", stored),
        os.path.join(BASE_DIR, stored),
        # Covers the historical bug where app.py used a bare relative 'uploads'
        # path: if the app was ever launched from the parent directory, photos
        # would have been saved one level above BASE_DIR instead of inside it.
        os.path.join(parent_dir, "uploads", stored),
        os.path.join(parent_dir, stored),
    ]

    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate):
            return candidate

    print(f"[PDF PHOTO] File not found: {photo.file_path}")
    return None


def _get_response_photos(session, response_id):
    """Exact photo relation from this project's models.py."""
    return (
        session.query(AuditResponsePhoto)
        .filter(AuditResponsePhoto.audit_response_id == response_id)
        .order_by(AuditResponsePhoto.uploaded_at.asc())
        .all()
    )


def _make_photo_image(photo, max_width=7.0 * cm, max_height=5.0 * cm):
    path = _resolve_photo_path(photo)
    if not path:
        return None

    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as pil_img:
            width_px, height_px = pil_img.size

        if width_px <= 0 or height_px <= 0:
            return None

        scale = min(
            max_width / float(width_px),
            max_height / float(height_px),
            1.0,
        )
        return Image(
            path,
            width=width_px * scale,
            height=height_px * scale,
            hAlign='CENTER',
        )
    except Exception as exc:
        print(f"[PDF PHOTO] Cannot read {path}: {exc}")
        return None


def _photo_evidence(session, responses, content_width, st):
    """Render every stored photo for the current equipment's responses."""
    items = []
    for resp, question, section in responses:
        for photo in _get_response_photos(session, resp.id):
            image = _make_photo_image(photo)
            if image is not None:
                items.append((image, safe(question.question_text, 'Inspection Point')))

    if not items:
        return []

    output = [
        Spacer(1, 0.18 * cm),
        Paragraph(
            'Photo Evidence',
            ParagraphStyle(
                'photo_heading', fontSize=8.5, leading=10,
                textColor=NAVY, fontName='Helvetica-Bold'
            )
        ),
        Spacer(1, 0.12 * cm),
    ]

    cell_width = (content_width - 0.35 * cm) / 2.0
    rows = []
    for i in range(0, len(items), 2):
        pair = items[i:i + 2]
        cells = []
        for image, question_text in pair:
            cells.append([
                image,
                Spacer(1, 2),
                Paragraph(
                    f'<b>Inspection Evidence</b><br/>{question_text}',
                    st['small']
                )
            ])
        while len(cells) < 2:
            cells.append('')
        rows.append(cells)

    table = Table(rows, colWidths=[cell_width, cell_width], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), WHITE),
        ('BOX', (0, 0), (-1, -1), 0.45, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.35, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    output.append(table)
    return output

def generate_pdf(audit_id_str, site_id=None):
    """
    Render the audit report PDF. If site_id is provided, the audit must
    belong to that site or generation is refused — the caller (app.py) is
    expected to have already fetched/validated the audit against the
    current site, but this second check is cheap and keeps this function
    safe to call directly (e.g. from the export feature) without relying
    solely on the caller.
    """
    session = Session()
    try:
        audit_id_str = str(audit_id_str)
        query = session.query(Audit).filter_by(id=audit_id_str)
        if site_id:
            query = query.filter_by(site_id=site_id)
        audit = query.first()
        if not audit:
            return None, 'Audit not found.'

        # Audits recorded before observations existed: make sure their Not Complied
        # answers have observation rows so the report is internally consistent.
        try:
            if backfill_observations(session, audit.site_id):
                session.commit()
        except Exception:
            session.rollback()

        photo_count = (
            session.query(AuditResponsePhoto)
            .join(AuditResponse, AuditResponsePhoto.audit_response_id == AuditResponse.id)
            .filter(AuditResponse.audit_id == audit.id)
            .count()
        )
        print(f'[PDF PHOTO] Audit {audit.id}: {photo_count} photo record(s) found')
        plant = session.query(Plant).filter_by(id=audit.plant_id).first()
        site_detail = session.query(AuditSiteDetail).filter_by(audit_id=audit.id).first()

        pdf_path = os.path.join(str(reports_dir()), f'report_{audit.audit_number or audit_id_str}.pdf')
        st = styles()
        W, H = A4
        content_width = W - 3.6 * cm
        elements = []

        # ------------------------------------------------------------------
        # COVER
        # ------------------------------------------------------------------
        logo = ''
        if os.path.exists(LOGO_PATH):
            try:
                logo = Image(LOGO_PATH, width=3.4*cm, height=1.05*cm)
            except Exception:
                pass
        top = Table([[logo, Paragraph('TECHNICAL &amp; OPERATIONAL AUDIT REPORT',
            ParagraphStyle('cover_right',fontSize=7.5,textColor=HexColor('#D7DDE3'),fontName='Helvetica-Bold',alignment=TA_RIGHT))]],
            colWidths=[6*cm, content_width-6*cm])
        top.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),('LEFTPADDING',(0,0),(-1,-1),14),('RIGHTPADDING',(0,0),(-1,-1),14),('TOPPADDING',(0,0),(-1,-1),18),('BOTTOMPADDING',(0,0),(-1,-1),10),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        elements.append(top)
        cover = Table([
            [Paragraph('Solar Plant<br/>O&amp;M Audit Report', st['cover_title'])],
            [Paragraph(safe(plant.name if plant else None, 'Solar Plant'), st['cover_sub'])],
            [Paragraph('Field Inspection &amp; Asset Health Assessment', st['cover_meta'])]
        ], colWidths=[content_width])
        cover.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),('LEFTPADDING',(0,0),(-1,-1),14),('RIGHTPADDING',(0,0),(-1,-1),14),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),18)]))
        elements.append(cover)
        strip = Table([['']], colWidths=[content_width], rowHeights=[0.16*cm])
        strip.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN)]))
        elements += [strip, Spacer(1,0.6*cm)]

        info = [
            ['Audit No.', safe(audit.audit_number, audit.id)], ['Plant', safe(plant.name if plant else None)],
            ['Site ID', safe(audit.site_id)],
            ['Location', safe(plant.location if plant else None)], ['Audit Date / Time', safe(audit.audit_date) + (f'  {audit.audit_time}' if audit.audit_time else '')],
            ['Auditor', safe(audit.auditor_name)], ['Auditee', safe(audit.auditee_name)],
            ['Audit Type', safe(audit.audit_type)], ['Status', safe(audit.status,'Draft').upper()],
            ['Generated', datetime.now().strftime('%d %b %Y, %H:%M') + f'  (Solar Audit v{APP_VERSION})']
        ]
        t = Table([[Paragraph(k,st['label']),Paragraph(v,st['body'])] for k,v in info], colWidths=[4*cm,content_width-4*cm])
        t.setStyle(TableStyle([('ROWBACKGROUNDS',(0,0),(-1,-1),[GRAY_LIGHT,WHITE]),('BOX',(0,0),(-1,-1),0.6,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        elements.append(t)
        elements += [Spacer(1,0.6*cm), Paragraph('This report consolidates field observations, equipment-level inspection results, compliance scoring and corrective actions recorded during the audit.', st['body']), PageBreak()]

        # ------------------------------------------------------------------
        # EXECUTIVE SUMMARY
        # ------------------------------------------------------------------
        elements += section_heading('01','Executive Summary','Management-level view of audit coverage, compliance and open actions.',st)
        total_assets = session.query(func.count(AuditAsset.id)).filter(AuditAsset.audit_id==audit.id, AuditAsset.is_audited==True).scalar() or 0
        result = score_audit(session, audit.id)
        total_responses = result['complied'] + result['not_complied'] + result['not_applicable']
        pass_count, fail_count = result['complied'], result['not_complied']
        # Submitted audits show the score frozen at submission; drafts show a live preview.
        if audit.score_pct is not None:
            score_pct, overall, rating = audit.score_pct, audit.compliance_pct or 0.0, audit.site_rating or result['rating']
        else:
            score_pct, overall, rating = result['score_pct'], result['compliance_pct'], result['rating']
        obs_all = session.query(Observation).filter(Observation.audit_id==audit.id, Observation.status!='withdrawn').all()
        open_ca = sum(1 for o in obs_all if o.status in UNFINISHED)
        sev_n = {k: sum(1 for o in obs_all if o.severity == k) for k in ('critical', 'major', 'minor')}

        kpi = Table([
            [Paragraph(str(total_assets),st['kpi_num']),Paragraph(str(total_responses),st['kpi_num']),Paragraph(str(pass_count),st['kpi_num']),Paragraph(str(fail_count),st['kpi_num']),Paragraph(str(len(obs_all)),st['kpi_num'])],
            [Paragraph('EQUIPMENT<br/>AUDITED',st['kpi_lbl']),Paragraph('CHECKPOINTS<br/>ANSWERED',st['kpi_lbl']),Paragraph('COMPLIED',st['kpi_lbl']),Paragraph('NOT<br/>COMPLIED',st['kpi_lbl']),Paragraph('OBSERVATIONS<br/>RAISED',st['kpi_lbl'])]
        ], colWidths=[content_width/5.0]*5)
        kpi.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LIGHT),('BOX',(0,0),(-1,-1),0.6,BORDER),('INNERGRID',(0,0),(-1,-1),0.4,WHITE),('TOPPADDING',(0,0),(-1,0),9),('BOTTOMPADDING',(0,0),(-1,0),2),('TOPPADDING',(0,1),(-1,1),2),('BOTTOMPADDING',(0,1),(-1,1),9)]))
        elements += [kpi, Spacer(1,0.45*cm)]

        def score_row(label, pct, right):
            return [Paragraph(label,st['label']),make_bar(pct,7.0*cm,0.28*cm),Paragraph(f'<b>{right}</b>',ParagraphStyle('score',fontSize=11,textColor=score_color(pct),fontName='Helvetica-Bold',alignment=TA_RIGHT))]
        score = Table([score_row('AUDIT SCORE (SEVERITY-WEIGHTED)', score_pct, f'{score_pct:.1f}%'),
                       score_row('COMPLIANCE (COMPLIED / APPLICABLE)', overall, f'{overall:.1f}%'),
                       [Paragraph('SITE RATING',st['label']),'',Paragraph(f'<b>{safe(rating)}</b>',ParagraphStyle('rate',fontSize=11,textColor=score_color(score_pct),fontName='Helvetica-Bold',alignment=TA_RIGHT))]],
                      colWidths=[5.6*cm,7.2*cm,content_width-12.8*cm])
        score.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.6,BORDER),('LINEBELOW',(0,0),(-1,1),0.3,BORDER),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        elements += [score, Spacer(1,0.3*cm)]

        sevt = Table([[Paragraph('Observations by severity',st['label']),Paragraph(f'<font color="#D64545"><b>Critical {sev_n["critical"]}</b></font>',st['small']),Paragraph(f'<font color="#D99A22"><b>Major {sev_n["major"]}</b></font>',st['small']),Paragraph(f'<b>Minor {sev_n["minor"]}</b>',st['small']),Paragraph(f'Open / in follow-up: <b>{open_ca}</b>',st['small'])]],colWidths=[4.6*cm,2.6*cm,2.6*cm,2.6*cm,content_width-12.4*cm])
        sevt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LIGHT),('BOX',(0,0),(-1,-1),0.5,BORDER),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),8)]))
        elements += [sevt, Spacer(1,0.4*cm), Paragraph('Compliance by Equipment Type',ParagraphStyle('subh',fontSize=8.5,textColor=NAVY,fontName='Helvetica-Bold')),Spacer(1,3)]

        type_rows = (session.query(AssetType.name, AuditResponse.response_status)
                     .select_from(AuditResponse)
                     .join(AuditQuestion, AuditResponse.question_id==AuditQuestion.id)
                     .outerjoin(AssetType, AssetType.id==AuditQuestion.asset_type_id)
                     .filter(AuditResponse.audit_id==audit.id).all())
        agg={}
        for name,status in type_rows:
            d=agg.setdefault(name or 'Safety',{'pass':0,'fail':0}); s_=(status or '').lower()
            if s_ in d: d[s_]+=1
        if agg:
            rows=[[Paragraph('Equipment Type',st['header']),Paragraph('Complied',st['header']),Paragraph('Not Complied',st['header']),Paragraph('Score',st['header']),Paragraph('Compliance',st['header'])]]
            for name in sorted(agg):
                d=agg[name]; den=d['pass']+d['fail']; pct=round(d['pass']*100/den,1) if den else 0
                rows.append([Paragraph(name,st['small']),Paragraph(str(d['pass']),st['small']),Paragraph(str(d['fail']),st['small']),Paragraph(f'<b>{pct:.1f}%</b>',ParagraphStyle('sc',fontSize=7.2,textColor=score_color(pct),fontName='Helvetica-Bold',alignment=TA_CENTER)),make_bar(pct,4*cm,0.17*cm)])
            tb=Table(rows,colWidths=[5.5*cm,1.7*cm,2.1*cm,1.7*cm,content_width-11.0*cm],repeatRows=1)
            tb.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,GRAY_LIGHT]),('BOX',(0,0),(-1,-1),0.5,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(1,1),(3,-1),'CENTER'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),5)]))
            elements.append(tb)
        else:
            elements.append(Paragraph('No scored responses recorded yet for this audit.',st['body']))
        elements.append(PageBreak())

        # ------------------------------------------------------------------
        # PLANT INFORMATION
        # ------------------------------------------------------------------
        elements += section_heading('02','Plant & Audit Information','Core identification and site configuration captured for this audit.',st)
        basic=[['Plant Name',safe(plant.name if plant else None)],['Location',safe(plant.location if plant else None)],['Audit ID',safe(audit.id)],['Audit Date',safe(audit.audit_date)],['Audit Time',safe(audit.audit_time)],['Audit Type',safe(audit.audit_type)],['Capacity',safe(audit.capacity_mw)],['Building / Block',safe(audit.building_name)],['City',safe(audit.city)],['State',safe(audit.state)],['Auditor',safe(audit.auditor_name)],['Auditee',safe(audit.auditee_name)]]
        bt=Table([[Paragraph(k,st['label']),Paragraph(v,st['small'])] for k,v in basic],colWidths=[4.2*cm,content_width-4.2*cm])
        bt.setStyle(TableStyle([('ROWBACKGROUNDS',(0,0),(-1,-1),[GRAY_LIGHT,WHITE]),('BOX',(0,0),(-1,-1),0.5,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        elements += [bt,Spacer(1,0.45*cm),Paragraph('Site Capacity &amp; Configuration',ParagraphStyle('subh2',fontSize=8.5,textColor=NAVY,fontName='Helvetica-Bold')),Spacer(1,3)]
        if site_detail:
            site=[['No. of Blocks',site_detail.no_of_blocks],['DC Capacity (MWp)',site_detail.dc_capacity],['AC Capacity (MVA)',site_detail.ac_capacity],['Central Inverter',site_detail.central_inverter_type],['Central Inverter Count',site_detail.central_inverter_count],['String Inverter',site_detail.string_inverter_type],['String Inverter Count',site_detail.string_inverter_count],['SCB Count',site_detail.scb_count],['MMS Type / Count',f'{safe(site_detail.mms_type)} / {safe(site_detail.mms_count)}'],['Transformer Type / Count',f'{safe(site_detail.transformer_type)} / {safe(site_detail.transformer_count)}'],['LT Panel Type / Count',f'{safe(site_detail.lt_panel_type)} / {safe(site_detail.lt_panel_count)}'],['HT Panel Type / Count',f'{safe(site_detail.ht_panel_type)} / {safe(site_detail.ht_panel_count)}'],['ACDB Count',site_detail.acdb_count],['UPS DB + Battery Count',site_detail.ups_db_battery_count],['Lightning Protection',site_detail.lightning_protection_count],['DC Earthing',site_detail.dc_earthing],['AC Earthing',site_detail.ac_earthing]]
            stbl=Table([[Paragraph(k,st['label']),Paragraph(safe(v),st['small'])] for k,v in site],colWidths=[5.5*cm,content_width-5.5*cm])
            stbl.setStyle(TableStyle([('ROWBACKGROUNDS',(0,0),(-1,-1),[WHITE,GRAY_LIGHT]),('BOX',(0,0),(-1,-1),0.5,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),3.5),('BOTTOMPADDING',(0,0),(-1,-1),3.5)]))
            elements.append(stbl)
        else:
            elements.append(Paragraph('Site capacity and configuration details were not recorded.',st['body']))
        elements.append(PageBreak())

        # ------------------------------------------------------------------
        # EQUIPMENT DETAILS
        # ------------------------------------------------------------------
        elements += section_heading('03','Equipment Audit Details','Checkpoint results by equipment (Complied / Not Complied / N/A). The ranking legend is shown once only.',st)
        elements += [rank_legend(content_width,st),Spacer(1,0.3*cm)]

        audit_assets=session.query(AuditAsset,Asset,AssetType).join(Asset,AuditAsset.asset_id==Asset.id).join(AssetType,Asset.asset_type_id==AssetType.id).filter(AuditAsset.audit_id==audit.id).order_by(AssetType.name,Asset.asset_name).all()
        for idx,(aa,asset,atype) in enumerate(audit_assets,1):
            responses=session.query(AuditResponse,AuditQuestion,AuditSection).join(AuditQuestion,AuditResponse.question_id==AuditQuestion.id).join(AuditSection,AuditQuestion.section_id==AuditSection.id).filter(AuditResponse.audit_id==audit.id,AuditResponse.asset_id==asset.id).order_by(AuditSection.section_order,AuditQuestion.question_order).all()
            p=sum(1 for r,_,_ in responses if (r.response_status or '').lower()=='pass'); f=sum(1 for r,_,_ in responses if (r.response_status or '').lower()=='fail'); den=p+f; pct=round(p*100/den,1) if den else 0

            title=Table([[Paragraph(f'{idx:02d}  {safe(asset.asset_name)}  |  {safe(atype.name)}',st['equip']),Paragraph(f'{pct:.0f}%',ParagraphStyle(f'ep{idx}',fontSize=9,textColor=WHITE,fontName='Helvetica-Bold',alignment=TA_RIGHT))]],colWidths=[content_width-2*cm,2*cm])
            title.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            meta=Table([[Paragraph('<b>Location</b><br/>'+safe(aa.location),st['small']),Paragraph('<b>Rank</b><br/>'+safe(aa.rank),st['small']),Paragraph('<b>Audited</b><br/>'+('Yes' if aa.is_audited else 'No'),st['small']),Paragraph('<b>Complied / Not Complied</b><br/>'+f'{p} / {f}',st['small']),Paragraph('<b>Compliance</b><br/>'+f'{pct:.1f}%',st['small'])]],colWidths=[3.6*cm,2.0*cm,2.0*cm,4.2*cm,content_width-11.8*cm])
            meta.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LIGHT),('BOX',(0,0),(-1,-1),0.4,BORDER),('INNERGRID',(0,0),(-1,-1),0.3,BORDER),('LEFTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            elements += [KeepTogether([title,meta])]
            if aa.observation or aa.remarks:
                obs=[]
                if aa.observation: obs.append([Paragraph('Observation',st['label']),Paragraph(safe(aa.observation),st['small'])])
                if aa.remarks: obs.append([Paragraph('Remarks',st['label']),Paragraph(safe(aa.remarks),st['small'])])
                ot=Table(obs,colWidths=[2.5*cm,content_width-2.5*cm]); ot.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN_LIGHT),('BOX',(0,0),(-1,-1),0.4,BORDER),('LEFTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])); elements.append(ot)

            if responses:
                rows=[[Paragraph('#',st['header']),Paragraph('Inspection Point',st['header']),Paragraph('Result',st['header']),Paragraph('Remarks',st['header'])]]
                last=None
                for i,(resp,q,sec) in enumerate(responses,1):
                    if sec.name!=last:
                        rows.append(['',Paragraph(f'<b>{safe(sec.name)}</b>',ParagraphStyle(f'grp{idx}_{i}',fontSize=7,textColor=GREEN_DARK,fontName='Helvetica-Bold')),'','']); last=sec.name
                    rows.append([Paragraph(str(i),st['small']),Paragraph(safe(q.question_text),st['small']),Paragraph(resp_label(resp.response_status),ParagraphStyle(f's{idx}_{i}',fontSize=7.2,fontName='Helvetica-Bold',textColor=(GREEN_DARK if (resp.response_status or '').lower()=='pass' else RED if (resp.response_status or '').lower()=='fail' else GRAY),alignment=TA_CENTER)),Paragraph(safe(resp.remarks,''),st['small'])])
                qt=Table(rows,colWidths=[0.55*cm,8.2*cm,2.3*cm,content_width-11.05*cm],repeatRows=1)
                qt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY_2),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,HexColor('#FAFBFC')]),('BOX',(0,0),(-1,-1),0.45,BORDER),('INNERGRID',(0,0),(-1,-1),0.3,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,1),(0,-1),'CENTER'),('ALIGN',(2,1),(2,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
                # Highlight section rows and span their cells.
                r=1; last=None
                for i,(_,_,sec) in enumerate(responses,1):
                    if sec.name!=last:
                        qt.setStyle(TableStyle([('SPAN',(1,r),(3,r)),('BACKGROUND',(0,r),(-1,r),GREEN_LIGHT)])); r+=1; last=sec.name
                    r+=1
                elements.append(qt)
                elements.extend(_photo_evidence(session, responses, content_width, st))
            else:
                elements.append(Paragraph('No inspection responses recorded for this equipment.',st['small']))
            elements.append(Spacer(1,0.28*cm))

        elements.append(PageBreak())

        # ------------------------------------------------------------------
        # SAFETY AUDIT
        # ------------------------------------------------------------------
        elements += section_heading('04','Safety Audit','Site-level safety checkpoints (not tied to a single equipment item).',st)
        safety_rows=(session.query(AuditResponse,AuditQuestion,AuditSection)
                     .join(AuditQuestion,AuditResponse.question_id==AuditQuestion.id)
                     .join(AuditSection,AuditQuestion.section_id==AuditSection.id)
                     .filter(AuditResponse.audit_id==audit.id,AuditResponse.asset_id.is_(None),AuditSection.name==SAFETY_SECTION_NAME)
                     .order_by(AuditQuestion.question_order,AuditQuestion.id).all())
        if safety_rows:
            rows=[[Paragraph('#',st['header']),Paragraph('Safety Checkpoint',st['header']),Paragraph('Result',st['header']),Paragraph('Remarks',st['header'])]]
            for i,(resp,q,sec) in enumerate(safety_rows,1):
                stt=(resp.response_status or '').lower()
                rows.append([Paragraph(str(i),st['small']),Paragraph(safe(q.question_text),st['small']),
                             Paragraph(resp_label(stt),ParagraphStyle(f'sf{i}',fontSize=7.2,fontName='Helvetica-Bold',textColor=(GREEN_DARK if stt=='pass' else RED if stt=='fail' else GRAY),alignment=TA_CENTER)),
                             Paragraph(safe(resp.remarks,''),st['small'])])
            sft=Table(rows,colWidths=[0.55*cm,8.2*cm,2.3*cm,content_width-11.05*cm],repeatRows=1)
            sft.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY_2),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,HexColor('#FAFBFC')]),('BOX',(0,0),(-1,-1),0.45,BORDER),('INNERGRID',(0,0),(-1,-1),0.3,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,1),(0,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
            elements.append(sft)
            elements.extend(_photo_evidence(session, safety_rows, content_width, st))
            elements.append(PageBreak())
        else:
            elements += [Paragraph('No safety checkpoints were answered for this audit.',st['body']), Spacer(1,0.8*cm)]

        # ------------------------------------------------------------------
        # CIVIL / INFRASTRUCTURE AUDIT
        # ------------------------------------------------------------------
        elements += section_heading('05','Civil / Infrastructure Audit','Site-level civil and infrastructure checkpoints.',st)
        civil_rows=(session.query(AuditResponse,AuditQuestion,AuditSection)
                     .join(AuditQuestion,AuditResponse.question_id==AuditQuestion.id)
                     .join(AuditSection,AuditQuestion.section_id==AuditSection.id)
                     .filter(AuditResponse.audit_id==audit.id,AuditResponse.asset_id.is_(None),AuditSection.name==CIVIL_SECTION_NAME)
                     .order_by(AuditQuestion.question_order,AuditQuestion.id).all())
        if civil_rows:
            rows=[[Paragraph('#',st['header']),Paragraph('Civil / Infrastructure Checkpoint',st['header']),Paragraph('Result',st['header']),Paragraph('Remarks',st['header'])]]
            for i,(resp,q,sec) in enumerate(civil_rows,1):
                stt=(resp.response_status or '').lower()
                rows.append([Paragraph(str(i),st['small']),Paragraph(safe(q.question_text),st['small']),
                             Paragraph(resp_label(stt),ParagraphStyle(f'sf{i}',fontSize=7.2,fontName='Helvetica-Bold',textColor=(GREEN_DARK if stt=='pass' else RED if stt=='fail' else GRAY),alignment=TA_CENTER)),
                             Paragraph(safe(resp.remarks,''),st['small'])])
            cvt=Table(rows,colWidths=[0.55*cm,8.2*cm,2.3*cm,content_width-11.05*cm],repeatRows=1)
            cvt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY_2),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,HexColor('#FAFBFC')]),('BOX',(0,0),(-1,-1),0.45,BORDER),('INNERGRID',(0,0),(-1,-1),0.3,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,1),(0,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
            elements.append(cvt)
            elements.extend(_photo_evidence(session, civil_rows, content_width, st))
            elements.append(PageBreak())
        else:
            elements += [Paragraph('No Civil / Infrastructure checkpoints were answered for this audit.',st['body']), Spacer(1,0.8*cm)]

        # ------------------------------------------------------------------
        # OBSERVATION REGISTER
        # ------------------------------------------------------------------
        elements += section_heading('06','Observations & Closure Tracking','Every NC and OFI finding, with severity, owner, target date and current closure status.',st)
        reg=(session.query(Observation,AuditResponse,AuditQuestion,Asset)
             .join(AuditResponse,Observation.audit_response_id==AuditResponse.id)
             .join(AuditQuestion,AuditResponse.question_id==AuditQuestion.id)
             .outerjoin(Asset,AuditResponse.asset_id==Asset.id)
             .filter(Observation.audit_id==audit.id,Observation.status!='withdrawn').all())
        sev_order={'critical':0,'major':1,'minor':2}
        reg.sort(key=lambda r:(sev_order.get(r[0].severity,3),r[0].target_date or '9999'))
        if reg:
            rows=[[Paragraph('#',st['header']),Paragraph('Type',st['header']),Paragraph('Equipment / Checkpoint',st['header']),Paragraph('Observation &amp; Remarks',st['header']),Paragraph('Severity',st['header']),Paragraph('Responsible',st['header']),Paragraph('Target Date',st['header']),Paragraph('Status',st['header'])]]
            for i,(ob,resp,q,asset) in enumerate(reg,1):
                text=f'<b>{safe(ob.observation,"(no observation text)")}</b>'
                if ob.remarks: text+=f'<br/>Remarks: {safe(ob.remarks)}'
                if resp.remarks: text+=f'<br/>Checkpoint remarks: {safe(resp.remarks)}'
                if ob.action_taken: text+=f'<br/><i>Action taken: {safe(ob.action_taken)}</i>'
                if ob.closure_date: text+=f'<br/><b>Closure date:</b> {safe(ob.closure_date)}'
                if ob.closed_at: text+=f"<br/><b>Accepted / closed:</b> {ob.closed_at.strftime('%d %b %Y %H:%M')}"
                closure_photo_count = session.query(ObservationPhoto).filter_by(observation_id=ob.id, kind='closure').count()
                if closure_photo_count: text+=f'<br/><b>Closure evidence:</b> {closure_photo_count} photo(s)'
                if ob.reviewer_comments: text+=f'<br/><i>Reviewer: {safe(ob.reviewer_comments)}</i>'
                sc=sev_color(ob.severity)
                rows.append([Paragraph(str(i),st['small']),
                             Paragraph((ob.finding_type or 'NC').upper(),st['small']),
                             Paragraph(f'<b>{safe(asset.asset_name if asset else "Safety")}</b><br/>{safe(q.question_text)}',st['small']),
                             Paragraph(text,st['small']),
                             Paragraph(SEVERITY_LABELS.get(ob.severity,'Not set').upper(),ParagraphStyle(f'sv{i}',fontSize=7,fontName='Helvetica-Bold',textColor=sc,alignment=TA_CENTER)),
                             Paragraph(safe(ob.responsible_person),st['small']),
                             Paragraph(safe(ob.target_date),st['small']),
                             Paragraph(display_status(ob),st['small'])])
            ct=Table(rows,colWidths=[0.5*cm,1.0*cm,3.8*cm,4.2*cm,1.4*cm,2.1*cm,1.7*cm,content_width-14.7*cm],repeatRows=1)
            ct.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,HexColor('#FAFBFC')]),('BOX',(0,0),(-1,-1),0.5,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,1),(0,-1),'CENTER'),('ALIGN',(4,1),(4,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            elements += [ct, Spacer(1,3), Paragraph(f'Status as of {datetime.now().strftime("%d %b %Y, %H:%M")}. Photo evidence for each observation appears under its checkpoint in the equipment / safety sections above.',st['subtitle'])]
        else:
            none=Table([[Paragraph('<b>No observations</b><br/>No NC or OFI findings were recorded for this audit.',st['body'])]],colWidths=[content_width]); none.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN_LIGHT),('BOX',(0,0),(-1,-1),0.5,BORDER),('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)])); elements.append(none)
        elements.append(PageBreak())

        # ------------------------------------------------------------------
        # SIGN-OFF
        # ------------------------------------------------------------------
        elements += section_heading('06','Audit Sign-Off','Final acknowledgement of the audit and its findings.',st)
        sign=Table([
            [Paragraph('AUDITOR',st['label']),Paragraph('AUDITEE / PLANT REPRESENTATIVE',st['label'])],
            [Paragraph(safe(audit.auditor_name,'____________________________'),st['body']),Paragraph(safe(audit.auditee_name,'____________________________'),st['body'])],
            [Paragraph('<br/><br/>Signature: __________________________',st['small']),Paragraph('<br/><br/>Signature: __________________________',st['small'])],
            [Paragraph('Date: __________________________',st['small']),Paragraph('Date: __________________________',st['small'])]
        ],colWidths=[content_width/2.0]*2)
        sign.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),GRAY_LIGHT),('BOX',(0,0),(-1,-1),0.6,BORDER),('INNERGRID',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),9),('VALIGN',(0,0),(-1,-1),'TOP')]))
        elements += [sign,Spacer(1,1*cm)]
        conclusion=Table([[Paragraph('<b>Audit Conclusion</b><br/><br/>' + f'The audit recorded <b>{total_responses}</b> checkpoint responses: <b>{pass_count}</b> Complied and <b>{fail_count}</b> Not Complied. Compliance is <b>{overall:.1f}%</b>, the severity-weighted audit score is <b>{score_pct:.1f}%</b> and the site rating is <b>{safe(rating)}</b>. <b>{len(obs_all)}</b> observation(s) were raised, of which <b>{open_ca}</b> are still open or in follow-up.',st['body'])]],colWidths=[content_width])
        conclusion.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GREEN_LIGHT),('BOX',(0,0),(-1,-1),0.6,GREEN),('LEFTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)])); elements.append(conclusion)

        # ------------------------------------------------------------------
        # HEADER / FOOTER
        # ------------------------------------------------------------------
        def on_page(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.5)
            canvas.line(1.8*cm,H-1.05*cm,W-1.8*cm,H-1.05*cm)
            if os.path.exists(LOGO_PATH):
                try:
                    canvas.drawImage(LOGO_PATH,1.8*cm,H-0.88*cm,width=2.3*cm,height=0.62*cm,preserveAspectRatio=True,mask='auto')
                except Exception: pass
            canvas.setFillColor(GRAY); canvas.setFont('Helvetica',6.8)
            canvas.drawRightString(W-1.8*cm,H-0.72*cm,f'Audit Report  |  {audit.audit_number or audit_id_str[:8]}')
            canvas.setStrokeColor(BORDER); canvas.line(1.8*cm,1.0*cm,W-1.8*cm,1.0*cm)
            canvas.setFillColor(GRAY); canvas.setFont('Helvetica',6.5)
            canvas.drawString(1.8*cm,0.62*cm,'CleanMax Solar Audit Platform  |  Confidential')
            canvas.drawRightString(W-1.8*cm,0.62*cm,f'Page {doc.page}')
            canvas.restoreState()

        doc=SimpleDocTemplate(pdf_path,pagesize=A4,leftMargin=1.8*cm,rightMargin=1.8*cm,topMargin=1.35*cm,bottomMargin=1.35*cm,title=f'Solar Plant O&M Audit Report - {safe(plant.name,"Plant")}',author='CleanMax Solar Audit Platform')
        doc.build(elements,onFirstPage=on_page,onLaterPages=on_page)

        if not os.path.exists(pdf_path):
            return None,'Report file disappeared right after being generated. Check antivirus/security software and the uploads folder.'
        return pdf_path,None
    except Exception as e:
        print(f'PDF generation error: {e}')
        import traceback; traceback.print_exc()
        return None,str(e)
    finally:
        session.close()
