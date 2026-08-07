import logging
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from management.auth import decode_token
from app.models.database import get_connection

router = APIRouter()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


async def _subscribe_ig_page_webhooks(ig_page_id: str, page_access_token: str) -> None:
    """Subscribe the Instagram page to Lyo's app webhooks for DMs."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{ig_page_id}/subscribed_apps",
                params={
                    "subscribed_fields": "messages,messaging_postbacks,messaging_optins",
                    "access_token": page_access_token,
                },
            )
            if resp.status_code >= 400:
                logger.error("IG page subscription failed %s: %s", resp.status_code, resp.text)
            else:
                logger.info("IG page %s subscribed to Lyo app webhooks", ig_page_id)
    except Exception:
        logger.exception("IG page subscription error for %s", ig_page_id)


@router.post("/manage/api/instagram/callback")
async def instagram_callback(request: Request):
    """Save Instagram Business Account credentials after FB Login flow.

    Expected body:
      { "instagram_page_id": "<IG Business Account ID>",
        "access_token": "<Page Access Token with instagram_manage_messages>" }
    """
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    ig_page_id = body.get("instagram_page_id")
    access_token = body.get("access_token")

    if not ig_page_id or not isinstance(ig_page_id, str):
        return JSONResponse({"error": "instagram_page_id required"}, status_code=400)
    if not access_token or not isinstance(access_token, str):
        return JSONResponse({"error": "access_token required"}, status_code=400)

    # Subscribe page to webhooks so Meta delivers DMs to /webhook/instagram
    await _subscribe_ig_page_webhooks(ig_page_id, access_token)

    # Page Access Tokens are long-lived but we track 60-day expiry for user tokens
    token_expires_at = datetime.now(timezone.utc) + timedelta(days=60)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET instagram_page_id = %s,
                   instagram_access_token = %s,
                   instagram_token_expires_at = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (ig_page_id, access_token, token_expires_at, user["business_id"]),
        )
        if cur.rowcount == 0:
            return JSONResponse({"error": "business not found"}, status_code=404)

    logger.info("Instagram connected for business %s: page_id=%s", user["business_id"], ig_page_id)
    return JSONResponse({"status": "connected", "instagram_page_id": ig_page_id})


@router.get("/manage/api/instagram/status")
async def instagram_status(request: Request):
    """Check if Instagram is connected for this business."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT instagram_page_id, instagram_token_expires_at FROM businesses WHERE id = %s",
            (user["business_id"],),
        )
        row = cur.fetchone()

    connected = bool(row and row[0])
    expires_at = row[1].isoformat() if (row and row[1]) else None
    days_until_expiry = None
    if row and row[1]:
        delta = row[1].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        days_until_expiry = max(0, delta.days)

    return JSONResponse({
        "connected": connected,
        "instagram_page_id": row[0] if connected else None,
        "token_expires_at": expires_at,
        "days_until_expiry": days_until_expiry,
    })


@router.post("/manage/api/instagram/disconnect")
async def instagram_disconnect(request: Request):
    """Clear Instagram credentials — tenant must reconnect via FB Login."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET instagram_page_id = NULL,
                   instagram_access_token = NULL,
                   instagram_token_expires_at = NULL,
                   updated_at = NOW()
               WHERE id = %s""",
            (user["business_id"],),
        )

    logger.info("Instagram disconnected for business %s", user["business_id"])
    return JSONResponse({"status": "disconnected"})
