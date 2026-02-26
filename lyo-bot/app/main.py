import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import settings
from app.models.schemas import WebhookPayload
from app.services.pipeline import pipeline
from app.services.reminders import reminder_service

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
