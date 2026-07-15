import asyncio
import logging
from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import settings
from app.models.schemas import WebhookPayload
from app.services.pipeline import pipeline
from app.services.reminders import reminder_service
from app.services.tenant import tenant_service
from app.services import whatsapp as whatsapp_service

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


@app.on_event("startup")
async def startup():
    scheduler = AsyncIOScheduler(timezone="Europe/Rome")
    scheduler.add_job(reminder_service.send_daily_reminders, CronTrigger(hour=10, minute=0))
    scheduler.add_job(reminder_service.check_unconfirmed, CronTrigger(hour=18, minute=0))
    scheduler.start()
    logger.info("Scheduler started")


@app.get("/health")
async def health():
    db_ok = False
    try:
        from app.models.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = cur.fetchone() is not None
    except Exception:
        logger.warning("Health check: DB connection failed")

    status = "ok" if db_ok else "degraded"
    return {"status": status, "version": "2.0.0", "db": "connected" if db_ok else "unavailable"}


@app.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """Meta webhook verification challenge."""
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        return PlainTextResponse(hub_challenge or "")
    return JSONResponse({"error": "forbidden"}, status_code=403)


@app.post("/webhook/whatsapp")
async def whatsapp_direct_webhook(request: Request):
    """Receive WhatsApp Cloud API messages directly from Meta."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    asyncio.create_task(_process_whatsapp_message(raw))
    return {"status": "received"}


async def _process_whatsapp_message(raw: dict):
    """Parse Meta Cloud API payload and run the AI pipeline."""
    try:
        entries = raw.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                if not messages:
                    continue

                msg = messages[0]
                if msg.get("type") != "text":
                    continue

                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                sender_phone = msg.get("from")
                text = msg.get("text", {}).get("body", "").strip()

                if not phone_number_id or not sender_phone or not text:
                    continue

                business = tenant_service.get_business_by_phone_id(phone_number_id)
                if not business:
                    logger.warning("No business for phone_number_id %s", phone_number_id)
                    continue
                if not business.meta_access_token:
                    logger.error("Business %s has no meta_access_token", business.id)
                    continue

                reply = await pipeline.process_direct(business, sender_phone, text)
                if reply:
                    await whatsapp_service.send_message(
                        phone_number_id, business.meta_access_token, sender_phone, reply
                    )
    except Exception:
        logger.exception("Error processing direct WhatsApp message")


@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Receive webhook from Chatwoot Agent Bot system.
    Returns 200 immediately (Chatwoot has 5s timeout).
    Processes message in background."""
    # Validate webhook secret if configured
    if settings.webhook_secret:
        token = request.headers.get("X-Chatwoot-Webhook-Token", "")
        if token != settings.webhook_secret:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

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
    """Background task: resolve tenant, process AI, send reply."""
    try:
        await pipeline.process(payload)
    except Exception as e:
        logger.error(f"Error processing message: {e}")
