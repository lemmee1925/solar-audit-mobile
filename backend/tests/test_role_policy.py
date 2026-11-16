from role_policy import canonical_role, is_admin, is_hq_admin, is_site_admin


def test_legacy_roles_normalize_to_the_four_canonical_roles():
    assert canonical_role('admin') == 'site_admin'
    assert canonical_role('technician') == 'user'
    assert [canonical_role(role) for role in ('hq_admin', 'site_admin', 'engineer', 'user')] == [
        'hq_admin', 'site_admin', 'engineer', 'user'
    ]


def test_only_hq_and_site_admin_are_administrators():
    assert is_admin('hq_admin')
    assert is_admin('site_admin')
    assert not is_admin('engineer')
    assert not is_admin('user')
    assert is_hq_admin('hq_admin')
    assert is_site_admin('site_admin')
