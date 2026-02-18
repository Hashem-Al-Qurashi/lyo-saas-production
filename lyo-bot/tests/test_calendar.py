"""Tests for calendar module -- mocked Google API."""

from unittest.mock import patch, MagicMock

from app.services.calendar import get_calendar_service, create_calendar_event, delete_calendar_event
from app.models.schemas import Business


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_business(sa_json=None) -> Business:
    return Business(
        id=1, chatwoot_account_id=100, name="Test Salon",
        google_calendar_id="primary",
        google_service_account_json=sa_json,
    )


# ---------------------------------------------------------------------------
# Tests: get_calendar_service
# ---------------------------------------------------------------------------

class TestGetCalendarService:

    @patch("app.services.calendar.settings")
    def test_returns_none_when_no_credentials(self, mock_settings):
        mock_settings.google_service_account_file = ""
        biz = _make_business()
        result = get_calendar_service(biz)
        assert result is None

    @patch("app.services.calendar.settings")
    @patch("app.services.calendar.json")
    def test_builds_service_from_sa_json(self, mock_json, mock_settings):
        mock_json.loads.return_value = {"type": "service_account"}

        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as mock_creds, \
             patch("googleapiclient.discovery.build") as mock_build:
            mock_creds.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            biz = _make_business(sa_json='{"type":"service_account"}')
            svc = get_calendar_service(biz)
            assert svc is not None
            mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: create_calendar_event
# ---------------------------------------------------------------------------

class TestCreateCalendarEvent:

    @patch("app.services.calendar.get_calendar_service")
    def test_returns_none_when_no_service(self, mock_get_svc):
        mock_get_svc.return_value = None
        biz = _make_business()
        result = create_calendar_event(
            biz, "Maria", "Taglio", "2026-03-04", "10:00", 45,
        )
        assert result is None

    @patch("app.services.calendar.get_calendar_service")
    def test_returns_event_id_on_success(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.events.return_value.insert.return_value.execute.return_value = {
            "id": "evt_123"
        }
        mock_get_svc.return_value = mock_service

        biz = _make_business()
        result = create_calendar_event(
            biz, "Maria", "Taglio Donna", "2026-03-04", "10:00", 45,
            operator_name="Giulia", customer_phone="+393331234567",
        )
        assert result == "evt_123"


# ---------------------------------------------------------------------------
# Tests: delete_calendar_event
# ---------------------------------------------------------------------------

class TestDeleteCalendarEvent:

    @patch("app.services.calendar.get_calendar_service")
    def test_returns_false_when_no_service(self, mock_get_svc):
        mock_get_svc.return_value = None
        biz = _make_business()
        assert delete_calendar_event(biz, "evt_123") is False

    @patch("app.services.calendar.get_calendar_service")
    def test_returns_true_on_success(self, mock_get_svc):
        mock_service = MagicMock()
        mock_service.events.return_value.delete.return_value.execute.return_value = None
        mock_get_svc.return_value = mock_service

        biz = _make_business()
        assert delete_calendar_event(biz, "evt_123") is True
