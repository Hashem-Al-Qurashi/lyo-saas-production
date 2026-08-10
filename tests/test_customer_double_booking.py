"""
Regression test: customer must not be able to book overlapping appointments on same day.

Root cause: Greta (phone 393312671591) booked detox_2_0 at 09:00 in one session,
then 2 hours later booked balayage_schiariture also at 09:00 (different operator).
The unique index only guards (business_id, operator_id, date, time); customer conflicts
across operators were never checked.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta, date as date_cls

# Ensure the deployed-live/bot package is on the path (before root-level business_context)
BOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'deployed-live', 'bot'))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
os.environ.setdefault("OPENAI_API_KEY", "test-key")

# salon_bot_with_booking is imported inside each test via `import salon_bot_with_booking as bot`
# to avoid module-level caching conflicts when the full test suite runs.


def _make_biz_context():
    return {
        "business": {
            "id": 1, "name": "Aura Hair Studio", "bot_name": "Lyo",
            "bot_persona": "x", "address": "a", "phone": "b", "email": "c@c.it",
            "timezone": "Europe/Rome", "language": "it", "owner_email": "o@o.it",
        },
        "services": {
            "detox_2_0": {
                "name_it": "Detox 2.0", "name_en": "Detox 2.0",
                "price": 80, "duration": 90, "description": None,
            },
            "balayage_schiariture": {
                "name_it": "Balayage/Schiariture", "name_en": "Balayage",
                "price": 130, "duration": 150, "description": None,
            },
        },
        "hours": {
            # Saturday (5) open 09:00-19:00
            5: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        },
        "closures": [],
        "operators": [
            {"id": 10, "display_name": "Irene", "is_active": True,
             "treatments": ["detox_2_0", "piega"], "hours": {}},
            {"id": 11, "display_name": "Giulia", "is_active": True,
             "treatments": ["balayage_schiariture", "piega"], "hours": {}},
        ],
        "faqs": [],
    }


def _mock_cursor_for_booking(
    *,
    operator_count=0,          # 0 = operator slot free
    customer_conflict=None,    # None or (id, treatment_name, time_str)
    insert_id=999,
):
    """Build a mock cursor whose sequential fetchone() calls match create_appointment flow."""
    cur = MagicMock()

    fetchone_results = []

    # 1. Operator availability check → (count,)
    fetchone_results.append((operator_count,))

    # 2. Customer conflict check → row or None
    fetchone_results.append(customer_conflict)

    if customer_conflict is None:
        # 3. INSERT RETURNING id
        fetchone_results.append((insert_id,))
        # 4. Auto-addon select (may or may not fire) — give a safe None
        fetchone_results.append(None)
        # 5. Extra selects (operator sort_order, etc.)
        for _ in range(5):
            fetchone_results.append(None)

    cur.fetchone.side_effect = fetchone_results
    cur.fetchall.return_value = []
    return cur


class TestCustomerDoubleBookingPrevention:

    def _import_create_appointment(self):
        """Import create_appointment with all heavy deps patched."""
        import importlib
        mocks = {
            'openai': MagicMock(),
            'requests': MagicMock(),
            'psycopg2': MagicMock(),
        }
        with patch.dict('sys.modules', mocks):
            import salon_bot_with_booking as bot
            return bot.create_appointment

    @patch('salon_bot_with_booking.create_calendar_event', return_value=None)
    @patch('salon_bot_with_booking.get_db_connection')
    @patch('salon_bot_with_booking.validate_day_and_time')
    @patch('salon_bot_with_booking.resolve_operator')
    @patch('salon_bot_with_booking.normalize_phone', side_effect=lambda p: p)
    def test_blocks_customer_overlap_same_time(
        self, mock_norm, mock_resolve_op, mock_validate, mock_db, mock_cal
    ):
        """
        GIVEN customer already has detox_2_0 at 09:00
        WHEN same customer books balayage_schiariture also at 09:00
        THEN create_appointment returns CUSTOMER_ALREADY_BOOKED error
        """
        import salon_bot_with_booking as bot

        mock_validate.return_value = {"valid": True}
        mock_resolve_op.return_value = {
            "success": True,
            "operator_id": 11,
            "operator_name": "Giulia",
            "auto_assign": False,
        }

        # Operator slot is FREE (count=0), but customer has existing detox at 09:00
        cur = _mock_cursor_for_booking(
            operator_count=0,
            customer_conflict=(185, "Detox 2.0", "09:00:00"),
        )
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_db.return_value)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.cursor.return_value = cur

        result = bot.create_appointment(
            customer_phone="393312671591",
            customer_name="Greta",
            service_type="balayage_schiariture",
            date="2026-08-29",
            time="09:00",
            business_id=1,
            biz_context=_make_biz_context(),
        )

        assert result["success"] is False, f"Expected failure, got: {result}"
        assert result["error"] == "CUSTOMER_ALREADY_BOOKED", f"Wrong error: {result.get('error')}"
        assert "09:00" in result.get("existing_time", ""), f"Missing existing_time: {result}"

    @patch('salon_bot_with_booking.create_calendar_event', return_value=None)
    @patch('salon_bot_with_booking.get_db_connection')
    @patch('salon_bot_with_booking.validate_day_and_time')
    @patch('salon_bot_with_booking.resolve_operator')
    @patch('salon_bot_with_booking.normalize_phone', side_effect=lambda p: p)
    def test_blocks_customer_overlap_adjacent_time(
        self, mock_norm, mock_resolve_op, mock_validate, mock_db, mock_cal
    ):
        """
        GIVEN customer has detox_2_0 at 09:00 (90 min, ends 10:30)
        WHEN same customer tries to book piega at 10:00 (30 min)
        THEN blocked — 10:00 < 10:30 (detox end time)
        """
        import salon_bot_with_booking as bot

        mock_validate.return_value = {"valid": True}
        mock_resolve_op.return_value = {
            "success": True,
            "operator_id": 10,
            "operator_name": "Irene",
            "auto_assign": False,
        }

        cur = _mock_cursor_for_booking(
            operator_count=0,
            customer_conflict=(185, "Detox 2.0", "09:00:00"),
        )
        mock_db.return_value.cursor.return_value = cur

        result = bot.create_appointment(
            customer_phone="393312671591",
            customer_name="Greta",
            service_type="balayage_schiariture",
            date="2026-08-29",
            time="10:00",
            business_id=1,
            biz_context=_make_biz_context(),
        )

        assert result["success"] is False
        assert result["error"] == "CUSTOMER_ALREADY_BOOKED"

    @patch('salon_bot_with_booking.create_calendar_event', return_value="evt_123")
    @patch('salon_bot_with_booking.get_db_connection')
    @patch('salon_bot_with_booking.validate_day_and_time')
    @patch('salon_bot_with_booking.resolve_operator')
    @patch('salon_bot_with_booking.normalize_phone', side_effect=lambda p: p)
    def test_allows_non_overlapping_same_day(
        self, mock_norm, mock_resolve_op, mock_validate, mock_db, mock_cal
    ):
        """
        GIVEN customer has detox_2_0 at 09:00 (90 min, ends 10:30)
        WHEN same customer books piega at 11:30 (after detox ends)
        THEN allowed — no overlap
        """
        import salon_bot_with_booking as bot

        mock_validate.return_value = {"valid": True}
        mock_resolve_op.return_value = {
            "success": True,
            "operator_id": 10,
            "operator_name": "Irene",
            "auto_assign": False,
        }

        # No customer conflict for 11:30
        cur = _mock_cursor_for_booking(
            operator_count=0,
            customer_conflict=None,
            insert_id=200,
        )
        mock_db.return_value.cursor.return_value = cur
        conn_mock = MagicMock()
        conn_mock.cursor.return_value = cur
        mock_db.return_value = conn_mock

        biz = _make_biz_context()
        # Add piega service
        biz["services"]["piega"] = {
            "name_it": "Piega", "name_en": "Blow-dry",
            "price": 30, "duration": 30, "description": None,
        }

        result = bot.create_appointment(
            customer_phone="393312671591",
            customer_name="Greta",
            service_type="piega",
            date="2026-08-29",
            time="11:30",
            business_id=1,
            biz_context=biz,
        )

        # Should succeed (or at minimum NOT fail with CUSTOMER_ALREADY_BOOKED)
        assert result.get("error") != "CUSTOMER_ALREADY_BOOKED", \
            f"Incorrectly blocked non-overlapping booking: {result}"
