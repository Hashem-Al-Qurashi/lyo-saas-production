import os
import re
import unicodedata
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from management.auth import decode_token
from app.models.database import get_connection
from app.services.tenant import tenant_service


def _slugify(text: str) -> str:
    """Convert display name to a URL/code-safe slug (e.g. 'Taglio Donna' -> 'taglio_donna')."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/treatments/", response_class=HTMLResponse)
async def list_treatments(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, code, name_it, name_en, duration_minutes, price, is_active, sort_order,
                      description_it, description_en, notes, auto_addon_id
               FROM treatments WHERE business_id = %s ORDER BY sort_order""",
            (user["business_id"],),
        )
        treatments_list = cur.fetchall()

    return templates.TemplateResponse("treatments.html", {
        "request": request, "user": user, "treatments": treatments_list,
    })


@router.post("/manage/treatments/add")
async def add_treatment(
    request: Request,
    name_it: str = Form(...),
    name_en: str = Form(""),
    duration_minutes: int = Form(...),
    price: float = Form(0),
    description_it: str = Form(""),
    description_en: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    code = _slugify(name_it)

    with get_connection() as conn:
        cur = conn.cursor()
        # Ensure unique code within business
        base_code = code
        suffix = 0
        while True:
            cur.execute(
                "SELECT COUNT(*) FROM treatments WHERE business_id = %s AND code = %s",
                (user["business_id"], code),
            )
            if cur.fetchone()[0] == 0:
                break
            suffix += 1
            code = f"{base_code}_{suffix}"

        cur.execute(
            """INSERT INTO treatments (business_id, code, name_it, name_en, duration_minutes, price,
                      description_it, description_en)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user["business_id"], code, name_it, name_en or None, duration_minutes, price,
             description_it.strip() or None, description_en.strip() or None),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)


@router.post("/manage/treatments/{treatment_id}/edit")
async def edit_treatment(
    request: Request,
    treatment_id: int,
    name_it: str = Form(...),
    name_en: str = Form(""),
    duration_minutes: int = Form(...),
    price: float = Form(0),
    description_it: str = Form(""),
    description_en: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    # Unchecked checkboxes don't send data, so check form directly
    form_data = await request.form()
    is_active = "is_active" in form_data
    auto_addon_raw = form_data.get("auto_addon_id", "")
    auto_addon_id = int(auto_addon_raw) if auto_addon_raw and auto_addon_raw.strip() else None

    code = _slugify(name_it)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE treatments SET code = %s, name_it = %s, name_en = %s,
                      duration_minutes = %s, price = %s, is_active = %s,
                      description_it = %s, description_en = %s, auto_addon_id = %s
               WHERE id = %s AND business_id = %s""",
            (code, name_it, name_en or None, duration_minutes, price, is_active,
             description_it.strip() or None, description_en.strip() or None,
             auto_addon_id, treatment_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)


@router.post("/manage/treatments/{treatment_id}/delete")
async def delete_treatment(request: Request, treatment_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM treatments WHERE id = %s AND business_id = %s",
            (treatment_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)
