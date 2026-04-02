"""FastAPI middleware — request logging, error handling."""
from __future__ import annotations

import logging
import time
import traceback

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("midi_macropad.http")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip static assets and WebSocket upgrades
        path = request.url.path
        if path.startswith("/assets") or path == "/ws" or path == "/favicon.ico":
            return await call_next(request)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.info("%s %s %d %.0fms", request.method, path,
                     response.status_code, elapsed_ms)
            return response
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.error("%s %s FAILED %.0fms: %s", request.method, path,
                      elapsed_ms, exc)
            raise
