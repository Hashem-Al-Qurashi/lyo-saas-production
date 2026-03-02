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
