"""
HTTP Basic authentication for the whole application.

Applied as middleware rather than as a per-route dependency because the things
worth protecting are not all routes: the static UI, /docs and /openapi.json are
plain mounts, and a dependency would leave them open. Middleware sees every
request, so there is no route that can be added later and silently miss it.

Basic — rather than a bearer token — because the UI streams scan progress over
EventSource and offers exports as ordinary links, neither of which can set a
request header. The browser attaches Basic credentials to both without any
frontend involvement.
"""

import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.utils.logging import logger
from web import settings


class AuthConfigError(RuntimeError):
    """Raised at startup when auth is enabled but unusable."""


def check_auth_config() -> None:
    """
    Fails fast when auth is switched on without credentials.

    The alternative — starting anyway and rejecting every request — looks
    identical from outside to a wrong password, and the deployment that gets it
    wrong is exactly the one that cannot afford to debug it.
    """
    if not settings.AUTH_ENABLED:
        logger.warning(
            "Authentication is DISABLED. Every endpoint, including scan results and "
            "validation history, is open to anyone who can reach this port. Set "
            "MAILCRAWL_AUTH_ENABLED=true before exposing this server."
        )
        return

    if not settings.AUTH_USERNAME or not settings.AUTH_PASSWORD:
        raise AuthConfigError(
            "MAILCRAWL_AUTH_ENABLED is true but MAILCRAWL_USER or MAILCRAWL_PASSWORD "
            "is empty. Set both, or disable authentication explicitly."
        )

    logger.info(f"Authentication enabled for user '{settings.AUTH_USERNAME}'.")


def _unauthorized() -> Response:
    """
    401 carrying the challenge that makes a browser prompt for credentials.

    WWW-Authenticate is what turns this from an error page into a login box, so
    the realm has to be quoted even though nothing displays it.
    """
    return JSONResponse(
        {"detail": "Authentication required."},
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{settings.AUTH_REALM}", charset="UTF-8"'},
    )


def _credentials_match(header: str) -> bool:
    """Validates one Authorization header against the configured credentials."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False

    username, separator, password = decoded.partition(":")
    if not separator:
        return False

    # Both halves are always compared, and compared in constant time, so neither
    # a wrong username nor a wrong password is distinguishable by timing.
    user_ok = secrets.compare_digest(username, settings.AUTH_USERNAME)
    password_ok = secrets.compare_digest(password, settings.AUTH_PASSWORD)
    return user_ok and password_ok


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated requests before they reach any route."""

    async def dispatch(self, request: Request, call_next):
        if not settings.AUTH_ENABLED:
            return await call_next(request)

        if request.url.path in settings.AUTH_EXEMPT_PATHS:
            return await call_next(request)

        header = request.headers.get("Authorization")
        if not header or not _credentials_match(header):
            return _unauthorized()

        return await call_next(request)
