import os
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from management.auth import decode_token
from app.models.database import get_connection
from app.services.tenant import tenant_service

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/settings/", response_class=HTMLResponse)
async def show_settings(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT name, bot_name, timezone, language, owner_email,
                      address, phone, whatsapp_phone, email, chatwoot_account_id,
                      google_calendar_id, google_service_account_json
               FROM businesses WHERE id = %s""",
            (biz_id,),
        )
        row = cur.fetchone()

    biz = None
    if row:
        biz = {
            "name": row[0], "bot_name": row[1], "timezone": row[2],
            "language": row[3], "owner_email": row[4], "address": row[5],
            "phone": row[6], "whatsapp_phone": row[7], "email": row[8],
            "chatwoot_account_id": row[9],
            "google_calendar_id": row[10], "google_service_account_json": row[11],
        }

    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user, "business": biz,
    })


@router.post("/manage/settings/save")
async def save_settings(
    request: Request,
    bot_name: str = Form("Assistente"),
    timezone: str = Form("Europe/Rome"),
    language: str = Form("it"),
    owner_email: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    whatsapp_phone: str = Form(""),
    email: str = Form(""),
    google_calendar_id: str = Form("primary"),
    google_service_account_json: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses SET bot_name = %s, timezone = %s, language = %s,
                      owner_email = %s, address = %s, phone = %s, whatsapp_phone = %s,
                      email = %s, google_calendar_id = %s, google_service_account_json = %s
               WHERE id = %s""",
            (bot_name, timezone, language, owner_email or None, address or None,
             phone or None, whatsapp_phone or None, email or None,
             google_calendar_id or "primary", google_service_account_json or None,
             biz_id),
        )

        # Get chatwoot_account_id for cache invalidation
        cur.execute("SELECT chatwoot_account_id FROM businesses WHERE id = %s", (biz_id,))
        row = cur.fetchone()
        if row:
            tenant_service.invalidate(row[0])

    return RedirectResponse(url="/manage/settings/", status_code=302)
