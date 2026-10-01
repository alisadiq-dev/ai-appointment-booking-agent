"""Request id and access log, as a pure ASGI middleware.

Pure ASGI (not BaseHTTPMiddleware) so the request id context variable reaches the route code and
streaming responses are untouched. It logs method, route template, status and duration only:
never headers, query strings, raw paths or bodies.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
# An inbound id is trusted only if it is short and plain (log and header safe).
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

access_logger = logging.getLogger("app.access")


def _request_id_from(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _SAFE_REQUEST_ID.fullmatch(candidate):
                return candidate
    return str(uuid.uuid4())


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from(scope)
        scope.setdefault("state", {})["request_id"] = request_id  # for the 500 handler
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500  # what the caller sees if the app raises

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            access_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", None) or "unmatched",
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)
