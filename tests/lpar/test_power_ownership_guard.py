"""ADR 0092 §4: the opt-in ADR 0011 ownership guard on ``power_lpar`` (#371).

The guard is a configuration switch, so both settings are contract. The
off-by-default arm asserts the *absence* of work — no SSH connection, no extra
REST read — because that absence is the reason ADR 0092 chose the default.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from hmc_mcp import audit, cli_lpars, operations_provision, server_lpars
from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_lpar import power_lpar
from hmc_mcp.ssh import HMCCLIError

LPAR_UUID = "11111111-1111-1111-1111-111111111111"
SYSTEM_UUID = "22222222-2222-2222-2222-222222222222"
OWNED_BY_ALICE = "[hmc-mcp owner:alice created:2026-08-14]"
OWNED_BY_BOB = "[hmc-mcp owner:bob created:2026-08-14]"


def _hmc(*, authorize: bool, agent_id: str = "alice") -> AsyncMock:
    """An HMC client double carrying a real :class:`HMCConfig`.

    The config must be real: ``AsyncMock().config.authorize_power_operations``
    is a truthy child mock, which would silently enable the guard everywhere.
    """
    hmc = AsyncMock()
    hmc.config = HMCConfig(
        host="hmc.test",
        user="u",
        password="p",
        agent_id=agent_id,
        authorize_power_operations=authorize,
        _env_file=None,
    )
    hmc.get_managed_system.return_value = {"Resource": {"SystemName": "sys1"}}
    hmc.get_logical_partition.return_value = {"Resource": {"PartitionName": "aix1"}}
    hmc.submit_job.return_value = {"UUID": "job-uuid"}
    return hmc


@asynccontextmanager
async def _factory_for(hmc):
    yield hmc


def _client_factory(hmc):
    def factory(_profile=None):
        return _factory_for(hmc)

    return factory


# ---------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------


def test_authorize_power_operations_defaults_off() -> None:
    assert HMCConfig(_env_file=None).authorize_power_operations is False


def test_authorize_power_operations_reads_its_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("HMC_AUTHORIZE_POWER_OPERATIONS", "true")
    assert HMCConfig(_env_file=None).authorize_power_operations is True


# ---------------------------------------------------------------------------
# Setting off: the call path is what it was, and it opens no SSH connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_guard_opens_no_ssh_connection_and_reads_no_ownership() -> None:
    hmc = _hmc(authorize=False)
    connect = AsyncMock(side_effect=AssertionError("opened an SSH connection"))

    with patch("hmc_mcp.ssh.asyncssh.connect", new=connect):
        result = await power_lpar(
            hmc,
            LPAR_UUID,
            power_on=False,
            system_name_or_uuid=SYSTEM_UUID,
        )

    assert result.job == {"UUID": "job-uuid"}
    connect.assert_not_called()
    hmc.get_managed_system.assert_not_awaited()
    hmc.get_logical_partition.assert_not_awaited()
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_guard_powers_a_partition_owned_by_another_agent() -> None:
    """Ownership stays advisory by default — that is the ADR 0092 §4 decision."""
    hmc = _hmc(authorize=False, agent_id="alice")

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=OWNED_BY_BOB),
    ) as read:
        await power_lpar(hmc, LPAR_UUID, power_on=False)

    read.assert_not_awaited()
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_guard_needs_no_managed_system_selector() -> None:
    hmc = _hmc(authorize=False)

    await power_lpar(hmc, LPAR_UUID, power_on=False)

    hmc.submit_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# Setting on: the guard runs before the job is submitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_guard_refuses_a_partition_another_agent_owns() -> None:
    hmc = _hmc(authorize=True, agent_id="alice")

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=OWNED_BY_BOB),
    ):
        with pytest.raises(PermissionError, match="ownership_override=true"):
            await power_lpar(
                hmc,
                LPAR_UUID,
                power_on=False,
                system_name_or_uuid=SYSTEM_UUID,
            )

    hmc.submit_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_guard_powers_a_partition_this_agent_owns() -> None:
    hmc = _hmc(authorize=True, agent_id="alice")

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=OWNED_BY_ALICE),
    ) as read:
        result = await power_lpar(
            hmc,
            LPAR_UUID,
            power_on=False,
            system_name_or_uuid=SYSTEM_UUID,
        )

    assert result.job == {"UUID": "job-uuid"}
    read.assert_awaited_once_with(hmc.config, "sys1", "aix1")
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_enabled_guard_runs_before_the_already_running_short_circuit() -> None:
    """A foreign-owned partition is refused, not reported as already running."""
    hmc = _hmc(authorize=True, agent_id="alice")
    hmc.get_quick_property.return_value = "running"

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=OWNED_BY_BOB),
    ):
        with pytest.raises(PermissionError):
            await power_lpar(
                hmc,
                LPAR_UUID,
                power_on=True,
                system_name_or_uuid=SYSTEM_UUID,
            )

    hmc.get_quick_property.assert_not_awaited()


@pytest.mark.asyncio
async def test_ownership_override_submits_the_job_and_is_audited(caplog) -> None:
    hmc = _hmc(authorize=True, agent_id="alice")

    with (
        patch(
            "hmc_mcp.operations_lpar.get_lpar_description",
            new=AsyncMock(return_value=OWNED_BY_BOB),
        ) as read,
        caplog.at_level(logging.WARNING),
    ):
        result = await power_lpar(
            hmc,
            LPAR_UUID,
            power_on=False,
            system_name_or_uuid=SYSTEM_UUID,
            ownership_override=True,
        )

    assert result.job == {"UUID": "job-uuid"}
    read.assert_not_awaited()
    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == audit.AUDIT_LOGGER_NAME
    ]
    assert len(records) == 1, "an absence assertion over an empty capture proves nothing"
    assert records[0]["event"] == "ownership-override"
    assert (records[0]["system"], records[0]["lpar"]) == ("sys1", "aix1")


@pytest.mark.asyncio
async def test_enabled_guard_fails_closed_when_the_ownership_read_fails() -> None:
    """SSH is a hard dependency once the guard is on — and it fails closed."""
    hmc = _hmc(authorize=True)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(side_effect=HMCCLIError("SSH command timed out after 300s")),
    ):
        with pytest.raises(HMCCLIError, match="timed out"):
            await power_lpar(
                hmc,
                LPAR_UUID,
                power_on=False,
                system_name_or_uuid=SYSTEM_UUID,
            )

    hmc.submit_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_guard_resolves_the_managed_system_once() -> None:
    """The named selector is resolved once and scopes the partition lookup.

    Resolving it again inside the guard would buy a second REST GET, and it
    would let a partition named by name resolve outside the system whose
    ownership token is then read.
    """
    hmc = _hmc(authorize=True, agent_id="alice")
    hmc.find_system_by_name.return_value = {"UUID": SYSTEM_UUID}
    hmc.find_partition_by_name.return_value = {"UUID": LPAR_UUID}

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=OWNED_BY_ALICE),
    ):
        await power_lpar(
            hmc,
            "aix1",
            power_on=False,
            system_name_or_uuid="sys1",
        )

    hmc.find_system_by_name.assert_awaited_once_with("sys1")
    hmc.find_partition_by_name.assert_awaited_once_with("aix1", system_uuid=SYSTEM_UUID)


@pytest.mark.asyncio
async def test_enabled_guard_refuses_without_a_managed_system_selector() -> None:
    hmc = _hmc(authorize=True)

    with pytest.raises(ValueError, match="system_name_or_uuid is required"):
        await power_lpar(hmc, LPAR_UUID, power_on=False)

    hmc.submit_job.assert_not_awaited()
    hmc.get_logical_partition.assert_not_awaited()
    hmc.get_managed_system.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_selector_is_refused_even_with_an_override() -> None:
    """The override waives ownership, not the guard's need to identify the token."""
    hmc = _hmc(authorize=True)

    with pytest.raises(ValueError, match="system_name_or_uuid is required"):
        await power_lpar(
            hmc,
            LPAR_UUID,
            power_on=False,
            ownership_override=True,
        )

    hmc.submit_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# Callers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_activation_leg_overrides_its_own_stamp() -> None:
    """ADR 0092 Consequences: the leg authorizes a partition it just stamped."""
    hmc = _hmc(authorize=True)
    operation = AsyncMock(return_value=AsyncMock(job={"UUID": "job-uuid"}))

    with patch.object(operations_provision, "power_lpar", operation):
        await operations_provision._power_on(hmc, "sys1", LPAR_UUID, None)

    assert operation.await_args.kwargs["ownership_override"] is True
    assert operation.await_args.kwargs["system_name_or_uuid"] == "sys1"


@pytest.mark.parametrize(
    "tool",
    [server_lpars.hmc_power_on_lpar, server_lpars.hmc_power_off_lpar],
)
def test_power_tools_forward_the_ownership_override(monkeypatch, tool) -> None:
    hmc = _hmc(authorize=True)
    operation = AsyncMock(return_value=AsyncMock(job={"UUID": "job-uuid"}))
    monkeypatch.setattr(server_lpars, "client_from_env", _client_factory(hmc))
    monkeypatch.setattr(server_lpars, "power_lpar", operation)

    tool("aix1", system_name_or_uuid="sys1", ownership_override=True)

    assert operation.await_args.kwargs["ownership_override"] is True
    assert operation.await_args.kwargs["system_name_or_uuid"] == "sys1"


@pytest.mark.parametrize(
    ("command", "power_on"),
    [("power-on", True), ("power-off", False)],
)
def test_power_cli_forwards_the_system_selector_and_override(
    monkeypatch, command, power_on
) -> None:
    hmc = _hmc(authorize=True)
    operation = AsyncMock(return_value=AsyncMock(lpar_uuid=LPAR_UUID, job=None))
    monkeypatch.setattr(cli_lpars, "_client", lambda: _factory_for(hmc))
    monkeypatch.setattr(cli_lpars, "power_lpar", operation)

    result = CliRunner().invoke(
        cli_lpars.lpars_app,
        [command, "aix1", "--system", "sys1", "--ownership-override", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert operation.await_args.kwargs["ownership_override"] is True
    assert operation.await_args.kwargs["system_name_or_uuid"] == "sys1"
    assert operation.await_args.kwargs["power_on"] is power_on
