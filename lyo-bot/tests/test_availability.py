"""Tests for AvailabilityService -- mocked database, no real DB required.

This is the most important test file: it covers multi-operator logic,
preferred-operator behaviour, parallel booking, and business-hours checks.
"""

from datetime import date, time, timedelta
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from app.services.availability import AvailabilityService
from app.models.schemas import Business, Operator, Treatment, BusinessHours


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_business(
    operators=None,
    treatments=None,
    hours=None,
) -> Business:
    """Build a Business with default relations."""
    default_treatments = [
        Treatment(
            id=1, business_id=1, code="taglio_donna",
            name_it="Taglio Donna", duration_minutes=45, price=60,
            operator_ids=[1, 2, 3],
        ),
        Treatment(
            id=2, business_id=1, code="colore",
            name_it="Colore", duration_minutes=90, price=120,
            operator_ids=[2],
        ),
    ]
    default_operators = [
        Operator(id=1, business_id=1, technical_id="op1",
                 display_name="Giulia", treatment_ids=[1]),
        Operator(id=2, business_id=1, technical_id="op2",
                 display_name="Marco", treatment_ids=[1, 2]),
        Operator(id=3, business_id=1, technical_id="op3",
                 display_name="Sara", treatment_ids=[1]),
    ]
    default_hours = [
        # Monday-Friday open 09:00-18:00
        BusinessHours(business_id=1, day_of_week=i, is_open=True,
                      open_time=time(9, 0), close_time=time(18, 0))
        for i in range(5)
    ] + [
        # Saturday closed
        BusinessHours(business_id=1, day_of_week=5, is_open=False),
        # Sunday closed
        BusinessHours(business_id=1, day_of_week=6, is_open=False),
    ]
    return Business(
        id=1, chatwoot_account_id=100, name="Test Salon",
        operators=operators or default_operators,
        treatments=treatments or default_treatments,
        hours=hours or default_hours,
    )


def _mock_is_free(free_map: dict):
    """Return a side_effect function for is_operator_free.
    *free_map* maps (operator_id, time_str) -> bool.
    If a key is missing, defaults to True."""
    def _side_effect(business_id, operator_id, appt_date, appt_time, duration):
        key = (operator_id, appt_time.strftime("%H:%M"))
        return free_map.get(key, True)
    return _side_effect


def _mock_conn_count(count_value):
    """Mock get_connection for is_operator_free (returns COUNT(*))."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (count_value,)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield mock_conn

    return _ctx


def _next_weekday(weekday: int) -> date:
    """Return the next future date with the given weekday (0=Mon … 6=Sun).
    Always at least 1 day ahead so it cannot be today and trigger TIME_IN_PAST."""
    today = date.today()
    days_ahead = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


# A Wednesday in the future (day_of_week=2, which is open per default_hours)
WEDNESDAY = _next_weekday(2)
# A Saturday in the future (day_of_week=5, which is closed per default_hours)
SATURDAY = _next_weekday(5)


# ---------------------------------------------------------------------------
# Tests: get_operators_for_treatment
# ---------------------------------------------------------------------------

class TestGetOperatorsForTreatment:

    def test_returns_correct_operators_for_taglio(self):
        biz = _make_business()
        svc = AvailabilityService()
        ops = svc.get_operators_for_treatment(biz, "taglio_donna")
        names = [o.display_name for o in ops]
        assert "Giulia" in names
        assert "Marco" in names
        assert "Sara" in names

    def test_returns_only_qualified_operators(self):
        biz = _make_business()
        svc = AvailabilityService()
        ops = svc.get_operators_for_treatment(biz, "colore")
        assert len(ops) == 1
        assert ops[0].display_name == "Marco"

    def test_unknown_treatment_returns_empty(self):
        biz = _make_business()
        svc = AvailabilityService()
        assert svc.get_operators_for_treatment(biz, "unknown") == []


# ---------------------------------------------------------------------------
# Tests: is_operator_free
# ---------------------------------------------------------------------------

class TestIsOperatorFree:

    @patch("app.services.availability.get_connection")
    def test_free_when_no_appointments(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_conn_count(0)

        svc = AvailabilityService()
        assert svc.is_operator_free(1, 1, WEDNESDAY, time(10, 0), 45) is True

    @patch("app.services.availability.get_connection")
    def test_busy_when_overlap_exists(self, mock_get_conn):
        mock_get_conn.side_effect = _mock_conn_count(1)

        svc = AvailabilityService()
        assert svc.is_operator_free(1, 1, WEDNESDAY, time(10, 0), 45) is False


# ---------------------------------------------------------------------------
# Tests: check_slot -- parallel booking (multi-operator)
# ---------------------------------------------------------------------------

class TestCheckSlotParallel:

    def test_slot_available_when_one_of_three_operators_free(self):
        """Operators 1 & 2 busy at 10:00, but operator 3 (Sara) is free.
        Auto-assign should pick Sara."""
        biz = _make_business()
        svc = AvailabilityService()

        with patch.object(svc, "is_operator_free",
                          side_effect=_mock_is_free({
                              (1, "10:00"): False,
                              (2, "10:00"): False,
                              (3, "10:00"): True,
                          })):
            result = svc.check_slot(biz, "taglio_donna", WEDNESDAY, time(10, 0))

        assert result["available"] is True
        assert result["operator"] == "Sara"

    def test_slot_unavailable_when_all_operators_busy(self):
        """All 3 operators busy at 10:00 => not available."""
        biz = _make_business()
        svc = AvailabilityService()

        with patch.object(svc, "is_operator_free",
                          side_effect=_mock_is_free({
                              (1, "10:00"): False,
                              (2, "10:00"): False,
                              (3, "10:00"): False,
                          })):
            result = svc.check_slot(biz, "taglio_donna", WEDNESDAY, time(10, 0))

        assert result["available"] is False
        assert result["reason"] == "ALL_OPERATORS_BUSY"


# ---------------------------------------------------------------------------
# Tests: check_slot -- preferred operator
# ---------------------------------------------------------------------------

class TestCheckSlotPreferred:

    def test_preferred_operator_free(self):
        biz = _make_business()
        svc = AvailabilityService()

        with patch.object(svc, "is_operator_free", return_value=True):
            result = svc.check_slot(
                biz, "taglio_donna", WEDNESDAY, time(10, 0),
                preferred_operator="Giulia",
            )

        assert result["available"] is True
        assert result["operator"] == "Giulia"
        assert result["operator_id"] == 1

    def test_preferred_operator_busy_suggests_alternatives_for_that_operator(self):
        """When the preferred operator is busy, we should get alternatives
        for THAT operator only -- never auto-switch to another operator.

        Slot generation at 45-min intervals from 09:00 produces:
        09:00, 09:45, 10:30, 11:15, 12:00, ...
        We mark 10:00 as busy (the requested time -- note: not a generated
        slot, but check_slot calls is_operator_free for the requested time
        directly).  The alternatives come from _find_operator_alternatives
        which generates actual slots.  We mark 10:30 as busy too, so it
        should NOT appear in alternatives.
        """
        biz = _make_business()
        svc = AvailabilityService()

        with patch.object(svc, "is_operator_free",
                          side_effect=_mock_is_free({
                              (1, "10:00"): False,
                              (1, "10:30"): False,
                              # all others default to True
                          })):
            result = svc.check_slot(
                biz, "taglio_donna", WEDNESDAY, time(10, 0),
                preferred_operator="Giulia",
            )

        assert result["available"] is False
        assert result["reason"] == "PREFERRED_OPERATOR_BUSY"
        assert result["operator"] == "Giulia"
        # Alternatives should exist and contain times for Giulia
        assert "alternatives" in result
        alt_times = [a["time"] for a in result["alternatives"]]
        # 09:00 and 09:45 should be available (before the busy window)
        assert "09:00" in alt_times
        assert "09:45" in alt_times
        # 11:15 should be available (after the busy window)
        assert "11:15" in alt_times
        # 10:30 should NOT be in alternatives (marked busy)
        assert "10:30" not in alt_times

    def test_preferred_operator_not_found(self):
        biz = _make_business()
        svc = AvailabilityService()

        result = svc.check_slot(
            biz, "taglio_donna", WEDNESDAY, time(10, 0),
            preferred_operator="NonExistent",
        )
        assert result["available"] is False
        assert result["reason"] == "OPERATOR_NOT_FOUND"


# ---------------------------------------------------------------------------
# Tests: business hours validation
# ---------------------------------------------------------------------------

class TestBusinessHours:

    def test_closed_day_returns_invalid(self):
        biz = _make_business()
        svc = AvailabilityService()
        result = svc.validate_business_hours(biz, SATURDAY, time(10, 0))
        assert result["valid"] is False
        assert result["reason"] == "CLOSED_DAY"

    def test_outside_hours_returns_invalid(self):
        biz = _make_business()
        svc = AvailabilityService()
        # 07:00 is before opening (09:00)
        result = svc.validate_business_hours(biz, WEDNESDAY, time(7, 0))
        assert result["valid"] is False
        assert result["reason"] == "OUTSIDE_BUSINESS_HOURS"

    def test_after_closing_returns_invalid(self):
        biz = _make_business()
        svc = AvailabilityService()
        # 18:00 is exactly close_time (should be invalid since >= close_time)
        result = svc.validate_business_hours(biz, WEDNESDAY, time(18, 0))
        assert result["valid"] is False
        assert result["reason"] == "OUTSIDE_BUSINESS_HOURS"

    def test_within_hours_returns_valid(self):
        biz = _make_business()
        svc = AvailabilityService()
        result = svc.validate_business_hours(biz, WEDNESDAY, time(10, 0))
        assert result["valid"] is True

    def test_at_opening_time_is_valid(self):
        biz = _make_business()
        svc = AvailabilityService()
        result = svc.validate_business_hours(biz, WEDNESDAY, time(9, 0))
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# Tests: check_slot rejects outside business hours
# ---------------------------------------------------------------------------

class TestCheckSlotBusinessHours:

    def test_check_slot_on_closed_day(self):
        biz = _make_business()
        svc = AvailabilityService()
        result = svc.check_slot(biz, "taglio_donna", SATURDAY, time(10, 0))
        assert result["available"] is False
        assert result["reason"] == "CLOSED_DAY"


# ---------------------------------------------------------------------------
# Tests: generate_time_slots helper
# ---------------------------------------------------------------------------

class TestGenerateTimeSlots:

    def test_generates_correct_slots(self):
        svc = AvailabilityService()
        slots = svc._generate_time_slots(time(9, 0), time(12, 0), 45)
        slot_strs = [s.strftime("%H:%M") for s in slots]
        # 09:00, 09:45, 10:30, 11:15 -- 12:00 wouldn't fit (12:00+45 > 12:00)
        assert slot_strs == ["09:00", "09:45", "10:30", "11:15"]

    def test_no_slots_if_duration_exceeds_window(self):
        svc = AvailabilityService()
        slots = svc._generate_time_slots(time(9, 0), time(9, 30), 45)
        assert slots == []
