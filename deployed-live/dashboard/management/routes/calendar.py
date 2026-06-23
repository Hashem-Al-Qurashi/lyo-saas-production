import os
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


@router.get("/manage/calendar/", response_class=HTMLResponse)
async def calendar_page(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "user": user,
    })


@router.get("/manage/api/operators")
async def api_operators(request: Request, date: str = Query(default=None)):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Parse optional date to filter by working day
    filter_dow = None
    if date:
        try:
            from datetime import date as date_cls
            parsed = date_cls.fromisoformat(date[:10])
            filter_dow = parsed.weekday()  # 0=Monday, 6=Sunday
        except ValueError:
            pass

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, display_name
               FROM operators
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (user["business_id"],),
        )
        rows = cur.fetchall()

        # If filtering by day, exclude operators who don't work that day
        if filter_dow is not None and rows:
            op_ids = [r[0] for r in rows]
            cur.execute(
                """SELECT operator_id, is_working
                   FROM operator_hours
                   WHERE operator_id = ANY(%s) AND day_of_week = %s""",
                (op_ids, filter_dow),
            )
            not_working = {r[0] for r in cur.fetchall() if not r[1]}
            rows = [r for r in rows if r[0] not in not_working]

    # Title-case names to fix inconsistent DB casing (e.g. "federica" → "Federica")
    resources = [{"id": r[0], "title": r[1].title() if r[1] else ""} for r in rows]
    resources.append({"id": "unassigned", "title": "Non assegnato"})
    return JSONResponse(resources)


@router.get("/manage/api/treatments")
async def api_treatments(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name_it, duration_minutes, price
               FROM treatments
               WHERE business_id = %s AND is_active = true
               ORDER BY name_it""",
            (user["business_id"],),
        )
        rows = cur.fetchall()

    return JSONResponse([
        {"id": r[0], "name": r[1], "duration": r[2], "price": float(r[3]) if r[3] else 0}
        for r in rows
    ])


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
                 AND status != 'cancelled'
               ORDER BY appointment_date, appointment_time""",
            (user["business_id"], start_date, end_date),
        )
        rows = cur.fetchall()

    events = []
    for r in rows:
        appt_id, name, phone, treat_name, treat_code, op_name, appt_date, appt_time, duration, status, price, op_id = r
        start_dt = f"{appt_date}T{appt_time}"
        # Calculate end time with midnight-crossing guard
        start_obj = datetime.strptime(f"{appt_date}T{appt_time}", "%Y-%m-%dT%H:%M:%S")
        end_obj = start_obj + timedelta(minutes=duration)
        end_dt = end_obj.strftime("%Y-%m-%dT%H:%M:%S")

        events.append({
            "id": appt_id,
            "title": f"{name}\n{treat_name or treat_code}",
            "start": start_dt,
            "end": end_dt,
            "resourceId": op_id if op_id else "unassigned",
            "color": STATUS_COLORS.get(status, "#6b7280"),
            "extendedProps": {
                "phone": phone,
                "status": status,
                "price": float(price) if price else 0,
                "operator": op_name.title() if op_name else None,
                "treatmentName": treat_name or treat_code or "",
                "customerName": name,
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
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    new_date = body.get("new_date")
    new_time = body.get("new_time")
    # Use sentinel key: "new_operator_id" present in body (even null) means intentional update
    operator_key_present = "new_operator_id" in body
    new_operator_id = body.get("new_operator_id")

    if not new_date or not new_time:
        return JSONResponse({"error": "new_date and new_time required"}, status_code=400)

    try:
        date.fromisoformat(str(new_date)[:10])
    except ValueError:
        return JSONResponse({"error": "invalid date format"}, status_code=400)

    if not re.match(r"^\d{2}:\d{2}(:\d{2})?$", str(new_time)):
        return JSONResponse({"error": "invalid time format"}, status_code=400)

    if new_operator_id is not None and not isinstance(new_operator_id, int):
        return JSONResponse({"error": "invalid operator_id"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()

        if operator_key_present:
            # Explicit operator update (including clearing to NULL when dragged to "unassigned")
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
                       operator_id = %s,
                       operator_name = %s,
                       updated_at = NOW()
                   WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
                (new_date, new_time, new_operator_id, op_name, appointment_id, user["business_id"]),
            )
        else:
            # Time-only move — don't touch operator
            cur.execute(
                """UPDATE appointments
                   SET appointment_date = %s, appointment_time = %s,
                       updated_at = NOW()
                   WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
                (new_date, new_time, appointment_id, user["business_id"]),
            )

        if cur.rowcount == 0:
            return JSONResponse({"error": "appointment not found or not confirmed"}, status_code=404)

    return JSONResponse({"status": "moved"})


@router.post("/manage/api/appointments")
async def api_create_appointment(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    customer_name = (body.get("customer_name") or "").strip()
    customer_phone = (body.get("customer_phone") or "").strip()
    treatment_id = body.get("treatment_id")
    operator_id = body.get("operator_id")
    appt_date = body.get("appointment_date")
    appt_time = body.get("appointment_time")

    if not customer_name:
        return JSONResponse({"error": "customer_name required"}, status_code=400)
    if not customer_phone:
        return JSONResponse({"error": "customer_phone required"}, status_code=400)
    if not treatment_id or not isinstance(treatment_id, int):
        return JSONResponse({"error": "treatment_id required"}, status_code=400)
    if not appt_date or not appt_time:
        return JSONResponse({"error": "appointment_date and appointment_time required"}, status_code=400)

    try:
        date.fromisoformat(str(appt_date)[:10])
    except ValueError:
        return JSONResponse({"error": "invalid appointment_date"}, status_code=400)

    if not re.match(r"^\d{2}:\d{2}(:\d{2})?$", str(appt_time)):
        return JSONResponse({"error": "invalid appointment_time"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()

        # Fetch treatment
        cur.execute(
            "SELECT name_it, code, duration_minutes, price FROM treatments WHERE id = %s AND business_id = %s AND is_active = true",
            (treatment_id, user["business_id"]),
        )
        treatment = cur.fetchone()
        if not treatment:
            return JSONResponse({"error": "treatment not found"}, status_code=404)
        treat_name, treat_code, duration, price = treatment

        # Fetch operator (optional)
        op_name = None
        if operator_id and isinstance(operator_id, int):
            cur.execute(
                "SELECT display_name FROM operators WHERE id = %s AND business_id = %s AND is_active = true",
                (operator_id, user["business_id"]),
            )
            op_row = cur.fetchone()
            op_name = op_row[0] if op_row else None
            if not op_row:
                operator_id = None

        # Double-booking guard: reject if operator already has a confirmed appointment
        # overlapping this time window (only applies when operator is assigned)
        if operator_id:
            cur.execute(
                """SELECT id FROM appointments
                   WHERE business_id = %s
                     AND operator_id = %s
                     AND appointment_date = %s
                     AND status = 'confirmed'
                     AND appointment_time < %s::time + (%s * interval '1 minute')
                     AND appointment_time + (duration_minutes * interval '1 minute') > %s::time
                   LIMIT 1""",
                (user["business_id"], operator_id, appt_date, appt_time, duration, appt_time),
            )
            if cur.fetchone():
                return JSONResponse(
                    {"error": "L'operatore ha già un appuntamento in questo orario"},
                    status_code=409,
                )

        cur.execute(
            """INSERT INTO appointments
               (business_id, customer_name, customer_phone, treatment_name, treatment_code,
                operator_name, operator_id, appointment_date, appointment_time,
                duration_minutes, price, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed')
               RETURNING id""",
            (user["business_id"], customer_name, customer_phone, treat_name, treat_code,
             op_name, operator_id, appt_date, appt_time, duration, price),
        )
        new_id = cur.fetchone()[0]

    return JSONResponse({"status": "created", "id": new_id}, status_code=201)
