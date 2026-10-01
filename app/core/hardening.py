"""Security headers and a request body size cap, as pure ASGI middleware."""

import json

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Largest valid request is a chat message: 1000 characters, at most 12 bytes each once
# JSON-escaped (about 12 KB). Everything else is far smaller. Anything bigger is refused early,
# so a client cannot make the server buffer and parse a huge body.
MAX_BODY_BYTES = 32 * 1024

# Safe for a JSON API with no browser pages of its own. `no-store` because responses are
# per-user (bookings, chat replies) and must not be kept by shared caches.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

# Sent only in production (the app sits behind the host's HTTPS proxy). Deliberately short and
# without includeSubDomains or preload: browsers cache it, so a mistake must expire quickly.
# Raise the max-age step by step (a day, a week, ...) once nothing breaks.
HSTS_VALUE = "max-age=300"

_TOO_LARGE_BODY = json.dumps(
    {"error": {"code": "payload_too_large", "message": "The request body is too large."}}
).encode()


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        self.app = app
        self.headers = {
            **SECURITY_HEADERS,
            **({"Strict-Transport-Security": HSTS_VALUE} if hsts else {}),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuses bodies over `max_bytes` with 413 in the standard error format.

    Honest clients are refused from their Content-Length without running the app. A body that
    lies about its length, or is chunked, is cut off while it is being read: the app then sees an
    empty body and whatever it answers is replaced by the 413.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        too_large = False
        replaced = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal replaced
            if not too_large:
                await send(message)
                return
            if not replaced:
                replaced = True
                await self._reject(send)
            # Everything else the app tries to send for this request is dropped.

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reject(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
