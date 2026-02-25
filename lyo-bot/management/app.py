import os
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from management.auth import authenticate_user, create_token, decode_token
from app.config import settings

mgmt_app = FastAPI(title="Lyo Management")
mgmt_app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret)

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@mgmt_app.get("/manage/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@mgmt_app.post("/manage/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = authenticate_user(email, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Credenziali non valide"})
    token = create_token(user["email"], user["business_id"])
    response = RedirectResponse(url="/manage/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=settings.jwt_expire_minutes * 60)
    return response


@mgmt_app.get("/manage/logout")
async def logout():
    response = RedirectResponse(url="/manage/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@mgmt_app.get("/manage/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


# Import and register route modules
from management.routes import treatments, operators, hours, settings as settings_routes, appointments, users  # noqa: E402

mgmt_app.include_router(appointments.router)
mgmt_app.include_router(treatments.router)
mgmt_app.include_router(operators.router)
mgmt_app.include_router(hours.router)
mgmt_app.include_router(settings_routes.router)
mgmt_app.include_router(users.router)
