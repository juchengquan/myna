from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp.server.auth.provider import TokenVerifier
from pydantic import ValidationError

from myna.config import Settings, parse_mcp_api_keys
from myna.verifiers import StaticKeyVerifier

SECRET = "sk-very-secret"
DEPLOYMENT_DOC = Path(__file__).parents[1] / "docs" / "deployment.md"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_means_no_keys(raw: str) -> None:
    assert parse_mcp_api_keys(raw) == {}


def test_parses_entries_into_key_to_name_map() -> None:
    raw = " client-a:sk-abc , client_b:sk-def "
    assert parse_mcp_api_keys(raw) == {"sk-abc": "client-a", "sk-def": "client_b"}


def test_key_may_contain_colons() -> None:
    assert parse_mcp_api_keys("client-a:sk:abc") == {"sk:abc": "client-a"}


def test_name_may_repeat_for_rotation() -> None:
    raw = "client-a:sk-old,client-a:sk-new"
    assert parse_mcp_api_keys(raw) == {"sk-old": "client-a", "sk-new": "client-a"}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (f'{{"{SECRET}":"client-a"}}', "no longer accepts JSON"),
        (SECRET, "entry 1 is missing the colon"),
        (f"client-a:{SECRET},", "entry 2 is missing the colon"),
        (f"client a:{SECRET}", "entry 1 has a name"),
        (f":{SECRET}", "entry 1 has a name"),
        (f"anonymous:{SECRET}", "entry 1 uses the reserved name"),
        ("client-a:", "entry 1 has an empty key"),
        (f"client-a:{SECRET} x", "entry 1 has an empty key, or a key containing spaces"),
        (f"client-a:{SECRET},client-b:{SECRET}", "entry 2 repeats a key"),
    ],
)
def test_rejects_malformed_values_without_printing_keys(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as exc_info:
        parse_mcp_api_keys(raw)

    assert SECRET not in str(exc_info.value)


def test_settings_error_does_not_print_the_rejected_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYNA_MCP_API_KEYS", SECRET)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "entry 1 is missing the colon" in str(exc_info.value)
    assert SECRET not in str(exc_info.value)


def test_settings_read_the_new_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYNA_MCP_API_KEYS", "client-a:sk-abc")
    assert Settings().mcp_api_keys == {"sk-abc": "client-a"}


async def test_static_key_verifier_returns_the_caller_name() -> None:
    verifier: TokenVerifier = StaticKeyVerifier({"sk-abc": "client-a"})

    access_token = await verifier.verify_token("sk-abc")

    assert access_token is not None
    assert access_token.client_id == "client-a"
    assert await verifier.verify_token("sk-wrong") is None


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl isn't installed")
def test_documented_generator_produces_a_valid_entry() -> None:
    pattern = r'^echo "client-a:\$\(openssl rand -hex 32\)"$'
    match = re.search(pattern, DEPLOYMENT_DOC.read_text(), re.M)
    assert match, "docs/deployment.md should show the key generator"

    output = subprocess.run(
        ["bash", "-c", match.group(0)], capture_output=True, text=True, check=True
    ).stdout.strip()

    parsed = parse_mcp_api_keys(output)
    assert list(parsed.values()) == ["client-a"]
    assert len(next(iter(parsed))) == 64
