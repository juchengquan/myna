from __future__ import annotations

from mcp.server.auth.provider import AccessToken

from myna.keys.store import KEY_PREFIX, KeyStore


class DatabaseKeyVerifier:
    """Accepts active keys from the key store.

    Tokens without the key prefix never touch the database, so a wrong key
    still gets a 401 while the database is down.
    """

    def __init__(self, store: KeyStore) -> None:
        self._store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(KEY_PREFIX):
            return None
        record = await self._store.find_active(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record.name,
            scopes=[],
            expires_at=int(record.expires_at.timestamp()) if record.expires_at else None,
        )
