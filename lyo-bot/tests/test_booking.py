"""Tests for BookingService -- mocked database and dependencies."""

from datetime import date, time
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from app.services.booking import BookingService
from app.models.schemas import Business, Operator, Treatment, BusinessHours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_business() -> Business:
    return Business(
        id=1, chatwoot_account_id=100, name="Test Salon",
        operators=[
            Operator(id=1, business_id=1, technical_id="op1",
                     display_name="Giulia", treatment_ids=[1]),
        ],
        treatments=[
            Treatment(id=1, business_id=1, code="taglio_donna",
                      name_it="Taglio Donna", duration_minutes=45, price=60,
                      operator_ids=[1]),
        ],
        hours=[
            BusinessHours(business_id=1, day_of_week=i, is_open=True,
                          open_time=time(9, 0), close_time=time(18, 0))
            for i in range(5)
        ],
    )


def _mock_conn_insert(returning_id):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (returning_id,)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield mock_conn

    return _ctx


WEDNESDAY = date(2026, 3, 4)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateAppointment:

    @patch("app.services.booking.cal_module.create_calendar_event", return_value="evt_abc")
    @patch("app.services.booking.get_connection")
    @patch("app.services.booking.availability_service")
    def test_create_with_auto_assign(self, mock_avail, mock_get_conn, mock_cal):
        mock_avail.check_slot.return_value = {
            "available": True,
            "operator": "Giulia",
            "operator_id": 1,
            "treatment": "Taglio Donna",
            "treatment_code": "taglio_donna",
            "duration_minutes": 45,
            "price": "60",
        }
        mock_get_conn.side_effect = _mock_conn_insert(101)

        svc = BookingService()
        biz = _make_business()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name="Maria Rossi",
            treatment_code="taglio_donna",
            appt_date=WEDNESDAY,
            appt_time=time(10, 0),
        )

        assert result["success"] is True
        assert result["appointment_id"] == 101
        assert result["operator"] == "Giulia"
        assert result["treatment"] == "Taglio Donna"
        assert result["google_event_id"] == "evt_abc"

    @patch("app.services.booking.availability_service")
    def test_create_fails_when_slot_busy(self, mock_avail):
        mock_avail.check_slot.return_value = {
            "available": False,
            "reason": "ALL_OPERATORS_BUSY",
            "alternatives": [{"time": "11:00", "operator": "Giulia"}],
        }

        svc = BookingService()
        biz = _make_business()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name="Maria Rossi",
            treatment_code="taglio_donna",
            appt_date=WEDNESDAY,
            appt_time=time(10, 0),
        )

        assert result["success"] is False
        assert result["error"] == "ALL_OPERATORS_BUSY"
        assert len(result["alternatives"]) == 1

    def test_create_fails_without_name(self):
        svc = BookingService()
        biz = _make_business()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name="",
            treatment_code="taglio_donna",
            appt_date=WEDNESDAY,
            appt_time=time(10, 0),
        )

        assert result["success"] is False
        assert result["error"] == "CUSTOMER_NAME_REQUIRED"

    def test_create_fails_with_none_name(self):
        svc = BookingService()
        biz = _make_business()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name=None,
            treatment_code="taglio_donna",
            appt_date=WEDNESDAY,
            appt_time=time(10, 0),
        )

        assert result["success"] is False
        assert result["error"] == "CUSTOMER_NAME_REQUIRED"

    def test_create_fails_with_whitespace_name(self):
        svc = BookingService()
        biz = _make_business()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name="   ",
            treatment_code="taglio_donna",
            appt_date=WEDNESDAY,
            appt_time=time(10, 0),
        )

        assert result["success"] is False
        assert result["error"] == "CUSTOMER_NAME_REQUIRED"

    @patch("app.services.booking.cal_module.create_calendar_event", return_value="evt_abc")
    @patch("app.services.booking.get_connection")
    @patch("app.services.booking.availability_service")
    def test_create_falls_back_to_auto_assign_when_preferred_operator_busy(
        self, mock_avail, mock_get_conn, mock_cal
    ):
        """Greta bug: check_availability(null) → Sara free → AI echoes Sara →
        create_appointment("Sara") → Sara now busy (TOCTOU race) → must fall back
        to auto-assign and book Federica instead of failing the booking.

        Without the fix this test is RED (returns PREFERRED_OPERATOR_BUSY).
        With the fix it is GREEN (falls back to Federica).
        """
        biz = Business(
            id=1, chatwoot_account_id=100, name="Test Salon",
            operators=[
                Operator(id=1, business_id=1, technical_id="op1",
                         display_name="Sara", treatment_ids=[1]),
                Operator(id=2, business_id=1, technical_id="op2",
                         display_name="Federica", treatment_ids=[1]),
            ],
            treatments=[
                Treatment(id=1, business_id=1, code="henne",
                          name_it="Henné", duration_minutes=60, price=50,
                          operator_ids=[1, 2]),
            ],
            hours=[
                BusinessHours(business_id=1, day_of_week=i, is_open=True,
                              open_time=time(9, 0), close_time=time(20, 0))
                for i in range(5)
            ],
        )

        # First call (preferred_operator="Sara"): Sara busy
        preferred_busy = {
            "available": False,
            "reason": "PREFERRED_OPERATOR_BUSY",
            "operator": "Sara",
            "alternatives": [{"time": "15:00"}, {"time": "16:00"}],
        }
        # Second call (preferred_operator=None, auto-assign): Federica free
        auto_assign_ok = {
            "available": True,
            "operator": "Federica",
            "operator_id": 2,
            "treatment": "Henné",
            "treatment_code": "henne",
            "duration_minutes": 60,
            "price": "50",
        }
        mock_avail.check_slot.side_effect = [preferred_busy, auto_assign_ok]
        mock_get_conn.side_effect = _mock_conn_insert(202)

        svc = BookingService()
        result = svc.create_appointment(
            business=biz,
            customer_phone="+393331234567",
            customer_name="Greta Biondin",
            treatment_code="henne",
            appt_date=WEDNESDAY,
            appt_time=time(17, 0),
            preferred_operator="Sara",  # AI echoed this from check_availability result
        )

        assert result["success"] is True, f"Expected success but got: {result}"
        assert result["operator"] == "Federica"
        assert result["appointment_id"] == 202
