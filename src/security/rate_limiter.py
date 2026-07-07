"""Sliding window rate limiting middleware for SSE transport."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp.security.rate_limit")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limit the number of requests per client (IP) over a sliding window."""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        """Initialize the rate limiter.

        Args:
            app: Starlette/ASGI application.
            max_requests: Maximum requests allowed per window (default: 60).
            window_seconds: Sliding window duration in seconds (default: 60).
        """
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}
        self._cleanup_counter: int = 0

    async def dispatch(self, request: Request, call_next):
        """Check the rate limit for the client IP and reject if exceeded.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response (429 if rate limit exceeded, or the downstream response).
        """
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        window = self._requests.get(client_ip)
        if window is not None:
            window[:] = [t for t in window if now - t < self.window_seconds]
            if not window:
                del self._requests[client_ip]
                window = None

        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            self._cleanup_counter = 0
            stale = [ip for ip, reqs in self._requests.items()
                     if reqs and reqs[-1] < now - self.window_seconds]
            for ip in stale:
                del self._requests[ip]

        if window is not None and len(window) >= self.max_requests:
            logger.warning(
                "Rate limit dépassé | ip=%s | %d req/%ds",
                client_ip, self.max_requests, self.window_seconds,
            )
            return JSONResponse(
                {"error": f"Rate limit dépassé ({self.max_requests} requêtes/{self.window_seconds}s)"},
                status_code=429,
                headers={"Retry-After": str(self.window_seconds)},
            )

        if window is None:
            self._requests[client_ip] = [now]
        else:
            window.append(now)
        return await call_next(request)
