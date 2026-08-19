"""Message pipeline: batching, conversation history, AI processing, reply.

Buffers rapid-fire messages from the same sender (WhatsApp voice notes,
multi-part texts) and processes them as a single concatenated message
after a configurable delay.
"""

import asyncio
import json
import logging
from typing import Optional

from app.config import settings
from app.models.database import get_connection
from app.models.schemas import WebhookPayload
from app.services.tenant import tenant_service
from app.services.customer import customer_service
from app.services.ai import ai_service
from app.services.chatwoot import chatwoot_client

logger = logging.getLogger(__name__)


class MessagePipeline:
    """Batches incoming messages per sender, then processes via AI."""

    def __init__(self):
        self._buffers: dict[str, list[dict]] = {}
        self._timers: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def process_direct(self, business, phone: str, text: str) -> Optional[str]:
        """Process a message from the direct WhatsApp Cloud API path. Returns reply text."""
        try:
            customer = await asyncio.to_thread(
                customer_service.get_or_create, business.id, phone,
            )
            history = await asyncio.to_thread(
                self._load_conversation_history, business.id, phone,
            )
            reply = await ai_service.process_message(
                business=business,
                customer_phone=phone,
                message=text,
                conversation_history=history,
                customer_name=customer.full_name,
                conversation_id=None,
                account_id=None,
            )
            await asyncio.to_thread(
                self._save_conversation, business.id, phone, None, text, reply, history,
            )
            return reply
        except Exception:
            logger.exception("Error in process_direct for business %s phone %s", business.id, phone)
            return None

    async def process(self, payload: WebhookPayload) -> None:
        """Handle an incoming webhook payload."""
        if not payload.account:
            logger.warning("Payload missing account, ignoring")
            return

        business = tenant_service.get_business(payload.account.id)
        if not business:
            logger.warning("No business for account %s", payload.account.id)
            return

        phone = payload.sender.phone_number if payload.sender else None
        if not phone:
            logger.warning("No phone number in payload for account %s", payload.account.id)
            return

        if payload.conversation and "bot_paused" in (payload.conversation.labels or []):
            logger.info("Bot paused for conversation %s, skipping", payload.conversation.id)
            return

        sender_name = payload.sender.name if payload.sender else None
        conversation_id = payload.conversation.id if payload.conversation else None
        content = payload.content or ""

        key = f"{business.id}:{phone}"
        await self._buffer_message(
            key=key,
            content=content,
            business=business,
            phone=phone,
            sender_name=sender_name,
            account_id=payload.account.id,
            conversation_id=conversation_id,
        )

    # ------------------------------------------------------------------
    # Buffering
    # ------------------------------------------------------------------

    async def _buffer_message(
        self,
        key: str,
        content: str,
        business,
        phone: str,
        sender_name: Optional[str],
        account_id: int,
        conversation_id: Optional[int],
    ) -> None:
        """Add message to buffer and (re)start the flush timer."""
        if key not in self._buffers:
            self._buffers[key] = []
        self._buffers[key].append({
            "content": content,
            "sender_name": sender_name,
        })

        # Cancel existing timer
        existing = self._timers.get(key)
        if existing and not existing.done():
            existing.cancel()

        # Start new timer
        self._timers[key] = asyncio.create_task(
            self._delayed_process(
                key=key,
                business=business,
                phone=phone,
                account_id=account_id,
                conversation_id=conversation_id,
            )
        )

    async def _delayed_process(
        self,
        key: str,
        business,
        phone: str,
        account_id: int,
        conversation_id: Optional[int],
    ) -> None:
        """Wait for the batch delay, then process buffered messages."""
        await asyncio.sleep(settings.message_batch_delay_seconds)
        await self._process_buffered(
            key=key,
            business=business,
            phone=phone,
            account_id=account_id,
            conversation_id=conversation_id,
        )

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def _process_buffered(
        self,
        key: str,
        business,
        phone: str,
        account_id: int,
        conversation_id: Optional[int],
    ) -> None:
        """Combine buffered messages, run AI, send reply."""
        msgs = self._buffers.pop(key, [])
        self._timers.pop(key, None)
        if not msgs:
            return

        # Combine all buffered content
        combined = "\n".join(m["content"] for m in msgs if m["content"])
        if not combined.strip():
            return

        # Sender name from first message that has one
        sender_name = None
        for m in msgs:
            if m.get("sender_name"):
                sender_name = m["sender_name"]
                break

        try:
            # Get or create customer
            customer = await asyncio.to_thread(
                customer_service.get_or_create,
                business.id, phone,
            )
            customer_name = customer.full_name

            # Load conversation history
            history = await asyncio.to_thread(
                self._load_conversation_history, business.id, phone,
            )

            # AI processing
            reply = await ai_service.process_message(
                business=business,
                customer_phone=phone,
                message=combined,
                conversation_history=history,
                customer_name=customer_name,
                conversation_id=conversation_id,
                account_id=account_id,
            )

            # Save conversation
            await asyncio.to_thread(
                self._save_conversation,
                business.id, phone, conversation_id,
                combined, reply, history,
            )

            # Send reply via Chatwoot
            if conversation_id is not None:
                await chatwoot_client.send_message(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    content=reply,
                )
            else:
                logger.warning("No conversation_id, cannot send reply for %s", key)

        except Exception:
            logger.exception("Error processing buffered messages for %s", key)

    # ------------------------------------------------------------------
    # Conversation persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _load_conversation_history(
        business_id: int, phone: str
    ) -> list[dict]:
        """Load the last 20 messages from the conversations table."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT messages FROM conversations
                        WHERE business_id = %s AND customer_phone = %s
                        """,
                        (business_id, phone),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        messages = row[0] if isinstance(row[0], list) else json.loads(row[0])
                        # Return last 20
                        return messages[-20:]
                    return []
        except Exception:
            logger.exception("Failed to load conversation history")
            return []

    @staticmethod
    def _save_conversation(
        business_id: int,
        phone: str,
        conversation_id: Optional[int],
        user_message: str,
        bot_response: str,
        history: list[dict],
    ) -> None:
        """Upsert conversation with the new messages appended."""
        updated = list(history)
        updated.append({"role": "user", "content": user_message})
        updated.append({"role": "assistant", "content": bot_response})
        # Keep last 20
        updated = updated[-20:]

        messages_json = json.dumps(updated)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO conversations
                            (business_id, customer_phone, chatwoot_conversation_id, messages, updated_at)
                        VALUES (%s, %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (business_id, customer_phone)
                        DO UPDATE SET
                            messages = %s::jsonb,
                            chatwoot_conversation_id = COALESCE(EXCLUDED.chatwoot_conversation_id, conversations.chatwoot_conversation_id),
                            updated_at = NOW()
                        """,
                        (business_id, phone, conversation_id, messages_json, messages_json),
                    )
        except Exception:
            logger.exception("Failed to save conversation")


# Singleton
pipeline = MessagePipeline()
