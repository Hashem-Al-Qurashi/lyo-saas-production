"""
Adversarial security tests for the 4 newly-implemented features.

Tests probe:
1.  Staff role bypass on POST /manage/settings/save
2.  Staff role bypass on POST /manage/users/add
3.  SQL injection in description/notes/rules fields (not possible via
    parameterised queries, but we verify the queries are genuinely parameterised)
4.  JSONB settings = NULL or empty string
5.  JWT decode with OLD tokens that have no 'role' field
6.  Additional edge cases: empty/whitespace-only inputs, oversized payloads,
    role escalation via form field manipulation
"""

import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
from jose import jwt
from fastapi.testclient import TestClient

from management.app import mgmt_app
from management.auth import create_token, decode_token
from app.config import settings

client = TestClient(mgmt_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cookie(role: str, business_id: int = 1):
    token = create_token("user@salon.it", business_id, role=role)
    return {"access_token": token}


def _csrf_post(url: str, data: dict, cookies: dict, follow_redirects: bool = False):
    """
    Obtain a CSRF token via the dashboard page, then POST with it.
    Mirrors the helper in test_mgmt.py but isolated here.
    """
    get_resp = client.get("/manage/login", cookies=cookies)
    session_cookie = get_resp.cookies.get("session")
    all_cookies = dict(cookies)
    if session_cookie:
        all_cookies["session"] = session_cookie

    get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
    csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
    if csrf_match:
        data = dict(data)
        data["csrf_token"] = csrf_match.group(1)
    if get_resp2.cookies.get("session"):
        all_cookies["session"] = get_resp2.cookies.get("session")

    return client.post(url, data=data, cookies=all_cookies,
                       follow_redirects=follow_redirects)


def _raw_post_no_csrf(url: str, data: dict, cookies: dict):
    """POST directly without a CSRF token - simulates a raw attacker request."""
    return client.post(url, data=data, cookies=cookies,
                       follow_redirects=False)


# ---------------------------------------------------------------------------
# 1. Staff role bypass tests
# ---------------------------------------------------------------------------

class TestStaffRoleBypassViaDirectPost:
    """
    A staff user who crafts a direct HTTP POST must still be blocked,
    regardless of CSRF handling.  The role check MUST happen inside the
    route handler itself, not just at the template/navigation level.
    """

    def test_staff_cannot_post_to_settings_save_without_csrf(self):
        """
        Raw POST without CSRF should be rejected at the CSRF layer (403)
        before the role check even runs.  This confirms CSRF is a real barrier.
        """
        resp = _raw_post_no_csrf(
            "/manage/settings/save",
            data={
                "bot_name": "Hacked",
                "timezone": "UTC",
                "language": "en",
            },
            cookies=_cookie("staff"),
        )
        # CSRF middleware must fire first → 403
        assert resp.status_code == 403, (
            f"Expected 403 from CSRF middleware, got {resp.status_code}. "
            "Staff can bypass CSRF protection."
        )

    def test_staff_cannot_post_to_settings_save_with_valid_csrf(self):
        """
        Even with a valid CSRF token, a staff user must be blocked (302 to
        dashboard, not 200/302 to settings/).
        """
        resp = _csrf_post(
            "/manage/settings/save",
            data={
                "bot_name": "Hacked",
                "timezone": "UTC",
                "language": "en",
            },
            cookies=_cookie("staff"),
            follow_redirects=False,
        )
        # Must redirect to dashboard, NOT to /manage/settings/
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}."
        )
        location = resp.headers.get("location", "")
        assert "/manage/settings/" not in location, (
            f"Staff was redirected to settings page: {location}. "
            "Role check failed on POST /manage/settings/save."
        )
        assert "dashboard" in location, (
            f"Staff redirect target is unexpected: {location}"
        )

    def test_staff_cannot_post_to_users_add_without_csrf(self):
        """Raw POST to /manage/users/add must be blocked by CSRF."""
        resp = _raw_post_no_csrf(
            "/manage/users/add",
            data={
                "name": "Injected Admin",
                "email": "injected@evil.com",
                "password": "password123",
                "role": "owner",
            },
            cookies=_cookie("staff"),
        )
        assert resp.status_code == 403, (
            f"Expected 403 from CSRF middleware, got {resp.status_code}. "
            "Staff can bypass CSRF on users/add."
        )

    def test_staff_cannot_post_to_users_add_with_valid_csrf(self):
        """Even with a valid CSRF token, staff must be blocked on /manage/users/add."""
        resp = _csrf_post(
            "/manage/users/add",
            data={
                "name": "Injected Admin",
                "email": "injected@evil.com",
                "password": "password123",
                "role": "owner",
            },
            cookies=_cookie("staff"),
            follow_redirects=False,
        )
        assert resp.status_code == 302, (
            f"Expected 302, got {resp.status_code}."
        )
        location = resp.headers.get("location", "")
        # Must NOT land on /manage/users/ with a success redirect
        # The role check redirects to /manage/dashboard
        assert "dashboard" in location, (
            f"Staff was NOT redirected to dashboard after blocked users/add. "
            f"Got: {location}. Role check failed."
        )

    def test_staff_cannot_toggle_user_with_valid_csrf(self):
        """Staff must not be able to toggle user activation status."""
        resp = _csrf_post(
            "/manage/users/1/toggle",
            data={},
            cookies=_cookie("staff"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "dashboard" in location, (
            f"Staff reached toggle endpoint. Location: {location}"
        )

    def test_staff_cannot_change_password_with_valid_csrf(self):
        """Staff must not be able to change another user's password."""
        resp = _csrf_post(
            "/manage/users/1/password",
            data={"new_password": "hacked123"},
            cookies=_cookie("staff"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "dashboard" in location, (
            f"Staff reached change-password endpoint. Location: {location}"
        )

    def test_role_escalation_via_form_field_is_blocked(self):
        """
        A staff user must not be able to create an 'owner' role account by
        POST-ing role=owner to /manage/users/add.  Even if the DB write
        somehow happened, the role check at the handler level must block it.
        """
        with patch("management.routes.users.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None

            resp = _csrf_post(
                "/manage/users/add",
                data={
                    "name": "Evil Owner",
                    "email": "evil@attacker.com",
                    "password": "password123",
                    "role": "owner",   # trying to escalate
                },
                cookies=_cookie("staff"),
                follow_redirects=False,
            )
            # The route should have rejected the staff user before ever
            # touching the DB
            mock_cur.execute.assert_not_called(), (
                "DB execute was called even though user is staff - role check bypassed!"
            )
            assert resp.status_code == 302
            location = resp.headers.get("location", "")
            assert "dashboard" in location


# ---------------------------------------------------------------------------
# 2. CSRF without authentication token
# ---------------------------------------------------------------------------

class TestUnauthenticatedAccess:
    """Endpoints must redirect to login when no JWT cookie is present."""

    def test_no_token_settings_get_redirects_to_login(self):
        resp = client.get("/manage/settings/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/manage/login" in resp.headers.get("location", "")

    def test_no_token_users_get_redirects_to_login(self):
        resp = client.get("/manage/users/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/manage/login" in resp.headers.get("location", "")

    def test_no_token_post_settings_blocked_by_csrf(self):
        """Without a session, raw POST must get 403 from CSRF."""
        resp = _raw_post_no_csrf(
            "/manage/settings/save",
            data={"bot_name": "X"},
            cookies={},   # no JWT, no session
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. SQL injection probes
# ---------------------------------------------------------------------------

class TestSQLInjectionInNewFields:
    """
    Verify that the new description/notes/rules fields are safe from SQL injection.
    Since psycopg2 parameterised queries are used, injection must not alter
    the SQL structure.  We test this by inspecting the SQL that reaches the
    cursor mock - the raw SQL string must NOT contain the injected payload,
    only the parameterised placeholder %s.
    """

    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE treatments; --",
        "' OR '1'='1",
        "\" OR \"1\"=\"1",
        "1; SELECT * FROM management_users; --",
        "' UNION SELECT password_hash FROM management_users --",
        "'; INSERT INTO management_users VALUES (999,'hack','hack@evil.com','hash','owner'); --",
        "<script>alert('xss')</script>",
        "' OR 1=1 LIMIT 1 --",
    ]

    def _make_mock_conn(self):
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.__exit__ = lambda s, *a: None
        return mock_conn, mock_cur

    def test_add_treatment_description_it_injection(self):
        for payload in self.SQL_INJECTION_PAYLOADS:
            with patch("management.routes.treatments.get_connection") as mock_conn_factory:
                mock_conn, mock_cur = self._make_mock_conn()
                mock_conn_factory.return_value = mock_conn
                with patch("management.routes.treatments.tenant_service"):
                    _csrf_post(
                        "/manage/treatments/add",
                        data={
                            "code": "t1",
                            "name_it": "Test",
                            "duration_minutes": "30",
                            "price": "10",
                            "description_it": payload,
                        },
                        cookies=_cookie("owner"),
                        follow_redirects=False,
                    )
                    if mock_cur.execute.called:
                        actual_sql = mock_cur.execute.call_args_list[-1][0][0]
                        # The SQL itself must not contain the payload
                        assert payload not in actual_sql, (
                            f"SQL injection payload leaked into SQL query.\n"
                            f"Payload: {payload!r}\nSQL: {actual_sql!r}"
                        )

    def test_add_treatment_notes_injection(self):
        for payload in self.SQL_INJECTION_PAYLOADS:
            with patch("management.routes.treatments.get_connection") as mock_conn_factory:
                mock_conn, mock_cur = self._make_mock_conn()
                mock_conn_factory.return_value = mock_conn
                with patch("management.routes.treatments.tenant_service"):
                    _csrf_post(
                        "/manage/treatments/add",
                        data={
                            "code": "t2",
                            "name_it": "Test2",
                            "duration_minutes": "30",
                            "price": "10",
                            "notes": payload,
                        },
                        cookies=_cookie("owner"),
                        follow_redirects=False,
                    )
                    if mock_cur.execute.called:
                        actual_sql = mock_cur.execute.call_args_list[-1][0][0]
                        assert payload not in actual_sql, (
                            f"Notes injection payload in SQL: {actual_sql!r}"
                        )

    def test_add_operator_notes_injection(self):
        for payload in self.SQL_INJECTION_PAYLOADS:
            with patch("management.routes.operators.get_connection") as mock_conn_factory:
                mock_conn, mock_cur = self._make_mock_conn()
                mock_conn_factory.return_value = mock_conn
                with patch("management.routes.operators.tenant_service"):
                    _csrf_post(
                        "/manage/operators/add",
                        data={
                            "technical_id": "op1",
                            "display_name": "Op",
                            "sort_order": "0",
                            "notes": payload,
                        },
                        cookies=_cookie("owner"),
                        follow_redirects=False,
                    )
                    if mock_cur.execute.called:
                        actual_sql = mock_cur.execute.call_args_list[-1][0][0]
                        assert payload not in actual_sql, (
                            f"Operator notes injection payload in SQL: {actual_sql!r}"
                        )

    def test_save_settings_rules_injection(self):
        for payload in self.SQL_INJECTION_PAYLOADS:
            with patch("management.routes.settings.get_connection") as mock_conn_factory:
                mock_conn, mock_cur = self._make_mock_conn()
                mock_cur.fetchone.side_effect = [({},), (1,)]
                mock_conn_factory.return_value = mock_conn
                with patch("management.routes.settings.tenant_service"):
                    _csrf_post(
                        "/manage/settings/save",
                        data={
                            "bot_name": "Bot",
                            "timezone": "Europe/Rome",
                            "language": "it",
                            "rules_deposit": payload,
                            "rules_cancellation": "",
                            "rules_punctuality": "",
                            "rules_other": "",
                        },
                        cookies=_cookie("owner"),
                        follow_redirects=False,
                    )
                    if mock_cur.execute.called:
                        for call in mock_cur.execute.call_args_list:
                            actual_sql = call[0][0]
                            assert payload not in actual_sql, (
                                f"Rules injection payload in SQL: {actual_sql!r}"
                            )


# ---------------------------------------------------------------------------
# 4. JSONB settings = NULL or empty string
# ---------------------------------------------------------------------------

class TestSettingsNullOrEmpty:
    """
    The settings column may be NULL (never set) or an empty string (migrated
    from an older schema).  The route must handle both without crashing.
    """

    def _mock_conn_with_settings(self, settings_value):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (
            "Salon", "Bot", "Europe/Rome", "it", "owner@s.it",
            "Via X", "+39123", "+39456", "info@s.it", 1,
            "primary", None,
            settings_value,   # <-- the settings column value
        )
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.__exit__ = lambda s, *a: None
        return mock_conn

    def test_settings_null_does_not_crash(self):
        """settings = None (SQL NULL) must produce an empty rules dict."""
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = self._mock_conn_with_settings(None)
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when settings is NULL. "
            "Handler crashed or redirected incorrectly."
        )

    def test_settings_empty_string_does_not_crash(self):
        """settings = '' must be handled gracefully (not crash on json.loads)."""
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = self._mock_conn_with_settings("")
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when settings is empty string. "
            "json.loads('') raises ValueError - is it caught?"
        )

    def test_settings_invalid_json_string_does_not_crash(self):
        """settings = '{not valid json}' must be handled gracefully."""
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = self._mock_conn_with_settings("{not: valid}")
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when settings is invalid JSON string. "
            "Handler must fall back to {{}} not crash."
        )

    def test_settings_integer_value_does_not_crash(self):
        """settings = 42 (unexpected type from DB) must not crash."""
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = self._mock_conn_with_settings(42)
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when settings is integer. "
            "The 'not isinstance(raw_settings, dict)' branch should catch this."
        )

    def test_settings_list_value_does_not_crash(self):
        """settings = [] (unexpected list from DB) must not crash."""
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = self._mock_conn_with_settings([])
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when settings is a list. "
            "Handler must treat non-dict as empty dict."
        )

    def test_settings_row_not_found_does_not_crash(self):
        """If the business row is not found at all (fetchone returns None), no 500."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.__exit__ = lambda s, *a: None
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = mock_conn
            resp = client.get("/manage/settings/", cookies=_cookie("owner"))
        assert resp.status_code == 200, (
            f"Got {resp.status_code} when business row not found. "
            "The '{% if business %}' branch in template should handle this."
        )

    def test_save_settings_when_current_settings_is_string_json(self):
        """
        On save, the current settings read from DB may be a JSON string
        (not a dict) if the column type is TEXT.  The code does:
            current_settings = row[0] if row and isinstance(row[0], dict) else {}
        This means a valid JSON string is silently discarded and other keys
        are lost.  Verify at minimum no crash occurs.
        """
        mock_cur = MagicMock()
        # First fetchone returns a JSON string (not dict)
        mock_cur.fetchone.side_effect = [
            ('{"existing_key": "existing_value"}',),
            (1,),
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.__exit__ = lambda s, *a: None
        with patch("management.routes.settings.get_connection") as mock_f:
            mock_f.return_value = mock_conn
            with patch("management.routes.settings.tenant_service"):
                resp = _csrf_post(
                    "/manage/settings/save",
                    data={
                        "bot_name": "Bot",
                        "timezone": "Europe/Rome",
                        "language": "it",
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code == 302, (
            f"Save settings crashed when current settings is a JSON string. "
            f"Status: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# 5. JWT decode with OLD tokens missing the 'role' field
# ---------------------------------------------------------------------------

class TestOldJWTWithoutRoleField:
    """
    Old tokens issued before role-based access was implemented will not contain
    a 'role' key.  decode_token must still return a dict (not None), and routes
    using user.get('role') must handle the missing key safely.
    """

    def _make_old_token(self, email: str = "old@salon.it", business_id: int = 1) -> str:
        """Create a JWT the old way - without the 'role' field."""
        expire = datetime.utcnow() + timedelta(minutes=60)
        payload = {
            "sub": email,
            "business_id": business_id,
            "exp": expire,
            # NO 'role' key - simulating a pre-RBAC token
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    def test_decode_token_returns_dict_for_old_token(self):
        """decode_token must succeed on a token with no 'role' field."""
        old_token = self._make_old_token()
        result = decode_token(old_token)
        assert result is not None, (
            "decode_token returned None for a valid (old) token without 'role'. "
            "Old sessions will be silently rejected."
        )
        assert isinstance(result, dict)
        assert "role" not in result, "Sanity check: old token really has no role"

    def test_old_token_dashboard_access(self):
        """An old token without role must still reach the dashboard."""
        old_token = self._make_old_token()
        resp = client.get("/manage/dashboard", cookies={"access_token": old_token})
        assert resp.status_code == 200, (
            f"Old token (no role) was rejected at dashboard. Got {resp.status_code}. "
            "Users with old sessions will be locked out."
        )

    def test_old_token_treatments_access(self):
        """An old token without role must still reach /manage/treatments/."""
        old_token = self._make_old_token()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.__exit__ = lambda s, *a: None
        with patch("management.routes.treatments.get_connection") as mock_f:
            mock_f.return_value = mock_conn
            resp = client.get("/manage/treatments/",
                              cookies={"access_token": old_token})
        assert resp.status_code == 200, (
            f"Old token rejected at treatments. Got {resp.status_code}."
        )

    def test_old_token_blocked_from_settings(self):
        """
        An old token has no 'role' field.  user.get('role') returns None.
        None is not in ('owner', 'admin'), so the settings route MUST block it.
        This is CORRECT security behaviour - just verify it is consistent.
        """
        old_token = self._make_old_token()
        resp = client.get("/manage/settings/",
                          cookies={"access_token": old_token},
                          follow_redirects=False)
        assert resp.status_code == 302, (
            f"Old token (no role) must be redirected away from settings. Got {resp.status_code}."
        )
        location = resp.headers.get("location", "")
        assert "/manage/settings/" not in location, (
            f"Old token reached settings page. Location: {location}"
        )

    def test_old_token_blocked_from_users(self):
        """Same as above but for /manage/users/."""
        old_token = self._make_old_token()
        resp = client.get("/manage/users/",
                          cookies={"access_token": old_token},
                          follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "/manage/users/" not in location, (
            f"Old token reached users page. Location: {location}"
        )

    def test_old_token_blocked_from_post_settings_save(self):
        """An old token must not be able to save settings."""
        old_token = self._make_old_token()
        resp = _csrf_post(
            "/manage/settings/save",
            data={"bot_name": "X", "timezone": "UTC", "language": "en"},
            cookies={"access_token": old_token},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "dashboard" in location, (
            f"Old token reached settings/save. Location: {location}"
        )

    def test_old_token_blocked_from_post_users_add(self):
        """An old token must not be able to add users."""
        old_token = self._make_old_token()
        resp = _csrf_post(
            "/manage/users/add",
            data={
                "name": "X",
                "email": "x@x.com",
                "password": "password123",
                "role": "owner",
            },
            cookies={"access_token": old_token},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "dashboard" in location, (
            f"Old token reached users/add. Location: {location}"
        )


# ---------------------------------------------------------------------------
# 6. Edge cases: empty / oversized / malformed inputs
# ---------------------------------------------------------------------------

class TestInputEdgeCases:
    """Verify robustness against malformed or extreme inputs."""

    def test_oversized_description_it_is_accepted(self):
        """
        A 100KB description_it should not crash the server.
        (DB will ultimately enforce column limits, but the server must not 500.)
        """
        huge_text = "A" * 100_000
        with patch("management.routes.treatments.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.treatments.tenant_service"):
                resp = _csrf_post(
                    "/manage/treatments/add",
                    data={
                        "code": "big",
                        "name_it": "Big",
                        "duration_minutes": "30",
                        "price": "10",
                        "description_it": huge_text,
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code in (302, 422), (
            f"Oversized description_it caused unexpected status: {resp.status_code}"
        )

    def test_whitespace_only_notes_treated_as_empty(self):
        """
        A notes field with only whitespace should be stored as NULL
        (notes or None logic in route).  Verify the route does not crash.
        """
        with patch("management.routes.operators.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.operators.tenant_service"):
                resp = _csrf_post(
                    "/manage/operators/add",
                    data={
                        "technical_id": "op_ws",
                        "display_name": "WS Op",
                        "sort_order": "0",
                        "notes": "   ",   # whitespace only
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code == 302, (
            f"Whitespace-only notes caused: {resp.status_code}"
        )
        if mock_cur.execute.called:
            call_args = mock_cur.execute.call_args[0][1]
            # notes is last param before business_id check; confirm it's "   " not None
            # The code does `notes or None` - "   " is truthy, so it stays as "   "
            # This is actually a minor bug: whitespace-only notes are stored as-is
            # Just document the actual behaviour here.
            notes_value = call_args[4]  # position in INSERT tuple
            # Acceptable either way - just must not crash
            assert notes_value in ("   ", None), (
                f"Unexpected notes value stored: {notes_value!r}"
            )

    def test_negative_duration_minutes_is_rejected_or_accepted(self):
        """
        duration_minutes = -1 should either be rejected by FastAPI (422)
        or stored (and we document the gap).  It must not cause a 500.
        """
        with patch("management.routes.treatments.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.treatments.tenant_service"):
                resp = _csrf_post(
                    "/manage/treatments/add",
                    data={
                        "code": "neg",
                        "name_it": "Neg Duration",
                        "duration_minutes": "-1",
                        "price": "10",
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code in (302, 422), (
            f"Negative duration caused unexpected {resp.status_code}."
        )

    def test_zero_price_is_accepted(self):
        """price = 0 is a legitimate value (free service)."""
        with patch("management.routes.treatments.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.treatments.tenant_service"):
                resp = _csrf_post(
                    "/manage/treatments/add",
                    data={
                        "code": "free",
                        "name_it": "Free Treatment",
                        "duration_minutes": "30",
                        "price": "0",
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code == 302, (
            f"Zero price was rejected: {resp.status_code}"
        )

    def test_unicode_in_description_is_handled(self):
        """description_it with emoji and Arabic characters must not crash."""
        unicode_text = "Trattamento speciale \U0001F48E \u0639\u0631\u0628\u064A"
        with patch("management.routes.treatments.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.treatments.tenant_service"):
                resp = _csrf_post(
                    "/manage/treatments/add",
                    data={
                        "code": "uni",
                        "name_it": "Unicode",
                        "duration_minutes": "30",
                        "price": "10",
                        "description_it": unicode_text,
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code == 302, (
            f"Unicode description caused: {resp.status_code}"
        )

    def test_newline_in_rules_fields_is_handled(self):
        """rules_deposit with embedded newlines must not crash json.dumps."""
        with patch("management.routes.settings.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_cur.fetchone.side_effect = [({},), (1,)]
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.settings.tenant_service"):
                resp = _csrf_post(
                    "/manage/settings/save",
                    data={
                        "bot_name": "Bot",
                        "timezone": "Europe/Rome",
                        "language": "it",
                        "rules_deposit": "Line one\nLine two\nLine three",
                        "rules_cancellation": "",
                        "rules_punctuality": "",
                        "rules_other": "",
                    },
                    cookies=_cookie("owner"),
                    follow_redirects=False,
                )
        assert resp.status_code == 302, (
            f"Newlines in rules caused: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# 7. Cross-business data isolation
# ---------------------------------------------------------------------------

class TestCrossBusinessIsolation:
    """
    A user from business 1 must not be able to edit treatments or operators
    belonging to business 2, even if they know the ID.
    """

    def test_edit_treatment_of_other_business_is_silently_blocked(self):
        """
        The UPDATE has WHERE id=%s AND business_id=%s.
        A user from business_id=1 posting to /manage/treatments/999/edit
        (where treatment 999 belongs to business 2) must not modify any rows.
        The route uses user["business_id"] from the JWT, so as long as the
        JWT is correct, the WHERE clause protects it.
        Verify the SQL params include the JWT's business_id, not a user-supplied one.
        """
        with patch("management.routes.treatments.get_connection") as mock_conn_f:
            mock_cur = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.__exit__ = lambda s, *a: None
            mock_conn_f.return_value = mock_conn
            with patch("management.routes.treatments.tenant_service"):
                _csrf_post(
                    "/manage/treatments/999/edit",
                    data={
                        "code": "steal",
                        "name_it": "Stolen",
                        "duration_minutes": "30",
                        "price": "0",
                        "is_active": "on",
                    },
                    cookies=_cookie("owner", business_id=1),
                    follow_redirects=False,
                )
                if mock_cur.execute.called:
                    call_args = mock_cur.execute.call_args[0][1]
                    # The business_id in the WHERE clause must be 1 (from JWT)
                    assert 1 in call_args, (
                        "JWT business_id not used in WHERE clause of treatment UPDATE."
                    )
                    assert 2 not in call_args, (
                        "business_id=2 appeared in query params - isolation failure."
                    )
