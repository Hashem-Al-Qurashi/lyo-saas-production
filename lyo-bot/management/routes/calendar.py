import os
from datetime import date, datetime

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
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


@router.get("/manage/api/operators")
async def api_operators(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, display_name, is_active
               FROM operators
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (user["business_id"],),
        )
        rows = cur.fetchall()

    resources = [{"id": r[0], "title": r[1]} for r in rows]
    return JSONResponse(resources)


STATUS_COLORS = {
    "confirmed": "#16a34a",   # green-600
    "completed": "#2563eb",   # blue-600
    "cancelled": "#dc2626",   # red-600
}


@router.get("/manage/api/appointments")
async def api_appointments(
    request: Request,
    start: str = Query(default=None),
    end: str = Query(default=None),
):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not start or not end:
        return JSONResponse({"error": "start and end required"}, status_code=400)

    try:
        start_date = date.fromisoformat(start[:10])
        end_date = date.fromisoformat(end[:10])
    except ValueError:
        return JSONResponse({"error": "invalid date format"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, customer_name, customer_phone, treatment_name, treatment_code,
                      operator_name, appointment_date::text, appointment_time::text,
                      duration_minutes, status, price, operator_id
               FROM appointments
               WHERE business_id = %s
                 AND appointment_date >= %s AND appointment_date < %s
               ORDER BY appointment_date, appointment_time""",
            (user["business_id"], start_date, end_date),
        )
        rows = cur.fetchall()

    events = []
    for r in rows:
        appt_id, name, phone, treat_name, treat_code, op_name, appt_date, appt_time, duration, status, price, op_id = r
        start_dt = f"{appt_date}T{appt_time}"
        # Calculate end time
        h, m, s = appt_time.split(":")
        total_min = int(h) * 60 + int(m) + duration
        end_h, end_m = divmod(total_min, 60)
        end_dt = f"{appt_date}T{end_h:02d}:{end_m:02d}:00"

        events.append({
            "id": appt_id,
            "title": f"{name}\n{treat_name or treat_code}",
            "start": start_dt,
            "end": end_dt,
            "resourceId": op_id,
            "color": STATUS_COLORS.get(status, "#6b7280"),
            "extendedProps": {
                "phone": phone,
                "status": status,
                "price": float(price) if price else 0,
                "operator": op_name,
            },
        })

    return JSONResponse(events)


@router.post("/manage/api/appointments/{appointment_id}/move")
async def api_move_appointment(request: Request, appointment_id: int):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    new_date = body.get("new_date")
    new_time = body.get("new_time")
    new_operator_id = body.get("new_operator_id")

    if not new_date or not new_time:
        return JSONResponse({"error": "new_date and new_time required"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        # Get operator name for denormalized column
        op_name = None
        if new_operator_id:
            cur.execute(
                "SELECT display_name FROM operators WHERE id = %s AND business_id = %s",
                (new_operator_id, user["business_id"]),
            )
            row = cur.fetchone()
            op_name = row[0] if row else None

        cur.execute(
            """UPDATE appointments
               SET appointment_date = %s, appointment_time = %s,
                   operator_id = COALESCE(%s, operator_id),
                   operator_name = COALESCE(%s, operator_name),
                   updated_at = NOW()
               WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
            (new_date, new_time, new_operator_id, op_name, appointment_id, user["business_id"]),
        )

        if cur.rowcount == 0:
            return JSONResponse({"error": "appointment not found or not confirmed"}, status_code=404)

    return JSONResponse({"status": "moved"})
