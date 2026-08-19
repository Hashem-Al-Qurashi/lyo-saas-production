"""Tests for TenantService -- no real database required."""

import time
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from app.services.tenant import TenantService
from app.models.schemas import Business


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_connection(cursor_side_effects):
    """Return a context-manager that yields a mock connection whose cursor
    returns *cursor_side_effects* on successive fetchone / fetchall calls."""

    mock_cursor = MagicMock()
    # Each call to fetchone/fetchall returns the next value
    mock_cursor.fetchone.side_effect = cursor_side_effects.get("fetchone", [])
    mock_cursor.fetchall.side_effect = cursor_side_effects.get("fetchall", [])

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield mock_conn

    return _ctx


# A sample business row (16 columns matching the SELECT)
_BIZ_ROW = (
    1,     # id
    100,   # chatwoot_account_id
    "Aura Hair",  # name
    "aura-hair",  # slug
    "Europe/Rome",  # timezone
    "it",  # language
    "Simone",  # bot_name
    None,  # bot_persona
    "Via Roma 1",  # address
    "+390000000",  # phone
    "info@aura.it",  # email
    "primary",  # google_calendar_id
    None,  # google_service_account_json
    None,  # owner_email
    "active",  # status
    {},  # settings
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTenantServiceCacheHit:
    """When the business is already cached and TTL has not expired,
    _load_from_db must NOT be called."""

    def test_cache_hit_returns_business(self):
        svc = TenantService()
        biz = Business(id=1, chatwoot_account_id=100, name="Cached Biz")
        svc._cache[100] = (biz, time.time())  # fresh

        result = svc.get_business(100)
        assert result is biz
        assert result.name == "Cached Biz"

    def test_cache_hit_does_not_call_db(self):
        svc = TenantService()
        biz = Business(id=1, chatwoot_account_id=100, name="Cached Biz")
        svc._cache[100] = (biz, time.time())

        with patch.object(svc, "_load_from_db") as mock_load:
            svc.get_business(100)
            mock_load.assert_not_called()


class TestTenantServiceCacheMiss:
    """When the cache is empty, the service queries the database."""

    @patch("app.services.tenant.get_connection")
    def test_loads_from_db_on_miss(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_connection(
            {
                "fetchone": [_BIZ_ROW],
                "fetchall": [
                    # treatments
                    [],
                    # operators
                    [],
                    # operator_treatments
                    [],
                    # business_hours
                    [],
                    # operator_hours
                    [],
                    # business_closures
                    [],
                ],
            }
        )

        svc = TenantService()
        biz = svc.get_business(100)
        assert biz is not None
        assert biz.name == "Aura Hair"
        assert biz.chatwoot_account_id == 100

    @patch("app.services.tenant.get_connection")
    def test_unknown_account_returns_none(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_connection(
            {"fetchone": [None], "fetchall": []}
        )

        svc = TenantService()
        assert svc.get_business(999) is None


class TestTenantServiceInvalidation:

    def test_invalidate_removes_entry(self):
        svc = TenantService()
        biz = Business(id=1, chatwoot_account_id=100, name="X")
        svc._cache[100] = (biz, time.time())

        svc.invalidate(100)
        assert 100 not in svc._cache

    def test_invalidate_all_clears_cache(self):
        svc = TenantService()
        svc._cache[1] = (MagicMock(), time.time())
        svc._cache[2] = (MagicMock(), time.time())

        svc.invalidate_all()
        assert len(svc._cache) == 0


class TestTenantServiceCacheExpiry:

    def test_expired_entry_triggers_db_load(self):
        svc = TenantService()
        biz = Business(id=1, chatwoot_account_id=100, name="Old")
        svc._cache[100] = (biz, time.time() - 9999)  # long expired

        with patch.object(svc, "_load_from_db", return_value=None) as mock_load:
            result = svc.get_business(100)
            mock_load.assert_called_once_with(100)
            assert result is None
