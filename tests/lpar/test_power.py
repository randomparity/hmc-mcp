"""Tests for managed-system, VIOS, and LPAR power jobs."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import JOB_ENTRY, make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.jobs import (
    BOOT_MODES,
    POWER_ON_OPERATION_TYPES,
    power_off_system_job,
    power_off_vios_job,
    power_on_lpar_job,
    power_on_system_job,
    power_on_vios_job,
)
from hmc_mcp.operations.lpar.core import LparPowerResult, power_lpar, power_on_lpar

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
PROFILE_UUID = "00000000-0000-0000-0000-0000000000aa"

# Captured from the builder before this change, so the byte-identity promise in
# ADR 0161 is pinned against a recorded value rather than the current code.
POWER_ON_LPAR_DEFAULT_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<JobRequest xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"'
    ' xmlns:JobRequest="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"'
    ' schemaVersion="V1_0">\n'
    "  <Metadata><Atom/></Metadata>\n"
    '  <RequestedOperation kb="CUR" kxe="false" schemaVersion="V1_0">\n'
    "    <Metadata><Atom/></Metadata>\n"
    '    <OperationName kb="ROR" kxe="false">PowerOn</OperationName>\n'
    '    <GroupName kb="ROR" kxe="false">LogicalPartition</GroupName>\n'
    '    <ProgressType kb="ROR" kxe="false">DISCRETE</ProgressType>\n'
    "  </RequestedOperation>\n"
    '  <JobParameters kb="CUR" kxe="false" schemaVersion="V1_0">\n'
    "    <Metadata><Atom/></Metadata>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">force</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">false</ParameterValue>\n'
    "    </JobParameter>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">novsi</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">true</ParameterValue>\n'
    "    </JobParameter>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">bootmode</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">norm</ParameterValue>\n'
    "    </JobParameter>\n"
    "  </JobParameters>\n"
    "</JobRequest>\n"
)


def test_system_power_jobs():
    assert "PowerOn" in power_on_system_job() and "ManagedSystem" in power_on_system_job()
    assert "PowerOff" in power_off_system_job() and "ManagedSystem" in power_off_system_job()


def test_vios_power_jobs():
    on = power_on_vios_job()
    off = power_off_vios_job()
    assert "PowerOn" in on and "VirtualIOServer" in on
    assert "PowerOff" in off and "VirtualIOServer" in off


def test_power_on_lpar_job_default_document_is_unchanged():
    """A call passing no activation argument emits today's document exactly."""
    assert power_on_lpar_job() == POWER_ON_LPAR_DEFAULT_DOCUMENT


def _parameter_values(document: str, name: str) -> list[str]:
    """Every ParameterValue whose ParameterName is *name*, in document order."""
    values = []
    blocks = document.split('<JobParameter schemaVersion="V1_0">')[1:]
    for block in blocks:
        parameter = block.split('<ParameterName kb="ROR" kxe="false">')[1]
        parameter_name, _, rest = parameter.partition("</ParameterName>")
        if parameter_name == name:
            value = rest.split('<ParameterValue kb="CUR" kxe="false">')[1]
            values.append(value.partition("</ParameterValue>")[0])
    return values


def test_power_on_lpar_job_emits_optional_parameters_when_supplied():
    """LogicalPartitionProfile and OperationType appear only when asked for."""
    supplied = power_on_lpar_job(
        profile_uuid=PROFILE_UUID, bootmode="sms", operation_type="activate"
    )
    assert _parameter_values(supplied, "LogicalPartitionProfile") == [PROFILE_UUID]
    assert _parameter_values(supplied, "OperationType") == ["activate"]
    assert _parameter_values(supplied, "bootmode") == ["sms"]

    for omitted in (power_on_lpar_job(), power_on_lpar_job(profile_uuid="")):
        assert _parameter_values(omitted, "LogicalPartitionProfile") == []
        assert _parameter_values(omitted, "OperationType") == []
        assert _parameter_values(omitted, "bootmode") == ["norm"]


@pytest.mark.parametrize("boot_mode", sorted(BOOT_MODES))
def test_power_on_lpar_job_accepts_every_boot_mode(boot_mode):
    """Every documented boot mode reaches the document unaltered."""
    assert _parameter_values(power_on_lpar_job(bootmode=boot_mode), "bootmode") == [
        boot_mode
    ]


@pytest.mark.parametrize(
    ("kwargs", "permitted"),
    [
        ({"bootmode": "warp"}, BOOT_MODES),
        ({"operation_type": "netboot"}, POWER_ON_OPERATION_TYPES),
        ({"operation_type": ""}, POWER_ON_OPERATION_TYPES),
    ],
)
def test_power_on_lpar_job_rejects_unknown_vocabulary(kwargs, permitted):
    """A non-member is refused before XML exists, naming the sorted permitted set."""
    with pytest.raises(ValueError) as rejected:
        power_on_lpar_job(**kwargs)
    assert ", ".join(sorted(permitted)) in str(rejected.value)


@pytest.mark.asyncio
async def test_power_on_system(mock_hmc):
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        job = await hmc.power_on_system(SYSTEM_UUID)
    assert route.called
    assert job is not None


@pytest.mark.asyncio
async def test_power_off_system(mock_hmc):
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_off_system(SYSTEM_UUID, immediate=True)
    body = route.calls.last.request.content.decode()
    assert "PowerOff" in body and "immediate" in body


@pytest.mark.asyncio
async def test_power_on_vios(mock_hmc):
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_on_vios(VIOS_UUID)
    assert route.called


@pytest.mark.asyncio
async def test_power_off_vios(mock_hmc):
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_off_vios(VIOS_UUID)
    assert route.called


# --------------------------------------------------------------------------- #
# The activation parameters reaching the job document through power_lpar
# --------------------------------------------------------------------------- #

LPAR_UUID = "11111111-1111-1111-1111-111111111111"


def _power_client() -> AsyncMock:
    """A client double whose config is real, so the ADR 0092 guard stays off.

    ``AsyncMock().config.authorize_power_operations`` is a truthy child mock,
    which would silently enable the ownership guard and change the call path.
    """
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping(
        {"host": "hmc.test", "user": "u", "password": "p"}
    )
    hmc.get_quick_property.return_value = "not activated"
    hmc.submit_job.return_value = {"UUID": "job-uuid"}
    return hmc


@pytest.mark.asyncio
async def test_power_lpar_forwards_activation_parameters():
    """PowerOn carries the caller's profile, boot mode and operation type."""
    hmc = _power_client()

    with patch(
        "hmc_mcp.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=True,
            boot_mode="sms",
            partition_profile_uuid=PROFILE_UUID,
            operation_type="activate",
        )

    _, document = hmc.submit_job.await_args.args
    assert _parameter_values(document, "bootmode") == ["sms"]
    assert _parameter_values(document, "LogicalPartitionProfile") == [PROFILE_UUID]
    assert _parameter_values(document, "OperationType") == ["activate"]


@pytest.mark.asyncio
async def test_power_lpar_power_off_document_is_unchanged():
    """The PowerOff arm builds a different document and takes none of the three."""
    hmc = _power_client()

    with patch(
        "hmc_mcp.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=False,
            boot_mode="sms",
            partition_profile_uuid=PROFILE_UUID,
            operation_type="activate",
        )

    path, document = hmc.submit_job.await_args.args
    assert path.endswith("/do/PowerOff")
    for name in ("bootmode", "LogicalPartitionProfile", "OperationType"):
        assert _parameter_values(document, name) == []


@pytest.mark.asyncio
async def test_power_on_lpar_passes_activation_parameters():
    """power_on_lpar hands all three straight to the shared power entry point."""
    hmc = _power_client()
    forwarded = AsyncMock(return_value=LparPowerResult(LPAR_UUID, {"UUID": "job-uuid"}))

    with patch("hmc_mcp.operations.lpar.core.power_lpar", new=forwarded):
        await power_on_lpar(
            hmc,
            LPAR_UUID,
            boot_mode="of",
            partition_profile_uuid=PROFILE_UUID,
            operation_type="activate",
        )

    assert forwarded.await_args.kwargs["boot_mode"] == "of"
    assert forwarded.await_args.kwargs["partition_profile_uuid"] == PROFILE_UUID
    assert forwarded.await_args.kwargs["operation_type"] == "activate"


@pytest.mark.asyncio
async def test_power_lpar_already_running_names_the_dropped_activation_parameters():
    """A running partition drops the activation request, so the message says so.

    "Boot this partition into SMS" is usually asked about a running partition,
    and a bare already-running message reads as success to a caller whose
    request was never attempted.
    """
    hmc = _power_client()
    hmc.get_quick_property.return_value = "running"

    with patch(
        "hmc_mcp.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        requested = await power_lpar(
            hmc, None, LPAR_UUID, power_on=True, boot_mode="sms"
        )
        plain = await power_lpar(hmc, None, LPAR_UUID, power_on=True)

    hmc.submit_job.assert_not_awaited()
    assert requested.job["already_running"] is True
    assert "were not applied" in requested.job["message"]
    # An ordinary already-running call says exactly what it always said.
    assert "were not applied" not in plain.job["message"]
