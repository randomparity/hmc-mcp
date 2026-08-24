"""Tests for Live Partition Mobility job builders + client methods."""

import httpx
import pytest

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import (
    migrate_abort_lpar_job,
    migrate_lpar_job,
    migrate_recover_lpar_job,
    migrate_validate_lpar_job,
    remote_restart_lpar_job,
)

from conftest import JOB_ENTRY, make_config


# -- job XML builders ---------------------------------------------------- #


def test_migrate_job():
    xml = migrate_lpar_job("vrml12-fsp")
    assert "Migrate" in xml
    assert "LogicalPartition" in xml
    assert "TargetManagedSystemName" in xml and "vrml12-fsp" in xml


def test_migrate_job_optional_params():
    xml = migrate_lpar_job("tgt", target_profile_name="prof1", wait_time=60)
    assert "TargetProfileName" in xml and "prof1" in xml
    assert "WaitTime" in xml and "60" in xml


def test_migrate_validate_job():
    xml = migrate_validate_lpar_job("tgt")
    assert "MigrateValidate" in xml
    assert "TargetManagedSystemName" in xml and "tgt" in xml


def test_migrate_abort_recover_restart_jobs():
    assert "MigrateAbort" in migrate_abort_lpar_job()
    assert "MigrateRecover" in migrate_recover_lpar_job()
    restart = remote_restart_lpar_job(
        "restart", "src", "lpar-uuid", target_managed_system="tgt"
    )
    assert "RemoteRestart" in restart and "targetManagedSystem" in restart
    assert "TargetManagedSystemName" not in restart


def test_remote_restart_cleanup_allows_no_target_and_retain_devices():
    xml = remote_restart_lpar_job("cleanup", "src", "lpar-uuid", retain_devices=True)
    assert "cleanup" in xml and "retaindev" in xml
    assert "targetManagedSystem" not in xml


@pytest.mark.parametrize(
    ("operation", "kwargs", "message"),
    [
        ("validate", {}, "requires a target"),
        ("recover", {"use_current_data": True}, "requires a target"),
        (
            "validate",
            {"target_managed_system": "tgt", "use_current_data": True},
            "restart",
        ),
        (
            "restart",
            {"target_managed_system": "tgt", "retain_devices": True},
            "cleanup",
        ),
    ],
)
def test_remote_restart_rejects_invalid_parameter_combinations(
    operation, kwargs, message
):
    with pytest.raises(ValueError, match=message):
        remote_restart_lpar_job(operation, "src", "lpar-uuid", **kwargs)


def test_remote_restart_preserves_target_uuid_parameter():
    xml = remote_restart_lpar_job(
        "restart",
        "src",
        "lpar-uuid",
        target_managed_system_uuid="00000000-0000-0000-0000-000000000001",
    )
    assert "targetManagedSystemUUID" in xml
    assert "targetManagedSystem</ParameterName>" not in xml


def test_remote_restart_rejects_unknown_runtime_operation():
    with pytest.raises(ValueError, match="operation must be one of"):
        remote_restart_lpar_job(
            "reboot",  # type: ignore[arg-type]
            "src",
            "lpar-uuid",
            target_managed_system="tgt",
        )


# -- client methods ------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lpar_migrate(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/LogicalPartition/lpar-uuid/do/Migrate").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        job = await hmc.lpar_migrate("lpar-uuid", "vrml12-fsp")
    body = route.calls.last.request.content.decode()
    assert "Migrate" in body and "vrml12-fsp" in body
    assert job is not None and job["Resource"]["JobID"] == "job-uuid-999"


@pytest.mark.asyncio
async def test_lpar_migrate_validate(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/MigrateValidate"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.lpar_migrate_validate("lpar-uuid", "tgt")
    assert "MigrateValidate" in route.calls.last.request.content.decode()


@pytest.mark.asyncio
async def test_lpar_migrate_abort(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/MigrateAbort"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.lpar_migrate_abort("lpar-uuid")
    assert "MigrateAbort" in route.calls.last.request.content.decode()


@pytest.mark.asyncio
async def test_lpar_migrate_recover(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/MigrateRecover"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.lpar_migrate_recover("lpar-uuid")
    assert "MigrateRecover" in route.calls.last.request.content.decode()


@pytest.mark.asyncio
async def test_lpar_remote_restart(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/RemoteRestart"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.lpar_remote_restart(
            "lpar-uuid", "restart", "src", target_managed_system="tgt"
        )
    body = route.calls.last.request.content.decode()
    assert "RemoteRestart" in body and "tgt" in body
