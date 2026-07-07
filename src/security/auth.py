"""Bearer token authentication middleware for SSE transport."""

import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp.security.auth")

_API_KEY: str | None = os.environ.get("MCP_API_KEY")

if _API_KEY and not _API_KEY.isascii():
    logger.error(
        "MCP_API_KEY contient des caractères non-ASCII - "
        "les headers HTTP ne supportent que l'ASCII. "
        "Générez une clé avec : openssl rand -hex 32"
    )
    _API_KEY = None


_PROTECTED_PREFIXES = ("/sse", "/messages")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Verify the Authorization: Bearer <key> header on MCP endpoints.

    Only /sse and /messages/ are protected. Discovery endpoints
    (.well-known/*, /register) remain accessible so the MCP client
    can perform its initial negotiation.

    If MCP_API_KEY is not set, all requests are allowed
    (backward compatibility) with a warning on the first call.
    """

    _warned_no_key = False

    async def dispatch(self, request: Request, call_next):
        """Validate the Bearer token on protected endpoints.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response (401/403 on auth failure, or the downstream response).
        """
        if not request.url.path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)

        if not _API_KEY:
            if not BearerAuthMiddleware._warned_no_key:
                logger.warning(
                    "⚠ SÉCURITÉ : MCP_API_KEY non définie - le serveur SSE "
                    "accepte toutes les connexions sans authentification. "
                    "En production, générez une clé avec 'openssl rand -hex 32' "
                    "et ajoutez-la dans ~/.mcp-db-results-anonymizer/.env "
                    "(variable MCP_API_KEY)."
                )
                BearerAuthMiddleware._warned_no_key = True
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("Requête rejetée - header Authorization manquant | ip=%s", request.client.host if request.client else "unknown")
            return JSONResponse(
                {"error": "Header Authorization: Bearer <key> requis"},
                status_code=401,
            )

        token = auth_header[7:]
        if not hmac.compare_digest(token.encode(), _API_KEY.encode()):
            logger.warning("Requête rejetée - API key invalide | ip=%s", request.client.host if request.client else "unknown")
            return JSONResponse(
                {"error": "API key invalide"},
                status_code=403,
            )

        return await call_next(request)
