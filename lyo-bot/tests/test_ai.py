"""Tests for app.services.ai -- mocked OpenAI, no DB or API key required."""

import json
from datetime import time
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from app.models.schemas import Business, Operator, Treatment, BusinessHours
from app.services.ai import AIService, build_system_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_business() -> Business:
    return Business(
        id=1,
        chatwoot_account_id=100,
        name="Test Salon",
        bot_name="Simone",
        address="Via Roma 1, Milano",
        phone="+390000000",
        language="it",
        operators=[
            Operator(
                id=1, business_id=1, technical_id="op1",
                display_name="Giulia", treatment_ids=[1],
            ),
        ],
        treatments=[
            Treatment(
                id=1, business_id=1, code="taglio_donna",
                name_it="Taglio Donna", duration_minutes=45, price=60,
                operator_ids=[1],
            ),
        ],
        hours=[
            BusinessHours(
                business_id=1, day_of_week=i, is_open=(i not in (0, 6)),
                open_time=time(9, 0) if i not in (0, 6) else None,
                close_time=time(18, 0) if i not in (0, 6) else None,
            )
            for i in range(7)
        ],
    )


def _mock_openai_response(content="Ciao! Come posso aiutarti?", tool_calls=None):
    """Build a mock OpenAI chat completion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []
    message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": [],
    }

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop" if not tool_calls else "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_openai_tool_call_response(tool_call_id, function_name, arguments):
    """Build a mock response that includes a tool call."""
    tool_call = MagicMock()
    tool_call.id = tool_call_id
    tool_call.function.name = function_name
    tool_call.function.arguments = json.dumps(arguments)

    message = MagicMock()
    message.content = None
    message.tool_calls = [tool_call]
    message.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {"name": function_name, "arguments": json.dumps(arguments)},
        }],
    }

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:

    def test_prompt_includes_bot_name(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Simone" in prompt

    def test_prompt_includes_treatments(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Taglio Donna" in prompt
        assert "taglio_donna" in prompt

    def test_prompt_includes_operators(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Giulia" in prompt

    def test_prompt_includes_address(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Via Roma 1, Milano" in prompt

    def test_prompt_includes_business_hours(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "09:00" in prompt
        assert "CLOSED" in prompt

    def test_prompt_includes_italian_language_rule(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "ITALIAN" in prompt


# ---------------------------------------------------------------------------
# AI Service tests (mocked OpenAI)
# ---------------------------------------------------------------------------

class TestAIService:

    @pytest.mark.asyncio
    @patch("app.services.ai.openai_client")
    async def test_process_returns_response(self, mock_client):
        """process_message returns the model's text when no tool calls."""
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            content="Ciao! Benvenuto!"
        )

        svc = AIService()
        biz = _make_business()
        result = await svc.process_message(
            business=biz,
            customer_phone="+393331234567",
            message="Ciao",
            conversation_history=[],
        )

        assert isinstance(result, str)
        assert "Ciao" in result or "Benvenuto" in result

    @pytest.mark.asyncio
    @patch("app.services.ai.openai_client")
    async def test_handles_tool_call(self, mock_client):
        """When the model issues a tool call, it should be dispatched and
        the final text response returned."""
        # First call: model requests get_customer_appointments
        tool_response = _mock_openai_tool_call_response(
            tool_call_id="call_123",
            function_name="get_customer_appointments",
            arguments={},
        )
        # Second call: model returns final text
        final_response = _mock_openai_response(
            content="Non hai appuntamenti."
        )
        mock_client.chat.completions.create.side_effect = [
            tool_response, final_response,
        ]

        svc = AIService()
        biz = _make_business()

        # Mock the booking service so it doesn't hit DB
        with patch("app.services.ai.booking_service") as mock_booking:
            mock_booking.get_customer_appointments.return_value = []

            result = await svc.process_message(
                business=biz,
                customer_phone="+393331234567",
                message="I miei appuntamenti",
                conversation_history=[],
            )

        assert "appuntamenti" in result.lower() or "Non hai" in result
        mock_booking.get_customer_appointments.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.ai.openai_client")
    async def test_max_rounds_prevents_infinite_loop(self, mock_client):
        """If the model keeps issuing tool calls, we stop after MAX_TOOL_ROUNDS."""
        # Always return a tool call
        tool_response = _mock_openai_tool_call_response(
            tool_call_id="call_loop",
            function_name="get_customer_appointments",
            arguments={},
        )
        mock_client.chat.completions.create.return_value = tool_response

        svc = AIService()
        svc.MAX_TOOL_ROUNDS = 2  # lower for faster test
        biz = _make_business()

        with patch("app.services.ai.booking_service") as mock_booking:
            mock_booking.get_customer_appointments.return_value = []

            result = await svc.process_message(
                business=biz,
                customer_phone="+393331234567",
                message="loop test",
                conversation_history=[],
            )

        # Should return something (fallback) rather than hang
        assert isinstance(result, str)
        # Should have been called MAX_TOOL_ROUNDS times
        assert mock_client.chat.completions.create.call_count == 2
