import os
from datetime import date

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from management.auth import decode_token
from app.models.database import get_connection

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/appointments/", response_class=HTMLResponse)
async def list_appointments(
    request: Request,
    filter_date: str = Query(default=None),
    status: str = Query(default="all"),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]

    # Default to today
    if filter_date:
        try:
            selected_date = date.fromisoformat(filter_date)
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    with get_connection() as conn:
        cur = conn.cursor()

        query = """
            SELECT id, customer_name, customer_phone, treatment_name, treatment_code,
                   operator_name, appointment_date::text, TO_CHAR(appointment_time, 'HH24:MI'),
                   duration_minutes, status, price
            FROM appointments
            WHERE business_id = %s AND appointment_date = %s
        """
        params = [biz_id, selected_date]

        if status != "all":
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY appointment_time"
        cur.execute(query, params)
        appointments = cur.fetchall()

        # Get counts for the selected date
        cur.execute(
            """SELECT
                   COUNT(*) FILTER (WHERE status = 'confirmed') as confirmed,
                   COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled,
                   COUNT(*) FILTER (WHERE status = 'completed') as completed,
                   COUNT(*) as total
               FROM appointments
               WHERE business_id = %s AND appointment_date = %s""",
            (biz_id, selected_date),
        )
        counts = cur.fetchone()

    return templates.TemplateResponse("appointments.html", {
        "request": request,
        "user": user,
        "appointments": appointments,
        "selected_date": selected_date.isoformat(),
        "selected_status": status,
        "counts": {
            "confirmed": counts[0],
            "cancelled": counts[1],
            "completed": counts[2],
            "total": counts[3],
        },
    })


@router.post("/manage/appointments/{appointment_id}/cancel")
async def cancel_appointment(request: Request, appointment_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE appointments SET status = 'cancelled', updated_at = NOW()
               WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
            (appointment_id, user["business_id"]),
        )
        cur.execute(
            """UPDATE appointments SET status = 'cancelled', updated_at = NOW()
               WHERE parent_appointment_id = %s AND business_id = %s AND status = 'confirmed'""",
            (appointment_id, user["business_id"]),
        )

    return RedirectResponse(url="/manage/appointments/", status_code=302)


@router.post("/manage/appointments/{appointment_id}/complete")
async def complete_appointment(request: Request, appointment_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE appointments SET status = 'completed', updated_at = NOW()
               WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
            (appointment_id, user["business_id"]),
        )

    return RedirectResponse(url="/manage/appointments/", status_code=302)
