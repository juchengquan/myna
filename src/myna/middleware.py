"""Path-scoped bearer-token auth for the mounted MCP endpoint.

The admin REST API has its own dependency-based auth (see
`myna.api.admin.require_admin`). The MCP endpoint is a mounted Starlette
sub-app that doesn't go through FastAPI's dependency system, so we
enforce auth here as ASGI middleware scoped to the mount prefix.

Behavior:
- Requests outside `MYNA_MCP_MOUNT_PATH` are passed through unchanged.
- For MCP requests, the middleware extracts a bearer token from the
  `Authorization` header and looks it up in `MYNA_MCP_API_KEYS`.
- On success, it sets `myna.context.current_caller` to the configured
  label for that key, so the tool-call observer can attribute the call.
- If no keys are configured:
    * in `development` the request is allowed as `anonymous`,
    * in `staging`/`production` the request is rejected with `401`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from myna.context import ANONYMOUS, current_caller


class MCPAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str,
        api_keys: dict[str, str],
        allow_anonymous: bool,
    ) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/") or "/"
        self._api_keys = dict(api_keys)
        self._allow_anonymous = allow_anonymous

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._matches(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        caller = self._authenticate(scope)
        if caller is None:
            await _send_json(send, 401, {"error": "unauthorized"})
            return

        token = current_caller.set(caller)
        try:
            await self._app(scope, receive, send)
        finally:
            current_caller.reset(token)

    def _matches(self, path: str) -> bool:
        if self._prefix == "/":
            return True
        return path == self._prefix or path.startswith(self._prefix + "/")

    def _authenticate(self, scope: Scope) -> str | None:
        header = _get_header(scope, b"authorization")
        if not header:
            return ANONYMOUS if self._allow_anonymous and not self._api_keys else None

        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None

        return self._api_keys.get(token)


def _get_header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            decoded: str = value.decode("latin-1")
            return decoded
    return ""


async def _send_json(send: Send, status_code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# Convenience alias kept for clarity in type hints elsewhere.
ASGICallable = Callable[[Scope, Receive, Send], Awaitable[None]]
