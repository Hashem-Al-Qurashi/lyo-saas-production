import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import WebhookPayload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lyo Bot", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Receive webhook from Chatwoot Agent Bot system.
    Returns 200 immediately (Chatwoot has 5s timeout).
    Processes message in background."""
    try:
        raw = await request.json()
        payload = WebhookPayload(**raw)

        # Only process incoming message_created events with content
        if not payload.is_message_created() or not payload.is_incoming():
            return {"status": "ignored", "reason": "not incoming message"}

        if not payload.content or not payload.content.strip():
            return {"status": "ignored", "reason": "empty content"}

        if not payload.account or not payload.conversation:
            return {"status": "ignored", "reason": "missing account or conversation"}

        logger.info(
            f"Webhook received: account={payload.account.id} "
            f"conv={payload.conversation.id} "
            f"from={payload.sender.phone_number if payload.sender else 'unknown'} "
            f"content={payload.content[:50]}..."
        )

        # Process in background (don't block the 200 response)
        asyncio.create_task(_process_message(payload))

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}


async def _process_message(payload: WebhookPayload):
    """Background task: resolve tenant, process AI, send reply.
    Wired up in Task 12 (pipeline.py)."""
    try:
        logger.info(f"Processing message for account {payload.account.id}: {payload.content[:50]}")
    except Exception as e:
        logger.error(f"Error processing message: {e}")
