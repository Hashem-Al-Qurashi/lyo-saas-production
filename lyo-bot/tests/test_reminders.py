"""Tests for ReminderService -- mocked database and chatwoot client."""

from unittest.mock import patch, MagicMock, AsyncMock
from contextlib import contextmanager

from app.services.reminders import ReminderService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_with_appointments(appointments):
    """Return a get_connection context manager that yields a mock conn.
    The first call returns appointments, subsequent calls are for the UPDATE."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = appointments

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    call_count = [0]

    @contextmanager
    def _ctx():
        call_count[0] += 1
        yield mock_conn

    return _ctx, mock_cursor


SAMPLE_APPOINTMENT = (
    42,       # appt_id
    1,        # biz_id
    "+393331234567",  # phone
    "Maria",  # name
    "taglio_donna",   # treatment_code
    "Taglio Donna",   # treatment_name (name_it)
    "2026-03-05",     # appointment_date
    "10:00:00",       # appointment_time
    "Giulia",         # operator_name
    999,      # conv_id (chatwoot_conversation_id)
    100,      # account_id (chatwoot_account_id)
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSendDailyReminders:

    @patch("app.services.reminders.chatwoot_client")
    @patch("app.services.reminders.get_connection")
    async def test_sends_message_and_marks_sent(self, mock_get_conn, mock_chatwoot):
        ctx_fn, mock_cursor = _mock_conn_with_appointments([SAMPLE_APPOINTMENT])
        mock_get_conn.side_effect = ctx_fn
        mock_chatwoot.send_message = AsyncMock()

        svc = ReminderService()
        await svc.send_daily_reminders()

        # Verify chatwoot message was sent
        mock_chatwoot.send_message.assert_called_once()
        call_kwargs = mock_chatwoot.send_message.call_args[1]
        assert call_kwargs["account_id"] == 100
        assert call_kwargs["conversation_id"] == 999
        assert "Maria" in call_kwargs["content"]
        assert "Taglio Donna" in call_kwargs["content"]
        assert "10:00" in call_kwargs["content"]
        assert "Giulia" in call_kwargs["content"]

        # Verify the UPDATE query was executed (reminder_sent_at)
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE appointments SET reminder_sent_at" in str(c)
        ]
        assert len(update_calls) == 1
        assert update_calls[0][0][1] == (42,)

    @patch("app.services.reminders.chatwoot_client")
    @patch("app.services.reminders.get_connection")
    async def test_no_appointments_sends_nothing(self, mock_get_conn, mock_chatwoot):
        ctx_fn, _ = _mock_conn_with_appointments([])
        mock_get_conn.side_effect = ctx_fn
        mock_chatwoot.send_message = AsyncMock()

        svc = ReminderService()
        await svc.send_daily_reminders()

        mock_chatwoot.send_message.assert_not_called()

    @patch("app.services.reminders.chatwoot_client")
    @patch("app.services.reminders.get_connection")
    async def test_message_without_operator(self, mock_get_conn, mock_chatwoot):
        appt_no_op = list(SAMPLE_APPOINTMENT)
        appt_no_op[8] = None  # operator_name = None
        ctx_fn, _ = _mock_conn_with_appointments([tuple(appt_no_op)])
        mock_get_conn.side_effect = ctx_fn
        mock_chatwoot.send_message = AsyncMock()

        svc = ReminderService()
        await svc.send_daily_reminders()

        call_kwargs = mock_chatwoot.send_message.call_args[1]
        # The operator line "Con <name>" should not be present
        assert "Giulia" not in call_kwargs["content"]

    @patch("app.services.reminders.chatwoot_client")
    @patch("app.services.reminders.get_connection")
    async def test_chatwoot_error_does_not_crash(self, mock_get_conn, mock_chatwoot):
        ctx_fn, _ = _mock_conn_with_appointments([SAMPLE_APPOINTMENT])
        mock_get_conn.side_effect = ctx_fn
        mock_chatwoot.send_message = AsyncMock(side_effect=Exception("Network error"))

        svc = ReminderService()
        # Should not raise
        await svc.send_daily_reminders()


class TestCheckUnconfirmed:

    async def test_check_unconfirmed_runs_without_error(self):
        svc = ReminderService()
        await svc.check_unconfirmed()
