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


class TestWebhookPhoneNumberExtraction:
    """Extract phone_number_id from Meta webhook payload."""

    def test_extracts_phone_number_id(self):
        from business_context import extract_phone_number_id
        value = {
            "metadata": {"phone_number_id": "961636900357709"},
            "messages": [{"from": "393331234567", "type": "text", "text": {"body": "Ciao"}}],
        }
        assert extract_phone_number_id(value) == "961636900357709"

    def test_returns_none_when_missing(self):
        from business_context import extract_phone_number_id
        value = {"messages": []}
        assert extract_phone_number_id(value) is None


def _make_biz_context():
    """Helper to create test business context."""
    return {
        "business": {
            "id": 1, "name": "Test Salon", "bot_name": "TestBot",
            "bot_persona": "You are TestBot at Test Salon.",
            "address": "Via Test 1", "phone": "+39 000", "email": "test@test.it",
            "timezone": "Europe/Rome", "language": "it",
            "owner_email": "owner@test.it",
        },
        "services": {
            "taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45, "description": None},
        },
        "hours": {
            0: {"is_open": False, "open_time": None, "close_time": None},
            1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        },
        "closures": [{"date": "2026-12-25", "reason": "Natale"}],
    }


class TestBuildSystemPrompt:
    """Build system prompt dynamically from business context."""

    def test_prompt_includes_business_name(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "Test Salon" in prompt

    def test_prompt_includes_bot_name(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "TestBot" in prompt

    def test_prompt_includes_services(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "taglio_donna" in prompt
        assert "60" in prompt

    def test_prompt_includes_hours(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "09:00" in prompt

    def test_prompt_includes_closures(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "Natale" in prompt


class TestBuildBookingTools:
    """Build OpenAI tool definitions dynamically."""

    def test_service_enum_matches_db(self):
        from business_context import build_booking_tools
        services = {"taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45}}
        tools = build_booking_tools(services)
        # Find create_appointment tool
        create_tool = next(t for t in tools if t["function"]["name"] == "create_appointment")
        enum_values = create_tool["function"]["parameters"]["properties"]["service_type"]["enum"]
        assert "taglio_donna" in enum_values
        assert len(enum_values) == 1  # Only services from DB

    def test_returns_all_tool_types(self):
        from business_context import build_booking_tools
        services = {"taglio_donna": {"name_it": "Taglio", "name_en": "Cut", "price": 60, "duration": 45}}
        tools = build_booking_tools(services)
        tool_names = [t["function"]["name"] for t in tools]
        assert "create_appointment" in tool_names
        assert "check_availability" in tool_names
        assert "get_customer_appointments" in tool_names
        assert "cancel_appointment" in tool_names
        assert "get_available_slots" in tool_names
        assert "escalate_to_human" in tool_names
