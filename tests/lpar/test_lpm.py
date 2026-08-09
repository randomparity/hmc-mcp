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
    restart = remote_restart_lpar_job("tgt")
    assert "RemoteRestart" in restart and "TargetManagedSystemName" in restart


# -- client methods ------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lpar_migrate(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/Migrate"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
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
        await hmc.lpar_remote_restart("lpar-uuid", "tgt")
    body = route.calls.last.request.content.decode()
    assert "RemoteRestart" in body and "tgt" in body
