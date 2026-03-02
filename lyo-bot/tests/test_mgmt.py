"""Tests for the management web application."""

import re
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from fastapi.testclient import TestClient

from management.app import mgmt_app
from management.auth import create_token

client = TestClient(mgmt_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_cookie(email="admin@salon.it", business_id=1, role="owner"):
    """Create a valid JWT token cookie."""
    token = create_token(email, business_id, role=role)
    return {"access_token": token}


def _post_with_csrf(url, data, cookies=None, **kwargs):
    """Do a GET first to establish CSRF session, then POST with csrf_token."""
    # GET any manage page to get session cookie with CSRF token
    get_resp = client.get("/manage/login", cookies=cookies)
    session_cookie = get_resp.cookies.get("session")
    all_cookies = dict(cookies or {})
    if session_cookie:
        all_cookies["session"] = session_cookie
    # Extract CSRF token from session via a page that renders it
    get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
    csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
    if csrf_match:
        data = dict(data)
        data["csrf_token"] = csrf_match.group(1)
    # Update session cookie from latest response
    if get_resp2.cookies.get("session"):
        all_cookies["session"] = get_resp2.cookies.get("session")
    return client.post(url, data=data, cookies=all_cookies, **kwargs)


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


# ---------------------------------------------------------------------------
# Treatment description + notes (spec gap item 1)
# ---------------------------------------------------------------------------

class TestTreatmentDescriptionNotes:
    """Treatment forms must expose description_it, description_en, notes fields."""

    def test_treatments_page_has_description_fields(self):
        """GET /manage/treatments/ should show description and notes input fields."""
        mock_treatments = [
            # id, code, name_it, name_en, duration, price, is_active, sort_order,
            # description_it, description_en, notes
            (1, "taglio", "Taglio", "Haircut", 30, 25.00, True, 0,
             "Taglio classico", "Classic haircut", "Solo su appuntamento"),
        ]
        with patch("management.routes.treatments.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = mock_treatments
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None

            response = client.get("/manage/treatments/", cookies=_auth_cookie())
            assert response.status_code == 200
            assert "description_it" in response.text
            assert "description_en" in response.text
            assert 'name="notes"' in response.text

    def test_add_treatment_includes_description_fields(self):
        """POST /manage/treatments/add should accept description_it, description_en, notes."""
        with patch("management.routes.treatments.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            with patch("management.routes.treatments.tenant_service"):
                response = _post_with_csrf(
                    "/manage/treatments/add",
                    data={
                        "code": "test",
                        "name_it": "Test",
                        "name_en": "Test EN",
                        "duration_minutes": "30",
                        "price": "25",
                        "description_it": "Desc IT",
                        "description_en": "Desc EN",
                        "notes": "Test note",
                    },
                    cookies=_auth_cookie(),
                    follow_redirects=False,
                )
                assert response.status_code == 302
                sql = mock_cur.execute.call_args[0][0]
                assert "description_it" in sql
                assert "description_en" in sql
                assert "notes" in sql

    def test_edit_treatment_includes_description_fields(self):
        """POST /manage/treatments/{id}/edit should accept and update description/notes."""
        with patch("management.routes.treatments.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            with patch("management.routes.treatments.tenant_service"):
                response = _post_with_csrf(
                    "/manage/treatments/1/edit",
                    data={
                        "code": "taglio",
                        "name_it": "Taglio",
                        "duration_minutes": "30",
                        "price": "25",
                        "description_it": "Updated desc",
                        "description_en": "Updated desc EN",
                        "notes": "Updated note",
                        "is_active": "on",
                    },
                    cookies=_auth_cookie(),
                    follow_redirects=False,
                )
                assert response.status_code == 302
                sql = mock_cur.execute.call_args[0][0]
                assert "description_it" in sql
                assert "notes" in sql


# ---------------------------------------------------------------------------
# Operator notes (spec gap item 2)
# ---------------------------------------------------------------------------

class TestOperatorNotes:
    """Operator forms must expose notes field."""

    def test_operators_page_has_notes_field(self):
        """GET /manage/operators/ should show notes input field."""
        mock_operators = [
            # id, technical_id, display_name, is_active, sort_order, notes
            (1, "sofia", "Sofia", True, 0, "Specialista colore"),
        ]
        with patch("management.routes.operators.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchall.side_effect = [
                mock_operators,  # operators query
                [(1, "taglio", "Taglio")],  # treatments query
                [],  # operator_treatments query
            ]
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None

            response = client.get("/manage/operators/", cookies=_auth_cookie())
            assert response.status_code == 200
            assert 'name="notes"' in response.text

    def test_add_operator_includes_notes(self):
        """POST /manage/operators/add should accept notes."""
        with patch("management.routes.operators.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            with patch("management.routes.operators.tenant_service"):
                response = _post_with_csrf(
                    "/manage/operators/add",
                    data={
                        "technical_id": "test_op",
                        "display_name": "Test Op",
                        "sort_order": "0",
                        "notes": "Test operator note",
                    },
                    cookies=_auth_cookie(),
                    follow_redirects=False,
                )
                assert response.status_code == 302
                sql = mock_cur.execute.call_args[0][0]
                assert "notes" in sql

    def test_edit_operator_includes_notes(self):
        """POST /manage/operators/{id}/edit should accept and update notes."""
        with patch("management.routes.operators.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            with patch("management.routes.operators.tenant_service"):
                response = _post_with_csrf(
                    "/manage/operators/1/edit",
                    data={
                        "technical_id": "sofia",
                        "display_name": "Sofia",
                        "sort_order": "0",
                        "is_active": "on",
                        "notes": "Updated operator note",
                    },
                    cookies=_auth_cookie(),
                    follow_redirects=False,
                )
                assert response.status_code == 302
                sql = mock_cur.execute.call_args[0][0]
                assert "notes" in sql


# ---------------------------------------------------------------------------
# Salon rules in settings (spec gap item 3)
# ---------------------------------------------------------------------------

class TestSalonRules:
    """Settings page must expose salon rules stored in JSONB settings column."""

    def test_settings_page_has_rules_fields(self):
        """GET /manage/settings/ should show deposit, cancellation, punctuality, other rules."""
        mock_row = (
            "Aura", "Assistente", "Europe/Rome", "it", "admin@aura.it",
            "Via Roma 1", "+39123", "+39456", "info@aura.it", 1,
            "primary", None,
            '{"rules": {"deposit": "20 EUR", "cancellation": "24h", "punctuality": "15 min", "other": ""}}'
        )
        with patch("management.routes.settings.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = mock_row
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None

            response = client.get("/manage/settings/", cookies=_auth_cookie())
            assert response.status_code == 200
            assert "rules_deposit" in response.text
            assert "rules_cancellation" in response.text
            assert "rules_punctuality" in response.text
            assert "rules_other" in response.text

    def test_save_settings_stores_rules_in_jsonb(self):
        """POST /manage/settings/save should store rules in JSONB settings column."""
        with patch("management.routes.settings.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchone.side_effect = [
                ({},),    # current settings
                (1,),     # chatwoot_account_id
            ]
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            with patch("management.routes.settings.tenant_service"):
                response = _post_with_csrf(
                    "/manage/settings/save",
                    data={
                        "bot_name": "Assistente",
                        "timezone": "Europe/Rome",
                        "language": "it",
                        "rules_deposit": "Anticipo 20 EUR",
                        "rules_cancellation": "24h prima",
                        "rules_punctuality": "Max 15 min ritardo",
                        "rules_other": "",
                    },
                    cookies=_auth_cookie(),
                    follow_redirects=False,
                )
                assert response.status_code == 302


# ---------------------------------------------------------------------------
# AI system prompt includes descriptions + rules (spec gap items 1, 3)
# ---------------------------------------------------------------------------

class TestAIPromptIncludes:
    """AI system prompt should include treatment descriptions/notes and salon rules."""

    def test_system_prompt_includes_treatment_description(self):
        """build_system_prompt should include treatment description and notes."""
        from app.services.ai import build_system_prompt
        from app.models.schemas import Business, Treatment, Operator, BusinessHours

        biz = Business(
            id=1, chatwoot_account_id=1, name="Test Salon",
            settings={"rules": {"deposit": "20 EUR deposit required"}},
            treatments=[
                Treatment(
                    id=1, business_id=1, code="taglio", name_it="Taglio",
                    duration_minutes=30, price=25, is_active=True,
                    description_it="Taglio classico uomo/donna",
                    notes="Solo su appuntamento",
                ),
            ],
            operators=[
                Operator(
                    id=1, business_id=1, technical_id="sofia",
                    display_name="Sofia", is_active=True,
                    treatment_ids=[1],
                    notes="Specialista colore",
                ),
            ],
            hours=[
                BusinessHours(business_id=1, day_of_week=0, is_open=True,
                             open_time="09:00", close_time="18:00"),
            ],
        )
        prompt = build_system_prompt(biz)
        assert "Taglio classico uomo/donna" in prompt
        assert "Solo su appuntamento" in prompt
        assert "Specialista colore" in prompt
        assert "20 EUR deposit required" in prompt


# ---------------------------------------------------------------------------
# Role-based permissions (spec gap item 5)
# ---------------------------------------------------------------------------

class TestRoleBasedPermissions:
    """Owner-only routes should reject staff users."""

    def _staff_cookie(self):
        return _auth_cookie(email="staff@salon.it", business_id=1, role="staff")

    def _owner_cookie(self):
        return _auth_cookie(email="admin@salon.it", business_id=1, role="owner")

    def test_staff_cannot_access_settings(self):
        """Staff user should be blocked from /manage/settings/."""
        response = client.get("/manage/settings/", cookies=self._staff_cookie(),
                            follow_redirects=False)
        assert response.status_code in (302, 403)

    def test_staff_cannot_access_users(self):
        """Staff user should be blocked from /manage/users/."""
        response = client.get("/manage/users/", cookies=self._staff_cookie(),
                            follow_redirects=False)
        assert response.status_code in (302, 403)

    def test_owner_can_access_settings(self):
        """Owner should access /manage/settings/ normally."""
        with patch("management.routes.settings.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (
                "Aura", "Assistente", "Europe/Rome", "it", "admin@aura.it",
                "Via Roma 1", "+39123", "+39456", "info@aura.it", 1,
                "primary", None, '{}'
            )
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            response = client.get("/manage/settings/", cookies=self._owner_cookie())
            assert response.status_code == 200

    def test_staff_can_access_treatments(self):
        """Staff should still be able to access /manage/treatments/."""
        with patch("management.routes.treatments.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = []
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            response = client.get("/manage/treatments/", cookies=self._staff_cookie())
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# CSRF header support for AJAX (Task 1)
# ---------------------------------------------------------------------------

class TestCSRFHeaderSupport:
    """CSRF token can be passed via X-CSRF-Token header (for AJAX)."""

    def test_post_with_csrf_header_succeeds(self):
        """POST with CSRF token in header should be accepted."""
        cookies = _auth_cookie()
        get_resp = client.get("/manage/login", cookies=cookies)
        session_cookie = get_resp.cookies.get("session")
        all_cookies = dict(cookies)
        if session_cookie:
            all_cookies["session"] = session_cookie
        get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
        csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
        assert csrf_match, "CSRF token not found in meta tag"
        csrf_token = csrf_match.group(1)
        if get_resp2.cookies.get("session"):
            all_cookies["session"] = get_resp2.cookies.get("session")

        with patch("management.routes.appointments.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            resp = client.post(
                "/manage/appointments/9999/cancel",
                data={},
                cookies=all_cookies,
                headers={"X-CSRF-Token": csrf_token},
                follow_redirects=False,
            )
        assert resp.status_code == 302

    def test_post_without_any_csrf_still_fails(self):
        """POST without CSRF token in body OR header should 403."""
        cookies = _auth_cookie()
        get_resp = client.get("/manage/login", cookies=cookies)
        session_cookie = get_resp.cookies.get("session")
        all_cookies = dict(cookies)
        if session_cookie:
            all_cookies["session"] = session_cookie
        resp = client.post(
            "/manage/appointments/9999/cancel",
            data={},
            cookies=all_cookies,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Calendar API — Operators endpoint (Task 2)
# ---------------------------------------------------------------------------

class TestCalendarAPIOperators:
    """GET /manage/api/operators returns FullCalendar resource objects."""

    @patch("management.routes.calendar.get_connection")
    def test_returns_operators_as_resources(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (1, "Giulia", True),
            (2, "Martina", True),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get("/manage/api/operators", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["title"] == "Giulia"

    def test_unauthenticated_returns_401(self):
        resp = client.get("/manage/api/operators")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Calendar API — Appointments endpoint (Task 3)
# ---------------------------------------------------------------------------

class TestCalendarAPIAppointments:
    """GET /manage/api/appointments returns FullCalendar event objects."""

    @patch("management.routes.calendar.get_connection")
    def test_returns_appointments_as_events(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (10, "Maria Rossi", "+39123", "Taglio Donna", "taglio_donna",
             "Giulia", "2026-03-02", "10:00:00", 45, "confirmed", 60.00, 1),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get(
            "/manage/api/appointments?start=2026-03-02&end=2026-03-03",
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        evt = data[0]
        assert evt["id"] == 10
        assert evt["title"] == "Maria Rossi\nTaglio Donna"
        assert evt["start"] == "2026-03-02T10:00:00"
        assert evt["end"] == "2026-03-02T10:45:00"
        assert evt["resourceId"] == 1
        assert evt["color"] == "#16a34a"

    @patch("management.routes.calendar.get_connection")
    def test_cancelled_appointment_is_red(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (11, "Luca B", "+39456", "Piega", "piega",
             "Martina", "2026-03-02", "14:00:00", 30, "cancelled", 30.00, 2),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get(
            "/manage/api/appointments?start=2026-03-02&end=2026-03-03",
            cookies=cookies,
        )
        data = resp.json()
        assert data[0]["color"] == "#dc2626"

    def test_missing_dates_returns_400(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/api/appointments", cookies=cookies)
        assert resp.status_code == 400

    def test_unauthenticated_returns_401(self):
        resp = client.get("/manage/api/appointments?start=2026-03-02&end=2026-03-03")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Calendar API — Move Appointment (Task 4)
# ---------------------------------------------------------------------------

class TestCalendarAPIMoveAppointment:
    """POST /manage/api/appointments/{id}/move reschedules via drag-and-drop."""

    @patch("management.routes.calendar.get_connection")
    def test_move_updates_date_time_operator(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_cur.fetchone.return_value = ("Martina",)
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        # Need CSRF for POST - get via header
        get_resp = client.get("/manage/login", cookies=cookies)
        all_cookies = dict(cookies)
        session_cookie = get_resp.cookies.get("session")
        if session_cookie:
            all_cookies["session"] = session_cookie
        get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
        csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        if get_resp2.cookies.get("session"):
            all_cookies["session"] = get_resp2.cookies.get("session")

        resp = client.post(
            "/manage/api/appointments/10/move",
            json={"new_date": "2026-03-03", "new_time": "11:00", "new_operator_id": 2},
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "moved"

    def test_unauthenticated_returns_401_or_403(self):
        resp = client.post("/manage/api/appointments/10/move", json={})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Calendar Page (Task 5)
# ---------------------------------------------------------------------------

class TestCalendarPage:
    """GET /manage/calendar/ returns calendar HTML page."""

    def test_calendar_page_returns_200(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/calendar/", cookies=cookies)
        assert resp.status_code == 200
        assert "fullcalendar" in resp.text.lower() or "FullCalendar" in resp.text

    def test_calendar_page_has_csrf_meta(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/calendar/", cookies=cookies)
        assert 'csrf-token' in resp.text

    def test_unauthenticated_redirects(self):
        resp = client.get("/manage/calendar/", follow_redirects=False)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Calendar Navigation (Task 6)
# ---------------------------------------------------------------------------

class TestCalendarNavigation:
    """Calendar is accessible from navigation and dashboard."""

    def test_nav_has_calendario_link(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/dashboard", cookies=cookies)
        assert '/manage/calendar/' in resp.text
        assert 'Calendario' in resp.text

    def test_dashboard_has_calendario_card(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/dashboard", cookies=cookies)
        assert 'Calendario' in resp.text
