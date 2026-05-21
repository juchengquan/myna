from __future__ import annotations

import pytest

from myna.mcp_server import build_mcp


@pytest.mark.asyncio
async def test_prompts_registered() -> None:
    mcp = build_mcp()
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert {"summarize", "weather-report"}.issubset(names)


@pytest.mark.asyncio
async def test_summarize_prompt_renders_with_args() -> None:
    mcp = build_mcp()
    result = await mcp.get_prompt("summarize", {"text": "hello world", "sentences": 1})
    assert result.messages
    body = result.messages[0].content.text  # type: ignore[union-attr]
    assert "1 sentence" in body
    assert "hello world" in body


@pytest.mark.asyncio
async def test_weather_report_prompt_renders_with_location() -> None:
    mcp = build_mcp()
    result = await mcp.get_prompt(
        "weather-report", {"location": "Berlin", "tone": "playful"}
    )
    assert result.messages
    body = result.messages[0].content.text  # type: ignore[union-attr]
    assert "Berlin" in body
    assert "playful" in body
