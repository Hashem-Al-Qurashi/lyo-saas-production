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


@router.get("/manage/faq/", response_class=HTMLResponse)
async def list_faqs(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, question, answer, is_active, sort_order
               FROM business_faqs WHERE business_id = %s ORDER BY sort_order""",
            (user["business_id"],),
        )
        faqs = cur.fetchall()

    return templates.TemplateResponse("faq.html", {
        "request": request, "user": user, "faqs": faqs,
    })


@router.post("/manage/faq/add")
async def add_faq(
    request: Request,
    question: str = Form(...),
    answer: str = Form(...),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO business_faqs (business_id, question, answer)
               VALUES (%s, %s, %s)""",
            (user["business_id"], question.strip(), answer.strip()),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/faq/", status_code=302)


@router.post("/manage/faq/{faq_id}/edit")
async def edit_faq(
    request: Request,
    faq_id: int,
    question: str = Form(...),
    answer: str = Form(...),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    form_data = await request.form()
    is_active = "is_active" in form_data

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE business_faqs SET question = %s, answer = %s, is_active = %s
               WHERE id = %s AND business_id = %s""",
            (question.strip(), answer.strip(), is_active,
             faq_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/faq/", status_code=302)


@router.post("/manage/faq/{faq_id}/delete")
async def delete_faq(request: Request, faq_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM business_faqs WHERE id = %s AND business_id = %s",
            (faq_id, user["business_id"]),
        )

    tenant_service.invalidate_by_business_id(user["business_id"])
    return RedirectResponse(url="/manage/faq/", status_code=302)
