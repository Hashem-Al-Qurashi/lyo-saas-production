"""CSRF protection middleware for management dashboard.

Generates a per-session CSRF token, validates it on all POST requests
to /manage/* routes (except login, which has no prior session).
"""

import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class CSRFMiddleware(BaseHTTPMiddleware):

    EXEMPT_PATHS = {"/manage/login"}

    async def dispatch(self, request, call_next) -> Response:
        # Only apply to /manage/* routes
        if not request.url.path.startswith("/manage"):
            return await call_next(request)

        # Ensure session has a CSRF token
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_hex(32)

        # Make token available to templates via request.state
        request.state.csrf_token = request.session["csrf_token"]

        if request.method == "POST" and request.url.path not in self.EXEMPT_PATHS:
            # Read the raw body bytes
            body_bytes = await request.body()

            # Re-inject body so downstream handlers can read it again
            async def receive():
                return {"type": "http.request", "body": body_bytes}
            request._receive = receive

            # Parse form data manually from the raw bytes
            form_data = parse_qs(body_bytes.decode("utf-8", errors="replace"))
            submitted_tokens = form_data.get("csrf_token", [])
            submitted_token = submitted_tokens[0] if submitted_tokens else ""
            expected_token = request.session.get("csrf_token", "")

            if not submitted_token or submitted_token != expected_token:
                return Response("CSRF token invalid", status_code=403)

        return await call_next(request)


def get_csrf_token(request) -> str:
    """Get CSRF token from request.state (set by middleware)."""
    return getattr(request.state, "csrf_token", "")
