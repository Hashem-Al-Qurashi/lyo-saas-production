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


@router.get("/manage/operators/", response_class=HTMLResponse)
async def list_operators(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, technical_id, display_name, is_active, sort_order
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

    op_treatments = {}
    for op_id, treat_id in op_treat_links:
        op_treatments.setdefault(op_id, []).append(treat_id)

    return templates.TemplateResponse("operators.html", {
        "request": request, "user": user, "operators": operators_list,
        "treatments": treatments_list, "op_treatments": op_treatments,
    })


@router.post("/manage/operators/add")
async def add_operator(
    request: Request,
    technical_id: str = Form(...),
    display_name: str = Form(...),
    sort_order: int = Form(0),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO operators (business_id, technical_id, display_name, sort_order)
               VALUES (%s, %s, %s, %s)""",
            (user["business_id"], technical_id, display_name, sort_order),
        )

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/edit")
async def edit_operator(
    request: Request,
    operator_id: int,
    technical_id: str = Form(...),
    display_name: str = Form(...),
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
            """UPDATE operators SET technical_id = %s, display_name = %s,
                      sort_order = %s, is_active = %s
               WHERE id = %s AND business_id = %s""",
            (technical_id, display_name, sort_order, is_active, operator_id, user["business_id"]),
        )

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)


@router.post("/manage/operators/{operator_id}/delete")
async def delete_operator(request: Request, operator_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE operators SET is_active = false WHERE id = %s AND business_id = %s",
            (operator_id, user["business_id"]),
        )

    tenant_service.invalidate(user["business_id"])
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

    tenant_service.invalidate(user["business_id"])
    return RedirectResponse(url="/manage/operators/", status_code=302)
