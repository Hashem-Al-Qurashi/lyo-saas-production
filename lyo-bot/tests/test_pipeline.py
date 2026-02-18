"""Tests for app.services.pipeline -- mocked services, no DB or API key required."""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from app.models.schemas import (
    WebhookPayload, WebhookAccount, WebhookConversation, WebhookSender,
    Business, Customer, Operator, Treatment, BusinessHours,
)
from app.services.pipeline import MessagePipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_business() -> Business:
    return Business(
        id=1,
        chatwoot_account_id=100,
        name="Test Salon",
        operators=[
            Operator(
                id=1, business_id=1, technical_id="op1",
                display_name="Giulia", treatment_ids=[1],
            ),
        ],
        treatments=[
            Treatment(
                id=1, business_id=1, code="taglio_donna",
                name_it="Taglio Donna", duration_minutes=45,
                operator_ids=[1],
            ),
        ],
        hours=[],
    )


def _make_payload(content="Ciao, vorrei prenotare") -> WebhookPayload:
    return WebhookPayload(
        event="message_created",
        content=content,
        message_type="incoming",
        account=WebhookAccount(id=100),
        conversation=WebhookConversation(id=42),
        sender=WebhookSender(
            id=1, name="Maria", phone_number="+393331234567",
        ),
    )


def _make_customer() -> Customer:
    return Customer(
        id=1, business_id=1, phone="+393331234567",
        first_name="Maria", last_name="Rossi",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMessagePipeline:

    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.ai_service")
    @patch("app.services.pipeline.customer_service")
    @patch("app.services.pipeline.tenant_service")
    async def test_full_pipeline(
        self, mock_tenant, mock_customer, mock_ai, mock_chatwoot
    ):
        """Full flow: tenant found -> customer resolved -> AI response -> message sent."""
        biz = _make_business()
        mock_tenant.get_business.return_value = biz
        mock_customer.get_or_create.return_value = _make_customer()
        mock_ai.process_message = AsyncMock(return_value="Ciao Maria!")
        mock_chatwoot.send_message = AsyncMock(return_value={"id": 1})

        pipe = MessagePipeline()
        payload = _make_payload()

        # Patch settings to use 0 delay for fast tests
        with patch("app.services.pipeline.settings") as mock_settings:
            mock_settings.message_batch_delay_seconds = 0

            # Patch DB operations in _load_conversation_history and _save_conversation
            with patch.object(pipe, "_load_conversation_history", return_value=[]):
                with patch.object(pipe, "_save_conversation"):
                    await pipe.process(payload)

                    # Wait for the timer task to complete
                    for task in list(pipe._timers.values()):
                        await task

        # Verify the chain
        mock_tenant.get_business.assert_called_once_with(100)
        mock_customer.get_or_create.assert_called_once_with(1, "+393331234567")
        mock_ai.process_message.assert_called_once()
        mock_chatwoot.send_message.assert_called_once_with(
            account_id=100,
            conversation_id=42,
            content="Ciao Maria!",
        )

    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.ai_service")
    @patch("app.services.pipeline.tenant_service")
    async def test_unknown_tenant_ignored(
        self, mock_tenant, mock_ai, mock_chatwoot
    ):
        """When tenant_service returns None, no AI or Chatwoot calls should happen."""
        mock_tenant.get_business.return_value = None

        pipe = MessagePipeline()
        payload = _make_payload()

        await pipe.process(payload)

        mock_tenant.get_business.assert_called_once_with(100)
        mock_ai.process_message.assert_not_called()
        mock_chatwoot.send_message.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.ai_service")
    @patch("app.services.pipeline.customer_service")
    @patch("app.services.pipeline.tenant_service")
    async def test_no_phone_ignored(
        self, mock_tenant, mock_customer, mock_ai, mock_chatwoot
    ):
        """When sender has no phone number, processing is skipped."""
        biz = _make_business()
        mock_tenant.get_business.return_value = biz

        pipe = MessagePipeline()
        payload = WebhookPayload(
            event="message_created",
            content="Ciao",
            message_type="incoming",
            account=WebhookAccount(id=100),
            conversation=WebhookConversation(id=42),
            sender=WebhookSender(id=1, name="Unknown", phone_number=None),
        )

        await pipe.process(payload)

        mock_customer.get_or_create.assert_not_called()
        mock_ai.process_message.assert_not_called()
        mock_chatwoot.send_message.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.ai_service")
    @patch("app.services.pipeline.customer_service")
    @patch("app.services.pipeline.tenant_service")
    async def test_message_batching(
        self, mock_tenant, mock_customer, mock_ai, mock_chatwoot
    ):
        """Multiple rapid messages should be batched into one AI call."""
        biz = _make_business()
        mock_tenant.get_business.return_value = biz
        mock_customer.get_or_create.return_value = _make_customer()
        mock_ai.process_message = AsyncMock(return_value="OK!")
        mock_chatwoot.send_message = AsyncMock(return_value={"id": 1})

        pipe = MessagePipeline()

        with patch("app.services.pipeline.settings") as mock_settings:
            mock_settings.message_batch_delay_seconds = 0

            with patch.object(pipe, "_load_conversation_history", return_value=[]):
                with patch.object(pipe, "_save_conversation"):
                    # Send two messages quickly
                    await pipe.process(_make_payload("Ciao"))
                    await pipe.process(_make_payload("Vorrei prenotare"))

                    # Wait for timer
                    for task in list(pipe._timers.values()):
                        await task

        # AI should be called once with combined message
        assert mock_ai.process_message.call_count == 1
        call_args = mock_ai.process_message.call_args
        # The combined message should contain both parts
        assert "Ciao" in call_args.kwargs.get("message", call_args[1].get("message", ""))
        assert "Vorrei prenotare" in call_args.kwargs.get("message", call_args[1].get("message", ""))
