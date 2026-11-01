"""ASGI middleware for the mounted MCP endpoint.

The admin REST API has its own dependency-based auth (see
`myna.api.auth.require_admin`). The MCP endpoint is a mounted Starlette
sub-app that doesn't go through FastAPI's dependency system, so we
enforce auth and rate limiting here as ASGI middleware scoped to the
mount prefix.

Three middlewares live here, intended to be applied in this order on the
FastAPI app (outermost first):

    app.add_middleware(MountPathMiddleware, ...)   # inner: runs third
    app.add_middleware(RateLimitMiddleware, ...)   # middle: runs second
    app.add_middleware(MCPAuthMiddleware, ...)     # outer: runs first

Starlette wraps middleware in LIFO order, so the last `add_middleware`
call sits closest to the network and runs first on inbound requests.
That means auth resolves the caller label into the `current_caller`
ContextVar before the rate limiter reads it. Failed auth attempts never
reach the rate limit middleware, so the auth middleware limits them itself.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from starlette.types import ASGIApp, Receive, Scope, Send

from myna.context import ANONYMOUS, current_caller
from myna.observability import RATE_LIMIT_HITS
from myna.rate_limit import RateLimiter


class MountPathMiddleware:
    """Serve the bare mount path directly instead of redirecting to `path/`.

    Starlette only routes `/mcp/` to the mounted app and answers `/mcp` with
    a 307, which not every MCP client follows.
    """

    def __init__(self, app: ASGIApp, *, prefix: str) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._prefix and scope.get("path") == self._prefix:
            path = f"{self._prefix}/"
            scope = {**scope, "path": path, "raw_path": path.encode("ascii")}
        await self._app(scope, receive, send)


class MCPAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str,
        verifier: TokenVerifier,
        allow_anonymous: bool,
        failure_limiter: RateLimiter,
    ) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/") or "/"
        self._verifier = verifier
        self._allow_anonymous = allow_anonymous
        self._failure_limiter = failure_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _path_matches(scope, self._prefix):
            await self._app(scope, receive, send)
            return

        caller = await self._authenticate(scope)
        if caller is None:
            # Only failures spend these tokens, so a valid key still works
            # from an address that is being throttled.
            allowed, retry_after = await self._failure_limiter.check(
                f"auth-failure:{_client_ip(scope)}"
            )
            if not allowed:
                RATE_LIMIT_HITS.labels(key_kind="auth_failure").inc()
                await _send_rate_limited(send, retry_after)
                return

            await _send_json(
                send,
                401,
                {"error": "unauthorized"},
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return

        token = current_caller.set(caller)
        try:
            await self._app(scope, receive, send)
        finally:
            current_caller.reset(token)

    async def _authenticate(self, scope: Scope) -> str | None:
        header = _get_header(scope, b"authorization")
        if not header:
            return ANONYMOUS if self._allow_anonymous else None

        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None

        access_token = await self._verifier.verify_token(token)
        return access_token.client_id if access_token else None


class RateLimitMiddleware:
    """Token-bucket rate limit on the MCP endpoint, keyed by caller or IP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str,
        limiter: RateLimiter,
    ) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/") or "/"
        self._limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not _path_matches(scope, self._prefix)
            or not self._limiter.enabled
        ):
            await self._app(scope, receive, send)
            return

        key, key_kind = _rate_limit_key(scope)
        allowed, retry_after = await self._limiter.check(key)
        if not allowed:
            RATE_LIMIT_HITS.labels(key_kind=key_kind).inc()
            await _send_rate_limited(send, retry_after)
            return

        await self._app(scope, receive, send)


def _path_matches(scope: Scope, prefix: str) -> bool:
    path: str = scope.get("path", "")
    if prefix == "/":
        return True
    return path == prefix or path.startswith(prefix + "/")


def _rate_limit_key(scope: Scope) -> tuple[str, str]:
    caller = current_caller.get()
    if caller != ANONYMOUS:
        return f"caller:{caller}", "caller"
    return f"ip:{_client_ip(scope)}", "ip"


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _get_header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            decoded: str = value.decode("latin-1")
            return decoded
    return ""


async def _send_rate_limited(send: Send, retry_after: float) -> None:
    retry_after_seconds = max(1, int(retry_after) + 1)
    await _send_json(
        send,
        429,
        {"error": "rate_limited", "retry_after": round(retry_after, 2)},
        extra_headers=[(b"retry-after", str(retry_after_seconds).encode("ascii"))],
    )


async def _send_json(
    send: Send,
    status_code: int,
    payload: dict[str, Any],
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# Convenience alias kept for clarity in type hints elsewhere.
ASGICallable = Callable[[Scope, Receive, Send], Awaitable[None]]
