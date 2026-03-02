"""Tests for business context loader."""

import pytest
from unittest.mock import patch, MagicMock

from business_context import (
    load_business_by_phone_number_id,
    load_services,
    load_business_hours,
    load_closures,
    build_services_dict,
    BusinessNotFoundError,
)


class TestLoadBusiness:
    """Load business by WhatsApp phone_number_id."""

    @patch("business_context.get_db_connection")
    def test_returns_business_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.description = [
            ("id",), ("name",), ("slug",), ("timezone",), ("language",),
            ("bot_name",), ("bot_persona",), ("address",), ("phone",),
            ("email",), ("owner_email",), ("google_calendar_id",),
            ("whatsapp_phone_number_id",), ("waba_id",), ("meta_access_token",),
            ("google_service_account_json",), ("status",), ("settings",),
        ]
        mock_cur.fetchone.return_value = (
            1, "Aura Hair Studio", "aura-hair-studio", "Europe/Rome", "it",
            "Simone", "You are Simone...", "Via dei Giardini 24", "+39 02 8394 5621",
            "info@aura.it", "owner@aura.it", "primary",
            "961636900357709", "waba_123", "token_abc",
            None, "active", {},
        )
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        biz = load_business_by_phone_number_id("961636900357709")
        assert biz["id"] == 1
        assert biz["name"] == "Aura Hair Studio"
        assert biz["bot_name"] == "Simone"
        assert biz["whatsapp_phone_number_id"] == "961636900357709"

    @patch("business_context.get_db_connection")
    def test_raises_not_found(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        with pytest.raises(BusinessNotFoundError):
            load_business_by_phone_number_id("unknown_id")


class TestLoadServices:
    """Load treatments from DB into SALON_SERVICES-compatible dict."""

    @patch("business_context.get_db_connection")
    def test_returns_services_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("taglio_donna", "Taglio Donna", "Women's Haircut", 45, 60.00, "Haircut desc"),
            ("piega", "Piega", "Styling", 30, 30.00, None),
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        services = load_services(business_id=1)
        assert len(services) == 2
        assert services["taglio_donna"]["name_it"] == "Taglio Donna"
        assert services["taglio_donna"]["price"] == 60.00
        assert services["taglio_donna"]["duration"] == 45

    @patch("business_context.get_db_connection")
    def test_empty_returns_empty_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        services = load_services(business_id=999)
        assert services == {}


class TestLoadBusinessHours:
    """Load business hours from DB."""

    @patch("business_context.get_db_connection")
    def test_returns_hours_by_day(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (0, False, None, None),       # Monday closed
            (1, True, "09:00", "19:00"),   # Tuesday open
            (2, True, "09:00", "19:00"),   # Wednesday open
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        hours = load_business_hours(business_id=1)
        assert hours[0]["is_open"] is False
        assert hours[1]["is_open"] is True
        assert hours[1]["open_time"] == "09:00"
        assert hours[1]["close_time"] == "19:00"


class TestLoadClosures:
    """Load business closures (holidays)."""

    @patch("business_context.get_db_connection")
    def test_returns_closure_dates(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("2026-12-25", "Natale"),
            ("2027-01-01", "Capodanno"),
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        closures = load_closures(business_id=1)
        assert len(closures) == 2
        assert closures[0]["date"] == "2026-12-25"
        assert closures[0]["reason"] == "Natale"


class TestBuildServicesDict:
    """Build human-readable services string for AI prompt."""

    def test_formats_services(self):
        services = {
            "taglio_donna": {
                "name_it": "Taglio Donna",
                "name_en": "Women's Haircut",
                "duration": 45,
                "price": 60.0,
                "description": "Haircut desc",
            },
            "piega": {
                "name_it": "Piega",
                "name_en": "Styling",
                "duration": 30,
                "price": 30.0,
                "description": None,
            },
        }
        result = build_services_dict(services)
        assert "taglio_donna" in result
        assert "Taglio Donna" in result
        assert "60" in result
        assert "45 min" in result

    def test_empty_services(self):
        result = build_services_dict({})
        assert result == ""
