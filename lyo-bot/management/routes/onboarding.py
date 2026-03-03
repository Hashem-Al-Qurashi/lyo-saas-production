from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from management.auth import decode_token
from app.models.database import get_connection

router = APIRouter()


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.post("/manage/api/whatsapp/callback")
async def whatsapp_callback(request: Request):
    """Receive Embedded Signup result -- save WhatsApp credentials to business."""
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

    if not phone_number_id:
        return JSONResponse({"error": "phone_number_id required"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET whatsapp_phone_number_id = %s,
                   waba_id = %s,
                   meta_access_token = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (phone_number_id, waba_id, access_token, user["business_id"]),
        )

        if cur.rowcount == 0:
            return JSONResponse({"error": "business not found"}, status_code=404)

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
            "SELECT whatsapp_phone_number_id FROM businesses WHERE id = %s",
            (user["business_id"],),
        )
        row = cur.fetchone()

    connected = bool(row and row[0])
    return JSONResponse({"connected": connected, "phone_number_id": row[0] if connected else None})
