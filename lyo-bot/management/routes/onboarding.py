import logging
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from management.auth import decode_token
from app.models.database import get_connection
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


async def _exchange_code(auth_code: str) -> str:
    """Exchange Embedded Signup auth code for a long-lived user access token.

    Embedded Signup with response_type='code' returns an auth CODE (not a token).
    Step 1: code → short-lived user token via /oauth/access_token?code=...
    Step 2: short-lived token → long-lived token via grant_type=fb_exchange_token
    Returns original code on failure (backend will reject it, but at least logs why).
    """
    if not settings.meta_app_id or not settings.meta_app_secret:
        logger.warning("META_APP_ID / META_APP_SECRET not configured — skipping token exchange")
        return auth_code
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: auth code → short-lived user access token
            r1 = await client.get(
                "https://graph.facebook.com/oauth/access_token",
                params={
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "code": auth_code,
                },
            )
            d1 = r1.json()
            user_token = d1.get("access_token")
            if not user_token:
                logger.error("Code→token exchange failed: %s", d1)
                return auth_code

            # Step 2: short-lived user token → long-lived token (60-day)
            r2 = await client.get(
                "https://graph.facebook.com/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "fb_exchange_token": user_token,
                },
            )
            d2 = r2.json()
            long_token = d2.get("access_token")
            if long_token:
                return long_token
            logger.error("Long-lived token exchange failed: %s", d2)
            return user_token  # at minimum save the short-lived token
    except Exception:
        logger.exception("Token exchange error")
    return auth_code


async def _subscribe_waba(waba_id: str, access_token: str) -> None:
    """Subscribe WABA to Lyo's Meta app so we receive webhooks."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code >= 400:
                logger.error("WABA subscription failed %s: %s", resp.status_code, resp.text)
            else:
                logger.info("WABA %s subscribed to Lyo app", waba_id)
    except Exception:
        logger.exception("WABA subscription error for %s", waba_id)


@router.post("/manage/api/whatsapp/callback")
async def whatsapp_callback(request: Request):
    """Receive Embedded Signup result — exchange token, subscribe WABA, save credentials."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    phone_number_id = body.get("phone_number_id")
    waba_id = body.get("waba_id")
    access_token = body.get("access_token")

    if not phone_number_id or not isinstance(phone_number_id, str):
        return JSONResponse({"error": "phone_number_id required"}, status_code=400)
    if not waba_id or not isinstance(waba_id, str):
        return JSONResponse({"error": "waba_id required"}, status_code=400)
    if not access_token or not isinstance(access_token, str):
        return JSONResponse({"error": "access_token required"}, status_code=400)

    # Exchange auth code → long-lived user access token (2-step)
    long_token = await _exchange_code(access_token)

    # Subscribe WABA so Meta sends webhooks to Lyo
    await _subscribe_waba(waba_id, long_token)

    # 60-day long-lived token — record expiry so we can warn before it lapses
    token_expires_at = datetime.now(timezone.utc) + timedelta(days=60)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET whatsapp_phone_number_id = %s,
                   waba_id = %s,
                   meta_access_token = %s,
                   meta_token_expires_at = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (phone_number_id, waba_id, long_token, token_expires_at, user["business_id"]),
        )
        if cur.rowcount == 0:
            return JSONResponse({"error": "business not found"}, status_code=404)

    logger.info("WhatsApp connected for business %s: phone_id=%s", user["business_id"], phone_number_id)
    return JSONResponse({"status": "connected", "phone_number_id": phone_number_id})


@router.get("/manage/api/whatsapp/status")
async def whatsapp_status(request: Request):
    """Check if WhatsApp is connected for this business."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT whatsapp_phone_number_id, meta_token_expires_at FROM businesses WHERE id = %s",
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
        "phone_number_id": row[0] if connected else None,
        "token_expires_at": expires_at,
        "days_until_expiry": days_until_expiry,
    })


@router.post("/manage/api/whatsapp/disconnect")
async def whatsapp_disconnect(request: Request):
    """Clear WhatsApp credentials — tenant must reconnect via Embedded Signup."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET whatsapp_phone_number_id = NULL,
                   waba_id = NULL,
                   meta_access_token = NULL,
                   meta_token_expires_at = NULL,
                   updated_at = NOW()
               WHERE id = %s""",
            (user["business_id"],),
        )

    logger.info("WhatsApp disconnected for business %s", user["business_id"])
    return JSONResponse({"status": "disconnected"})
