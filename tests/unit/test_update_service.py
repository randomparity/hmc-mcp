"""Direct contracts for update-service version and VIOS-result handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest

from hmcpctl.operations.updates.service import (
    _require_platform_update_version,
    _with_vios_stdout,
    submit_available_hmc_ptfs_query,
    update_console_software,
)


def test_platform_update_requires_documented_console_version():
    _require_platform_update_version({"Resource": {"VersionInfo": "V11R1M1111"}})

    with pytest.raises(ValueError, match="HMC 11.1.1111"):
        _require_platform_update_version({"Resource": {"VersionInfo": "V10R2M9999"}})


def test_vios_completed_wait_result_projects_stdout_without_mutating_payload():
    job = {
        "Resource": {
            "Status": "COMPLETED",
            "Results": {
                "JobParameter": {"ParameterName": "stdOut", "ParameterValue": " ok "}
            },
        }
    }

    result = _with_vios_stdout(job, wait=True)

    assert result is not job
    assert result["stdOut"] == "ok"
    assert "stdOut" not in job


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "args"),
    [
        (update_console_software, ({"MediaType": "IBMWebsite"},)),
        (submit_available_hmc_ptfs_query, ()),
    ],
)
@pytest.mark.parametrize("length", [257, 20_000])
async def test_console_update_identifier_refused_before_submission(operation, args, length):
    client = SimpleNamespace(submit_job=AsyncMock(return_value=None))
    value = "B" * length
    with pytest.raises(ValueError) as exc:
        await operation(client, value, *args)
    message = str(exc.value)
    assert "console_uuid" in message and str(length) in message
    assert "256" in message and value not in message
    client.submit_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "args", "suffix"),
    [
        (update_console_software, ({"MediaType": "IBMWebsite"},), "UpdateManagementConsole"),
        (submit_available_hmc_ptfs_query, (), "ListManagementConsoleUpdates"),
    ],
)
async def test_console_update_unicode_boundary_preserves_job_path(operation, args, suffix):
    client = SimpleNamespace(submit_job=AsyncMock(return_value=None))
    value = "\U0001f600" * 256
    await operation(client, value, *args)
    assert client.submit_job.await_count == 1
    assert client.submit_job.await_args.args[0] == (
        f"/rest/api/uom/ManagementConsole/{quote(value, safe='')}/do/{suffix}"
    )
