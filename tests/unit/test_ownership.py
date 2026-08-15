"""Tests for multi-agent LPAR ownership helpers (issue #132)."""

from __future__ import annotations

import asyncio
import datetime
import logging
from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.config import validate_agent_id
from hmc_mcp.operations_lpar import authorize_lpar_mutation


# ---------------------------------------------------------------------------
# validate_agent_id
# ---------------------------------------------------------------------------


def test_validate_agent_id_valid():
    validate_agent_id("alice")  # plain name
    validate_agent_id("agent-1")  # hyphens ok
    validate_agent_id("agent.1")  # dots ok
    validate_agent_id("a" * 64)  # max length


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
    with pytest.raises(ValueError, match="HMC REST API"):
        validate_agent_id("team/agent")


def test_validate_agent_id_colon():
    with pytest.raises(ValueError, match="colon"):
        validate_agent_id("team:agent")


def test_validate_agent_id_space():
    with pytest.raises(ValueError, match="space"):
        validate_agent_id("alice smith")


def test_validate_agent_id_non_ascii():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alicé")


def test_validate_agent_id_control_char():
    with pytest.raises(ValueError, match="printable ASCII"):
        validate_agent_id("alice\n")


# ---------------------------------------------------------------------------
# stamp_lpar_ownership
# ---------------------------------------------------------------------------


from hmc_mcp.ssh_commands import stamp_lpar_ownership  # noqa: E402 (after validate tests)
from hmc_mcp.config import HMCConfig  # noqa: E402


def _config():
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def test_stamp_returns_token_on_success():
    config = _config()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
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
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1")  # no agent_id
        )
    assert token is not None
    assert "owner:hmc-mcp" in token


def test_stamp_returns_none_on_ssh_error():
    config = _config()
    from hmc_mcp.ssh_commands import HMCCLIError

    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description",
        new=AsyncMock(side_effect=HMCCLIError("SSH failed")),
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="alice")
        )
    assert token is None  # swallowed — best-effort


def test_token_format():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(
            stamp_lpar_ownership(config, "sys1", "lpar1", agent_id="my-agent")
        )
    assert token == f"[hmc-mcp owner:my-agent created:{today}]"
    # token must pass the existing description validator
    from hmc_mcp.ssh_commands import validate_lpar_description

    validate_lpar_description(token)  # no exception


@pytest.mark.parametrize(
    ("description", "agent_id", "allowed"),
    [
        ("legacy partition", "alice", True),
        ("[hmc-mcp owner:alice created:2026-08-14]", "alice", True),
        ("[hmc-mcp owner:bob created:2026-08-14]", "alice", False),
        ("[hmc-mcp owner:broken]", "alice", False),
    ],
)
def test_authorize_lpar_mutation(description, agent_id, allowed):
    hmc = type(
        "StubHMC", (), {"config": _config().model_copy(update={"agent_id": agent_id})}
    )()
    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=description),
    ):
        if allowed:
            asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))
        else:
            with pytest.raises(PermissionError, match="ownership_override=true"):
                asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))


def test_authorize_lpar_mutation_returns_owner_from_authorizing_read():
    hmc = type(
        "StubHMC", (), {"config": _config().model_copy(update={"agent_id": "alice"})}
    )()
    read = AsyncMock(return_value="[hmc-mcp owner:alice created:2026-08-14]")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        owner = asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))

    assert owner == "alice"
    read.assert_awaited_once_with(hmc.config, "sys1", "lpar1")


def test_authorize_lpar_mutation_override_is_audited(caplog):
    hmc = type("StubHMC", (), {"config": _config()})()
    read = AsyncMock(return_value="[hmc-mcp owner:bob created:2026-08-14]")
    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=read),
        caplog.at_level(logging.WARNING, logger="hmc_mcp.operations_lpar"),
    ):
        owner = asyncio.run(
            authorize_lpar_mutation(hmc, "sys1", "lpar1", ownership_override=True)
        )
    assert owner == "bob"
    read.assert_awaited_once_with(hmc.config, "sys1", "lpar1")
    record = caplog.records[-1]
    assert record.getMessage() == "LPAR ownership override approved"
    assert record.hmc_system == "sys1"
    assert record.hmc_lpar == "lpar1"
    assert record.hmc_agent_id == "hmc-mcp"


def test_authorize_lpar_mutation_normal_access_has_no_override_audit(caplog):
    hmc = type("StubHMC", (), {"config": _config()})()
    with (
        patch(
            "hmc_mcp.operations_lpar.get_lpar_description",
            new=AsyncMock(return_value="legacy partition"),
        ),
        caplog.at_level(logging.WARNING, logger="hmc_mcp.operations_lpar"),
    ):
        asyncio.run(authorize_lpar_mutation(hmc, "sys1", "lpar1"))

    assert caplog.records == []
