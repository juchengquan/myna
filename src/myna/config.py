from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_KEY_NAME = re.compile(r"[A-Za-z0-9_-]+")
_KEY_VALUE = re.compile(r"[\x21-\x7e]+")
# Means an unauthenticated caller, in metrics and rate limiting.
_RESERVED_KEY_NAMES = {"anonymous"}


def parse_mcp_api_keys(raw: str) -> dict[str, str]:
    """Parse `name:key,name:key` into a `{key: name}` map.

    A name may repeat, so a client can hold two keys during rotation. Errors
    identify an entry by its position and never include its text, because a
    malformed entry may be a real key.
    """
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        raise ValueError(
            "MYNA_MCP_API_KEYS no longer accepts JSON. Use name:key,name:key, "
            "for example client-a:sk-abc,client-b:sk-def"
        )

    keys: dict[str, str] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        name, separator, key = entry.strip().partition(":")
        problem = None
        if not separator:
            problem = "is missing the colon between name and key"
        elif not _KEY_NAME.fullmatch(name):
            problem = "has a name that isn't only letters, digits, - and _"
        elif name in _RESERVED_KEY_NAMES:
            problem = f"uses the reserved name {name!r}"
        elif not _KEY_VALUE.fullmatch(key):
            problem = "has an empty key, or a key containing spaces or non-ASCII characters"
        elif key in keys:
            problem = "repeats a key from an earlier entry"
        if problem:
            raise ValueError(f"MYNA_MCP_API_KEYS entry {position} {problem}")
        keys[key] = name
    return keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYNA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors would otherwise print the rejected value, which
        # can be a secret.
        hide_input_in_errors=True,
    )

    env: Literal["development", "staging", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    mcp_server_name: str = "myna"
    mcp_mount_path: str = "/mcp"
    mcp_stateless: bool = Field(
        default=True,
        description=(
            "Run the MCP endpoint in stateless HTTP mode, so replicas need no "
            "session affinity. Legacy clients can't be asked questions in this "
            "mode, so `confirm_action` returns a protocol error for them."
        ),
    )
    mcp_session_idle_timeout_seconds: float = Field(
        default=1800,
        ge=0,
        description=(
            "Close a stateful session after this long with nothing in flight. "
            "Set to 0 to never expire sessions. Has no effect in stateless mode."
        ),
    )
    mcp_max_sessions: int = Field(
        default=10_000,
        ge=0,
        description=(
            "Refuse new stateful sessions past this many per process, with a "
            "503. Set to 0 for no limit. Has no effect in stateless mode."
        ),
    )

    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        description=(
            "Host header values accepted on the MCP endpoint, as a JSON list. "
            "Anything else gets a 421. Entries match exactly, or use `host:*` "
            'for any port. Remote deployments must add their hostname, e.g. '
            '`MYNA_MCP_ALLOWED_HOSTS=\'["myna.example.com"]\'`.'
        ),
    )
    mcp_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        description=(
            "Origin header values accepted on the MCP endpoint, as a JSON list. "
            "Anything else gets a 403. Only browser-based clients send Origin."
        ),
    )

    admin_api_key: str | None = Field(default=None, description="Bearer token for admin API")
    metrics_api_key: str | None = Field(
        default=None,
        description=(
            "Bearer token for `/metrics`. In development, unset means open "
            "access; in staging/production, unset makes `/metrics` return 500."
        ),
    )

    mcp_api_keys: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict,
        description=(
            "Bearer tokens accepted on the MCP endpoint, as `name:key,name:key`, "
            "e.g. `MYNA_MCP_API_KEYS=client-a:sk-abc,client-b:sk-def`. The name is "
            "the caller in metrics and audit logs. A name may repeat, so a client "
            "can hold two keys during rotation. In development, no keys means "
            "anonymous access is allowed; in staging/production it blocks all MCP "
            "traffic."
        ),
    )

    @field_validator("mcp_api_keys", mode="before")
    @classmethod
    def _parse_mcp_api_keys(cls, value: object) -> object:
        return parse_mcp_api_keys(value) if isinstance(value, str) else value

    mcp_rate_limit_per_minute: int = Field(
        default=120,
        ge=0,
        description=(
            "Per-caller request quota on the MCP endpoint, expressed as a "
            "sustained rate. The token bucket has a burst capacity equal to "
            "this value. Set to 0 to disable rate limiting. Keyed by caller "
            "label when authenticated, otherwise by client IP."
        ),
    )

    otel_enabled: bool = Field(
        default=False,
        description=(
            "Toggle for OpenTelemetry tracing. When false (default), all "
            "tracing setup is skipped and the SDK's no-op provider is used "
            "— zero overhead. Set to true to record spans for HTTP requests "
            "and MCP tool calls."
        ),
    )
    otel_service_name: str = Field(
        default="myna",
        description="Value for the OTel `service.name` resource attribute.",
    )
    otel_exporter_endpoint: str | None = Field(
        default=None,
        description=(
            "OTLP/HTTP collector endpoint, e.g. `http://localhost:4318/v1/traces`. "
            "When unset and tracing is enabled, spans are exported via the "
            "console exporter (useful for local debugging)."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
