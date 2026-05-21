from __future__ import annotations

import hashlib
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

Unit = Literal["celsius", "fahrenheit"]

_CONDITIONS = ("sunny", "cloudy", "rainy", "snowy", "windy", "foggy")


class WeatherReport(BaseModel):
    location: str
    temperature: float = Field(description="Current temperature in the requested unit")
    unit: Unit
    condition: str
    humidity_pct: int = Field(ge=0, le=100)
    wind_kph: float
    note: str = "Dummy data — this tool returns deterministic fake values for testing."


def fake_weather(location: str, unit: Unit = "celsius") -> WeatherReport:
    """Deterministic fake weather report keyed by location.

    Shared by the `get_weather` tool and the `weather://locations/{location}`
    resource so the two surfaces never drift.
    """
    digest = hashlib.sha256(location.strip().lower().encode("utf-8")).digest()

    temp_c = -10 + (digest[0] / 255.0) * 45  # -10..35 C
    temperature = temp_c if unit == "celsius" else temp_c * 9 / 5 + 32
    condition = _CONDITIONS[digest[1] % len(_CONDITIONS)]
    humidity = 20 + (digest[2] % 71)  # 20..90
    wind = round((digest[3] / 255.0) * 40, 1)  # 0..40 kph

    return WeatherReport(
        location=location,
        temperature=round(temperature, 1),
        unit=unit,
        condition=condition,
        humidity_pct=humidity,
        wind_kph=wind,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def get_weather(location: str, unit: Unit = "celsius") -> WeatherReport:
        """Return a (fake, deterministic) current-weather report for a location.

        Useful as a stand-in MCP tool while wiring up clients. The values
        are derived from a hash of the location string so repeated calls
        for the same place return the same result.
        """
        return fake_weather(location, unit)
