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


@router.get("/manage/treatments/", response_class=HTMLResponse)
async def list_treatments(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, code, name_it, name_en, duration_minutes, price, is_active, sort_order
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
    code: str = Form(...),
    name_it: str = Form(...),
    name_en: str = Form(""),
    duration_minutes: int = Form(...),
    price: float = Form(0),
    sort_order: int = Form(0),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO treatments (business_id, code, name_it, name_en, duration_minutes, price, sort_order)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user["business_id"], code, name_it, name_en or None, duration_minutes, price, sort_order),
        )

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)


@router.post("/manage/treatments/{treatment_id}/edit")
async def edit_treatment(
    request: Request,
    treatment_id: int,
    code: str = Form(...),
    name_it: str = Form(...),
    name_en: str = Form(""),
    duration_minutes: int = Form(...),
    price: float = Form(0),
    sort_order: int = Form(0),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    # Unchecked checkboxes don't send data, so check form directly
    form_data = await request.form()
    is_active = "is_active" in form_data

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE treatments SET code = %s, name_it = %s, name_en = %s,
                      duration_minutes = %s, price = %s, sort_order = %s, is_active = %s
               WHERE id = %s AND business_id = %s""",
            (code, name_it, name_en or None, duration_minutes, price, sort_order, is_active,
             treatment_id, user["business_id"]),
        )

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)


@router.post("/manage/treatments/{treatment_id}/delete")
async def delete_treatment(request: Request, treatment_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE treatments SET is_active = false WHERE id = %s AND business_id = %s",
            (treatment_id, user["business_id"]),
        )

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/treatments/", status_code=302)
