import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from app.config import settings
from app.models.database import get_connection
from management.auth import hash_password

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)

_SA_COOKIE = "sa_token"
_SA_ROLE = "superadmin"


def _create_sa_token() -> str:
    expire = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode(
        {"sub": settings.superadmin_email, "role": _SA_ROLE, "exp": expire},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


def _get_sa_user(request: Request) -> bool:
    token = request.cookies.get(_SA_COOKIE)
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload.get("role") == _SA_ROLE
    except JWTError:
        return False


@router.get("/superadmin/login", response_class=HTMLResponse)
async def sa_login_page(request: Request):
    return templates.TemplateResponse("superadmin_login.html", {"request": request, "error": None})


@router.post("/superadmin/login")
async def sa_login(request: Request, email: str = Form(...), password: str = Form(...)):
    if email != settings.superadmin_email or password != settings.superadmin_password:
        return templates.TemplateResponse("superadmin_login.html", {
            "request": request, "error": "Credenziali non valide"
        })
    token = _create_sa_token()
    response = RedirectResponse(url="/superadmin/", status_code=302)
    response.set_cookie(
        _SA_COOKIE, token,
        httponly=True,
        max_age=12 * 3600,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/superadmin/logout")
async def sa_logout():
    response = RedirectResponse(url="/superadmin/login", status_code=302)
    response.delete_cookie(_SA_COOKIE)
    return response


@router.get("/superadmin/", response_class=HTMLResponse)
async def sa_dashboard(request: Request):
    if not _get_sa_user(request):
        return RedirectResponse(url="/superadmin/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT b.id, b.name, b.slug, b.status, b.created_at::text,
                   COUNT(DISTINCT a.id) AS appt_count,
                   COUNT(DISTINCT u.id) AS user_count
            FROM businesses b
            LEFT JOIN appointments a ON a.business_id = b.id
            LEFT JOIN management_users u ON u.business_id = b.id
            GROUP BY b.id
            ORDER BY b.id
        """)
        businesses = cur.fetchall()

    return templates.TemplateResponse("superadmin.html", {
        "request": request,
        "businesses": businesses,
        "error": None,
        "success": None,
    })


@router.post("/superadmin/tenants/create")
async def sa_create_tenant(
    request: Request,
    salon_name: str = Form(...),
    owner_name: str = Form(...),
    owner_email: str = Form(...),
    owner_password: str = Form(...),
    timezone: str = Form("Europe/Rome"),
):
    if not _get_sa_user(request):
        return RedirectResponse(url="/superadmin/login", status_code=302)

    error = None
    if len(owner_password) < 6:
        error = "Password must be at least 6 characters"
    elif not owner_email or "@" not in owner_email:
        error = "Invalid email address"
    elif not salon_name.strip():
        error = "Salon name is required"

    if error:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT b.id, b.name, b.slug, b.status, b.created_at::text,
                       COUNT(DISTINCT a.id), COUNT(DISTINCT u.id)
                FROM businesses b
                LEFT JOIN appointments a ON a.business_id = b.id
                LEFT JOIN management_users u ON u.business_id = b.id
                GROUP BY b.id ORDER BY b.id
            """)
            businesses = cur.fetchall()
        return templates.TemplateResponse("superadmin.html", {
            "request": request, "businesses": businesses, "error": error, "success": None,
        })

    slug = salon_name.strip().lower().replace(" ", "-").replace("'", "")

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Create business
            cur.execute("""
                INSERT INTO businesses (name, slug, timezone, language, bot_name, status)
                VALUES (%s, %s, %s, 'it', 'Lyo', 'active')
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """, (salon_name.strip(), slug, timezone))
            biz_id = cur.fetchone()[0]

            # Default business hours: Mon-Sat 09:00-19:00, Sun closed
            for dow in range(7):
                is_open = dow < 6
                cur.execute("""
                    INSERT INTO business_hours (business_id, day_of_week, is_open, open_time, close_time)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (business_id, day_of_week) DO NOTHING
                """, (biz_id, dow, is_open,
                      "09:00" if is_open else None,
                      "19:00" if is_open else None))

            # Owner management user
            cur.execute("""
                INSERT INTO management_users (business_id, name, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, 'owner', true)
                ON CONFLICT (email) DO UPDATE SET business_id = EXCLUDED.business_id,
                    password_hash = EXCLUDED.password_hash
                RETURNING id
            """, (biz_id, owner_name.strip(), owner_email.strip(), hash_password(owner_password)))
            cur.fetchone()

        success = f"Tenant '{salon_name.strip()}' created. Login: {owner_email.strip()}"
    except Exception as e:
        error = f"Error creating tenant: {e}"
        success = None

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT b.id, b.name, b.slug, b.status, b.created_at::text,
                   COUNT(DISTINCT a.id), COUNT(DISTINCT u.id)
            FROM businesses b
            LEFT JOIN appointments a ON a.business_id = b.id
            LEFT JOIN management_users u ON u.business_id = b.id
            GROUP BY b.id ORDER BY b.id
        """)
        businesses = cur.fetchall()

    return templates.TemplateResponse("superadmin.html", {
        "request": request, "businesses": businesses,
        "error": error, "success": success if not error else None,
    })


@router.post("/superadmin/tenants/{biz_id}/toggle")
async def sa_toggle_tenant(request: Request, biz_id: int):
    if not _get_sa_user(request):
        return RedirectResponse(url="/superadmin/login", status_code=302)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE businesses
            SET status = CASE WHEN status = 'active' THEN 'inactive' ELSE 'active' END,
                updated_at = NOW()
            WHERE id = %s
        """, (biz_id,))

    return RedirectResponse(url="/superadmin/", status_code=302)
