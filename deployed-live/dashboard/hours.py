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

DAY_NAMES = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/hours/", response_class=HTMLResponse)
async def show_hours(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT day_of_week, is_open, open_time::text, close_time::text
               FROM business_hours WHERE business_id = %s ORDER BY day_of_week""",
            (biz_id,),
        )
        hours_rows = cur.fetchall()

        cur.execute(
            """SELECT id, closure_date::text, reason, closure_end_date::text
               FROM business_closures WHERE business_id = %s ORDER BY closure_date""",
            (biz_id,),
        )
        closures = cur.fetchall()

    hours_map = {}
    for row in hours_rows:
        hours_map[row[0]] = {"is_open": row[1], "open_time": row[2], "close_time": row[3]}

    return templates.TemplateResponse("hours.html", {
        "request": request, "user": user,
        "hours_map": hours_map, "day_names": DAY_NAMES, "closures": closures,
    })


@router.post("/manage/hours/save")
async def save_hours(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    form_data = await request.form()

    with get_connection() as conn:
        cur = conn.cursor()
        for day in range(7):
            is_open = form_data.get(f"is_open_{day}") == "on"
            open_time = form_data.get(f"open_time_{day}") or "09:00"
            close_time = form_data.get(f"close_time_{day}") or "18:00"

            cur.execute(
                """INSERT INTO business_hours (business_id, day_of_week, is_open, open_time, close_time)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (business_id, day_of_week)
                   DO UPDATE SET is_open = EXCLUDED.is_open,
                                 open_time = EXCLUDED.open_time,
                                 close_time = EXCLUDED.close_time""",
                (biz_id, day, is_open, open_time, close_time),
            )

    tenant_service.invalidate_by_business_id(biz_id)
    return RedirectResponse(url="/manage/hours/", status_code=302)


@router.post("/manage/hours/closures/add")
async def add_closure(
    request: Request,
    closure_date: str = Form(...),
    closure_end_date: str = Form(""),
    reason: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    end_date = closure_end_date.strip() or None

    with get_connection() as conn:
        cur = conn.cursor()
        # Swap if end < start
        if end_date and end_date < closure_date:
            closure_date, end_date = end_date, closure_date

        cur.execute(
            """INSERT INTO business_closures (business_id, closure_date, closure_end_date, reason)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (business_id, closure_date) DO UPDATE
               SET closure_end_date = EXCLUDED.closure_end_date, reason = EXCLUDED.reason""",
            (user["business_id"], closure_date, end_date, reason or None),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/hours/", status_code=302)


@router.post("/manage/hours/closures/{closure_id}/delete")
async def delete_closure(request: Request, closure_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM business_closures WHERE id = %s AND business_id = %s",
            (closure_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/hours/", status_code=302)
