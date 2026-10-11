"""Starlette middleware redirecting anonymous requests to the login page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from nicegui import app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

_PUBLIC_PREFIXES = ("/login", "/register", "/_nicegui", "/_storage")


def _is_public(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PREFIXES)


def _sanitized_target(path: str) -> str:
    """Return a safe in-app redirect target; never return absolute or protocol-relative URLs."""
    if path.startswith("/") and not path.startswith("//"):
        return path
    return "/"


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a logged-in session for every non-public route."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if _is_public(path):
            return await call_next(request)
        user = app.storage.user
        if user.get("id") is not None:
            return await call_next(request)
        target = _sanitized_target(path)
        return RedirectResponse(
            f"/login?redirect_to={quote(target)}",
            status_code=303,
        )
