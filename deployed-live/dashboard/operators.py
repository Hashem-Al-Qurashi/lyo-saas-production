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
    """Convert display name to a URL/code-safe slug (e.g. 'Antonio Rossi' -> 'antonio_rossi')."""
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


@router.get("/manage/operators/", response_class=HTMLResponse)
async def list_operators(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, technical_id, display_name, is_active, sort_order, notes
               FROM operators WHERE business_id = %s ORDER BY sort_order""",
            (biz_id,),
        )
        operators_list = cur.fetchall()

        cur.execute(
            "SELECT id, code, name_it FROM treatments WHERE business_id = %s AND is_active = true ORDER BY sort_order",
            (biz_id,),
        )
        treatments_list = cur.fetchall()

        cur.execute(
            """SELECT operator_id, treatment_id FROM operator_treatments
               WHERE operator_id IN (SELECT id FROM operators WHERE business_id = %s)""",
            (biz_id,),
        )
        op_treat_links = cur.fetchall()

        cur.execute(
            """SELECT operator_id, day_of_week, is_working,
                      TO_CHAR(start_time, 'HH24:MI'), TO_CHAR(end_time, 'HH24:MI'),
                      TO_CHAR(break_start, 'HH24:MI'), TO_CHAR(break_end, 'HH24:MI')
               FROM operator_hours
               WHERE operator_id IN (SELECT id FROM operators WHERE business_id = %s)
               ORDER BY operator_id, day_of_week""",
            (biz_id,),
        )
        hours_rows = cur.fetchall()

    op_treatments = {}
    for op_id, treat_id in op_treat_links:
        op_treatments.setdefault(op_id, []).append(treat_id)

    op_hours = {}
    for row in hours_rows:
        op_id, dow, is_working, start, end = row[0], row[1], row[2], row[3], row[4]
        break_s = row[5] if len(row) > 5 else None
        break_e = row[6] if len(row) > 6 else None
        op_hours.setdefault(op_id, {})[dow] = (is_working, start, end, break_s, break_e)

    return templates.TemplateResponse("operators.html", {
        "request": request, "user": user, "operators": operators_list,
        "treatments": treatments_list, "op_treatments": op_treatments,
        "op_hours": op_hours,
    })


@router.post("/manage/operators/add")
async def add_operator(
    request: Request,
    display_name: str = Form(...),
    notes: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    technical_id = _slugify(display_name)

    with get_connection() as conn:
        cur = conn.cursor()
        # Ensure unique technical_id within business
        base_id = technical_id
        suffix = 0
        while True:
            cur.execute(
                "SELECT COUNT(*) FROM operators WHERE business_id = %s AND technical_id = %s",
                (user["business_id"], technical_id),
            )
            if cur.fetchone()[0] == 0:
                break
            suffix += 1
            technical_id = f"{base_id}_{suffix}"

        cur.execute(
            """INSERT INTO operators (business_id, technical_id, display_name, notes)
               VALUES (%s, %s, %s, %s)""",
            (user["business_id"], technical_id, display_name, notes.strip() or None),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/edit")
async def edit_operator(
    request: Request,
    operator_id: int,
    display_name: str = Form(...),
    notes: str = Form(""),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    # Unchecked checkboxes don't send data, so check form directly
    form_data = await request.form()
    is_active = "is_active" in form_data

    technical_id = _slugify(display_name)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE operators SET technical_id = %s, display_name = %s,
                      is_active = %s, notes = %s
               WHERE id = %s AND business_id = %s""",
            (technical_id, display_name, is_active, notes.strip() or None,
             operator_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/delete")
async def delete_operator(request: Request, operator_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM operators WHERE id = %s AND business_id = %s",
            (operator_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/hours")
async def update_operator_hours(request: Request, operator_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    form_data = await request.form()

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM operators WHERE id = %s AND business_id = %s",
            (operator_id, user["business_id"]),
        )
        if not cur.fetchone():
            return RedirectResponse(url="/manage/operators/", status_code=302)

        for dow in range(7):
            is_working = f"working_{dow}" in form_data
            start_time = form_data.get(f"start_{dow}") or None
            end_time = form_data.get(f"end_{dow}") or None
            break_start = form_data.get(f"break_start_{dow}") or None
            break_end = form_data.get(f"break_end_{dow}") or None
            cur.execute(
                """INSERT INTO operator_hours (operator_id, day_of_week, is_working, start_time, end_time, break_start, break_end)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (operator_id, day_of_week) DO UPDATE
                   SET is_working = EXCLUDED.is_working,
                       start_time = EXCLUDED.start_time,
                       end_time = EXCLUDED.end_time,
                       break_start = EXCLUDED.break_start,
                       break_end = EXCLUDED.break_end""",
                (operator_id, dow, is_working, start_time, end_time, break_start, break_end),
            )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/treatments")
async def update_operator_treatments(request: Request, operator_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    form_data = await request.form()
    treatment_ids = [int(v) for k, v in form_data.multi_items() if k == "treatment_ids"]

    with get_connection() as conn:
        cur = conn.cursor()
        # Verify the operator belongs to this business
        cur.execute(
            "SELECT id FROM operators WHERE id = %s AND business_id = %s",
            (operator_id, user["business_id"]),
        )
        if not cur.fetchone():
            return RedirectResponse(url="/manage/operators/", status_code=302)

        # Clear existing and re-insert
        cur.execute("DELETE FROM operator_treatments WHERE operator_id = %s", (operator_id,))
        for treat_id in treatment_ids:
            cur.execute(
                "INSERT INTO operator_treatments (operator_id, treatment_id) VALUES (%s, %s)",
                (operator_id, treat_id),
            )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)
