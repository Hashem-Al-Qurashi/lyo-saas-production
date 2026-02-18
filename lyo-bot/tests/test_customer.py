"""Tests for CustomerService -- mocked database."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from app.services.customer import CustomerService
from app.models.schemas import Customer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_ctx(fetchone_return=None):
    """Context-manager yielding a connection whose cursor.fetchone returns
    *fetchone_return*."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_return

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield mock_conn

    return _ctx


_CUSTOMER_ROW = (1, 10, "+393331234567", "Maria", "Rossi", "whatsapp", 99)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNormalizePhone:

    def test_strips_spaces_and_dashes(self):
        assert CustomerService._normalize_phone("+39 333-123 4567") == "+393331234567"

    def test_adds_plus_prefix(self):
        assert CustomerService._normalize_phone("393331234567") == "+393331234567"

    def test_already_normalized(self):
        assert CustomerService._normalize_phone("+393331234567") == "+393331234567"


class TestGetCustomer:

    @patch("app.services.customer.get_connection")
    def test_returns_customer_when_found(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_conn_ctx(fetchone_return=_CUSTOMER_ROW)

        svc = CustomerService()
        c = svc.get_customer(10, "+393331234567")

        assert c is not None
        assert c.first_name == "Maria"
        assert c.last_name == "Rossi"
        assert c.phone == "+393331234567"

    @patch("app.services.customer.get_connection")
    def test_returns_none_when_not_found(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_conn_ctx(fetchone_return=None)

        svc = CustomerService()
        assert svc.get_customer(10, "+390000000000") is None


class TestGetOrCreate:

    @patch("app.services.customer.get_connection")
    def test_returns_existing_customer(self, mock_get_conn):
        """If the customer already exists, no INSERT should happen."""
        mock_get_conn.side_effect = _mock_conn_ctx(fetchone_return=_CUSTOMER_ROW)

        svc = CustomerService()
        c = svc.get_or_create(10, "+393331234567")

        assert c.first_name == "Maria"

    @patch("app.services.customer.get_connection")
    def test_creates_new_customer(self, mock_get_conn):
        """First call (get) returns None, second call (insert) returns row."""
        new_row = (2, 10, "+390001112222", None, None, "whatsapp", None)

        call_count = {"n": 0}

        @contextmanager
        def _dual_ctx():
            mock_cursor = MagicMock()
            # First get_connection call is get_customer (fetchone -> None)
            # Second get_connection call is the INSERT (fetchone -> new_row)
            if call_count["n"] == 0:
                mock_cursor.fetchone.return_value = None
            else:
                mock_cursor.fetchone.return_value = new_row
            call_count["n"] += 1

            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            yield mock_conn

        mock_get_conn.side_effect = lambda: _dual_ctx()

        svc = CustomerService()
        c = svc.get_or_create(10, "+390001112222", platform="whatsapp")

        assert c.phone == "+390001112222"


class TestUpdateName:

    @patch("app.services.customer.get_connection")
    def test_updates_successfully(self, mock_get_conn):
        updated_row = (1, 10, "+393331234567", "Anna", "Bianchi", "whatsapp", 99)
        mock_get_conn.side_effect = _mock_conn_ctx(fetchone_return=updated_row)

        svc = CustomerService()
        c = svc.update_name(10, "+393331234567", "Anna", "Bianchi")

        assert c is not None
        assert c.first_name == "Anna"
        assert c.last_name == "Bianchi"

    @patch("app.services.customer.get_connection")
    def test_returns_none_when_not_found(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_conn_ctx(fetchone_return=None)

        svc = CustomerService()
        assert svc.update_name(10, "+390000000000", "X") is None
