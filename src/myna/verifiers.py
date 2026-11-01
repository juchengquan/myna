"""Token verifiers for the MCP endpoint.

A verifier turns a bearer token into who is calling. `MCPAuthMiddleware` only
talks to the SDK's `TokenVerifier` interface, so a new way of issuing tokens
means adding a verifier here, with no change to auth, metrics, or rate limiting.
The caller name is the verifier's `AccessToken.client_id`.
"""

from __future__ import annotations

import secrets

from mcp.server.auth.provider import AccessToken, TokenVerifier


class VerifierUnavailable(Exception):
    """A verifier can't reach what it checks against, so the caller gets a 503."""


class StaticKeyVerifier:
    """Accepts the keys configured in `MYNA_MCP_API_KEYS`."""

    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = {key.encode(): name for key, name in api_keys.items()}

    async def verify_token(self, token: str) -> AccessToken | None:
        # Check every key in constant time, so timing reveals nothing about
        # how close a guess was.
        presented = token.encode()
        name = None
        for key, candidate in self._api_keys.items():
            if secrets.compare_digest(key, presented):
                name = candidate
        if name is None:
            return None
        return AccessToken(token=token, client_id=name, scopes=[])


class ChainVerifier:
    """Tries each verifier in order and accepts the first match."""

    def __init__(self, *verifiers: TokenVerifier) -> None:
        self._verifiers = verifiers

    async def verify_token(self, token: str) -> AccessToken | None:
        for verifier in self._verifiers:
            access_token = await verifier.verify_token(token)
            if access_token is not None:
                return access_token
        return None
