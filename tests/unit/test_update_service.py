"""Direct contracts for update-service version and VIOS-result handling."""

from __future__ import annotations

import pytest

from hmc_mcp.operations.updates.service import (
    _require_platform_update_version,
    _with_vios_stdout,
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
