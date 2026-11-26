import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from site_scope import compute_score


def test_ofi_category_and_severity_ordering():
    # Higher business impact or higher severity yields a lower score.
    base = [('pass', None, None, None)] * 9
    aes_minor = compute_score(base + [('ofi', 'minor', None, 'Aesthetic')])
    prof_minor = compute_score(base + [('ofi', 'minor', None, 'Profitability')])
    rel_minor = compute_score(base + [('ofi', 'minor', None, 'Reliability')])
    saf_minor = compute_score(base + [('ofi', 'minor', None, 'Safety')])

    assert aes_minor['score_pct'] > prof_minor['score_pct'] > rel_minor['score_pct'] > saf_minor['score_pct']

    saf_major = compute_score(base + [('ofi', 'major', None, 'Safety')])
    saf_crit = compute_score(base + [('ofi', 'critical', None, 'Safety')])
    assert saf_minor['score_pct'] > saf_major['score_pct'] > saf_crit['score_pct']


def test_na_is_excluded_and_critical_safety_caps_rating():
    rows = [('pass', None, None, None)] * 19 + [
        ('ofi', 'critical', None, 'Safety'),
        ('na', None, None, None),
        ('na', None, None, None),
    ]
    res = compute_score(rows)
    assert res['applicable'] == 20
    assert res['not_applicable'] == 2
    assert res['score_pct'] == 95.0
    assert res['compliance_pct'] == 95.0
    assert res['critical_safety_ofi'] is True
    assert res['rating'] == 'Needs Improvement'
