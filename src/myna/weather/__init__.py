"""Example integration: current weather from Open-Meteo.

Shows a real upstream API, a cached tool, a templated resource, and a prompt
in one place. To remove it, delete this package and `tests/weather/`, then
delete its two lines in `mcp_server.py`.
"""

from myna.weather.server import register

__all__ = ["register"]
