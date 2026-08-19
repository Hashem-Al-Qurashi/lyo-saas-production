import os
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from management.auth import decode_token, hash_password
from app.models.database import get_connection

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/users/", response_class=HTMLResponse)
async def list_users(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)
    if user.get("role") not in ("owner", "admin"):
        return RedirectResponse(url="/manage/dashboard", status_code=302)

    biz_id = user["business_id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, email, role, is_active
               FROM management_users
               WHERE business_id = %s
               ORDER BY name""",
            (biz_id,),
        )
        users_list = cur.fetchall()

    return templates.TemplateResponse("users.html", {
        "request": request,
        "user": user,
        "users": users_list,
        "error": None,
        "success": None,
    })


@router.post("/manage/users/add")
async def add_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("staff"),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)
    if user.get("role") not in ("owner", "admin"):
        return RedirectResponse(url="/manage/dashboard", status_code=302)

    biz_id = user["business_id"]
    error = None

    ALLOWED_ROLES = {"owner", "admin", "staff"}
    if role not in ALLOWED_ROLES:
        role = "staff"

    if len(password) < 6:
        error = "La password deve avere almeno 6 caratteri"
    else:
        try:
            hashed = hash_password(password)
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO management_users (business_id, name, email, password_hash, role)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (biz_id, name, email, hashed, role),
                )
        except Exception as e:
            if "unique" in str(e).lower():
                error = "Email gia in uso"
            else:
                error = "Errore nella creazione dell'utente"

    if error:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, email, role, is_active FROM management_users WHERE business_id = %s ORDER BY name",
                (biz_id,),
            )
            users_list = cur.fetchall()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users_list,
            "error": error, "success": None,
        })

    return RedirectResponse(url="/manage/users/", status_code=302)


@router.post("/manage/users/{user_id}/toggle")
async def toggle_user(request: Request, user_id: int):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)
    if user.get("role") not in ("owner", "admin"):
        return RedirectResponse(url="/manage/dashboard", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE management_users SET is_active = NOT is_active, updated_at = NOW()
               WHERE id = %s AND business_id = %s""",
            (user_id, user["business_id"]),
        )

    return RedirectResponse(url="/manage/users/", status_code=302)


@router.post("/manage/users/{user_id}/password")
async def change_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)
    if user.get("role") not in ("owner", "admin"):
        return RedirectResponse(url="/manage/dashboard", status_code=302)

    biz_id = user["business_id"]

    if len(new_password) < 6:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, email, role, is_active FROM management_users WHERE business_id = %s ORDER BY name",
                (biz_id,),
            )
            users_list = cur.fetchall()
        return templates.TemplateResponse("users.html", {
            "request": request, "user": user, "users": users_list,
            "error": "La password deve avere almeno 6 caratteri", "success": None,
        })

    hashed = hash_password(new_password)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE management_users SET password_hash = %s, updated_at = NOW()
               WHERE id = %s AND business_id = %s""",
            (hashed, user_id, biz_id),
        )

    return RedirectResponse(url="/manage/users/", status_code=302)
