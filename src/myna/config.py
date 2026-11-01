from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYNA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    mcp_api_keys: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Bearer tokens accepted on the MCP endpoint. JSON object mapping "
            "key -> caller label, e.g. "
            '`MYNA_MCP_API_KEYS=\'{"sk-abc":"client-a"}\'`. In development, '
            "an empty map means anonymous access is allowed; in staging/"
            "production an empty map blocks all MCP traffic."
        ),
    )

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

    open_meteo_geocoding_url: str = Field(
        default="https://geocoding-api.open-meteo.com",
        description=(
            "Base URL for the Open-Meteo geocoding API. Used by the "
            "`get_weather` tool to turn a location name into coordinates. "
            "Overridable so tests can point at a mock transport."
        ),
    )
    open_meteo_forecast_url: str = Field(
        default="https://api.open-meteo.com",
        description=(
            "Base URL for the Open-Meteo forecast API. Used by the "
            "`get_weather` tool to fetch current conditions for resolved "
            "coordinates. Overridable so tests can point at a mock transport."
        ),
    )
    open_meteo_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Per-request timeout for Open-Meteo HTTP calls.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
