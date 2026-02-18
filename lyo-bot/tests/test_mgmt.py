"""Tests for the management web application."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from fastapi.testclient import TestClient

from management.app import mgmt_app
from management.auth import create_token

client = TestClient(mgmt_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_cookie(email="admin@salon.it", business_id=1):
    """Create a valid JWT token cookie."""
    token = create_token(email, business_id)
    return {"access_token": token}


# ---------------------------------------------------------------------------
# Import test
# ---------------------------------------------------------------------------

class TestManagementAppImport:

    def test_mgmt_app_can_be_imported(self):
        """Verify the management app imports without errors."""
        assert mgmt_app is not None
        assert mgmt_app.title == "Lyo Management"


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

class TestLoginPage:

    def test_login_page_returns_200(self):
        response = client.get("/manage/login")
        assert response.status_code == 200
        assert "Lyo Management" in response.text

    def test_login_page_has_form(self):
        response = client.get("/manage/login")
        assert "email" in response.text
        assert "password" in response.text

    @patch("management.app.authenticate_user")
    def test_login_with_invalid_credentials(self, mock_auth):
        mock_auth.return_value = None
        response = client.post(
            "/manage/login",
            data={"email": "bad@example.com", "password": "wrong"},
        )
        assert response.status_code == 200
        assert "Credenziali non valide" in response.text

    @patch("management.app.authenticate_user")
    def test_login_with_valid_credentials_redirects(self, mock_auth):
        mock_auth.return_value = {
            "id": 1, "business_id": 42, "email": "admin@salon.it",
            "name": "Admin", "role": "admin",
        }
        response = client.post(
            "/manage/login",
            data={"email": "admin@salon.it", "password": "correct"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/manage/dashboard" in response.headers["location"]


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

class TestLogout:

    def test_logout_redirects_to_login(self):
        response = client.get("/manage/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/manage/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:

    def test_dashboard_without_token_redirects_to_login(self):
        response = client.get("/manage/dashboard", follow_redirects=False)
        assert response.status_code == 302 or response.status_code == 307
        assert "/manage/login" in response.headers.get("location", "")

    def test_dashboard_with_token_returns_200(self):
        response = client.get("/manage/dashboard", cookies=_auth_cookie())
        assert response.status_code == 200
        assert "Dashboard" in response.text

    def test_dashboard_has_navigation_links(self):
        response = client.get("/manage/dashboard", cookies=_auth_cookie())
        assert "/manage/treatments/" in response.text
        assert "/manage/operators/" in response.text
        assert "/manage/hours/" in response.text
        assert "/manage/settings/" in response.text
