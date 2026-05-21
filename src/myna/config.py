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

    admin_api_key: str | None = Field(default=None, description="Bearer token for admin API")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
