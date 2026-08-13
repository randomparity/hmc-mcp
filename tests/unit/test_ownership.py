"""Tests for multi-agent LPAR ownership helpers (issue #132)."""
from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.ssh import validate_agent_id


# ---------------------------------------------------------------------------
# validate_agent_id
# ---------------------------------------------------------------------------


def test_validate_agent_id_valid():
    validate_agent_id("alice")           # plain name
    validate_agent_id("agent-1")         # hyphens ok
    validate_agent_id("agent.1")         # dots ok
    validate_agent_id("a" * 64)          # max length


def test_validate_agent_id_reserved():
    with pytest.raises(ValueError, match="reserved"):
        validate_agent_id("hmc-mcp")


def test_validate_agent_id_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_agent_id("")


def test_validate_agent_id_too_long():
    with pytest.raises(ValueError, match="64"):
        validate_agent_id("a" * 65)


def test_validate_agent_id_comma():
    with pytest.raises(ValueError, match="comma"):
        validate_agent_id("alice,eve")


def test_validate_agent_id_equals():
    with pytest.raises(ValueError, match="="):
        validate_agent_id("key=val")


def test_validate_agent_id_bracket():
    with pytest.raises(ValueError, match="bracket"):
        validate_agent_id("alice[1]")


def test_validate_agent_id_slash():
    with pytest.raises(ValueError, match="slash"):
        validate_agent_id("team/agent")


def test_validate_agent_id_non_ascii():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alicé")


def test_validate_agent_id_control_char():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alice\n")


# ---------------------------------------------------------------------------
# stamp_lpar_ownership
# ---------------------------------------------------------------------------


from hmc_mcp.ssh import stamp_lpar_ownership  # noqa: E402 (after validate tests)
from hmc_mcp.config import HMCConfig           # noqa: E402


def _config():
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def test_stamp_returns_token_on_success():
    config = _config()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")) as mock_set:
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    today = datetime.date.today().isoformat()
    assert token == f"[hmc-mcp owner:alice created:{today}]"
    mock_set.assert_awaited_once()
    # verify set_lpar_description was called with the token as the description arg
    call_args = mock_set.call_args.args
    assert call_args[3] == token


def test_stamp_default_agent_id():
    config = _config()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1")  # no agent_id
        )
    assert token is not None
    assert "owner:hmc-mcp" in token


def test_stamp_returns_none_on_ssh_error():
    config = _config()
    from hmc_mcp.ssh import HMCCLIError
    with patch(
        "hmc_mcp.ssh.set_lpar_description",
        new=AsyncMock(side_effect=HMCCLIError("SSH failed")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None  # swallowed — best-effort


def test_stamp_returns_none_on_asyncssh_error():
    import asyncssh
    config = _config()
    with patch(
        "hmc_mcp.ssh.set_lpar_description",
        new=AsyncMock(side_effect=asyncssh.DisconnectError(asyncssh.DISC_CONNECTION_LOST, "connection lost")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None


def test_token_format():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch("hmc_mcp.ssh.set_lpar_description", new=AsyncMock(return_value="")):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="my-agent")
        )
    assert token == f"[hmc-mcp owner:my-agent created:{today}]"
    # token must pass the existing description validator
    from hmc_mcp.ssh import validate_lpar_description
    validate_lpar_description(token)  # no exception
