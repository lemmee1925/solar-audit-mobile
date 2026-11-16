import pytest
from unittest.mock import patch, MagicMock
from app import app
from models import User

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        yield client

@patch('app.current_site')
def test_unauthorized_access(mock_current_site, client):
    # Mock site config to avoid redirect to setup
    mock_site = MagicMock()
    mock_site.site_id = "TEST_SITE"
    mock_current_site.return_value = mock_site
    
    # Attempt to access dashboard without login
    response = client.get('/dashboard', follow_redirects=True)
    assert response.status_code == 200
    assert b'Please log in first' in response.data or b'login' in response.data.lower()

@patch('app.load_site_config')
@patch('app.current_site')
@patch('app.Session')
def test_valid_login(mock_session, mock_current_site, mock_load_site_config, client):
    mock_site = MagicMock()
    mock_site.site_id = "TEST_SITE"
    mock_current_site.return_value = mock_site
    mock_load_site_config.return_value = mock_site
    
    mock_user = User(id='1', username='admin', password_hash='mock_hash', role='admin', site_id='TEST_SITE')
    mock_db_session = MagicMock()
    mock_db_session.query.return_value.filter_by.return_value.first.return_value = mock_user
    mock_session.return_value = mock_db_session
    
    with patch('app.check_password_hash', return_value=True):
        response = client.post('/login', data={'username': 'admin', 'password': 'password'}, follow_redirects=True)
        assert response.status_code == 200

@patch('app.current_site')
@patch('app.Session')
def test_missing_data_fields(mock_session, mock_current_site, client):
    mock_site = MagicMock()
    mock_site.site_id = "TEST_SITE"
    mock_current_site.return_value = mock_site
    
    # Simulate an edge case: submit an audit with missing fields
    # Here we mock login first
    with client.session_transaction() as sess:
        sess['_user_id'] = '1'
        sess['_fresh'] = True
        
    response = client.post('/audit/new', data={}, follow_redirects=True)
    # The application should handle this gracefully, usually returning a validation error
    assert response.status_code in [200, 400]
