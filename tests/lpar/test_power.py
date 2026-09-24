"""Tests for managed-system, VIOS, and LPAR power jobs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import JOB_ENTRY, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.config import HMCConfig
from hmcpctl.errors import HMCError
from hmcpctl.jobs import (
    BOOT_MODES,
    POWER_OFF_OPERATIONS,
    POWER_ON_OPERATION_TYPES,
    power_off_lpar_job,
    power_off_system_job,
    power_off_vios_job,
    power_on_lpar_job,
    power_on_system_job,
    power_on_vios_job,
)
from hmcpctl.operations.lpar import decommission
from hmcpctl.operations.lpar.core import (
    LparPowerResult,
    _unapplied_activation_clause,
    power_lpar,
    power_on_lpar,
)

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

# Captured from the builder before this change, so ADR 0164's byte-identity
# promise is pinned against a recorded value rather than the current code.
POWER_OFF_LPAR_DEFAULT_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<JobRequest xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"'
    ' xmlns:JobRequest="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"'
    ' schemaVersion="V1_0">\n'
    "  <Metadata><Atom/></Metadata>\n"
    '  <RequestedOperation kb="CUR" kxe="false" schemaVersion="V1_0">\n'
    "    <Metadata><Atom/></Metadata>\n"
    '    <OperationName kb="ROR" kxe="false">PowerOff</OperationName>\n'
    '    <GroupName kb="ROR" kxe="false">LogicalPartition</GroupName>\n'
    '    <ProgressType kb="ROR" kxe="false">DISCRETE</ProgressType>\n'
    "  </RequestedOperation>\n"
    '  <JobParameters kb="CUR" kxe="false" schemaVersion="V1_0">\n'
    "    <Metadata><Atom/></Metadata>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">immediate</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">false</ParameterValue>\n'
    "    </JobParameter>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">restart</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">false</ParameterValue>\n'
    "    </JobParameter>\n"
    '    <JobParameter schemaVersion="V1_0">\n'
    "      <Metadata><Atom/></Metadata>\n"
    '      <ParameterName kb="ROR" kxe="false">operation</ParameterName>\n'
    '      <ParameterValue kb="CUR" kxe="false">shutdown</ParameterValue>\n'
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


def test_power_off_lpar_job_default_document_is_unchanged():
    """A call passing no new argument emits today's document exactly."""
    assert power_off_lpar_job() == POWER_OFF_LPAR_DEFAULT_DOCUMENT


@pytest.mark.parametrize(
    ("immediate", "restart"),
    [(False, False), (True, False), (False, True)],
)
def test_power_off_lpar_job_emits_restart_and_operation(immediate, restart):
    """All three parameters are emitted on every call, in the document's order."""
    document = power_off_lpar_job(immediate=immediate, restart=restart)
    assert _parameter_values(document, "immediate") == ["true" if immediate else "false"]
    assert _parameter_values(document, "restart") == ["true" if restart else "false"]
    assert _parameter_values(document, "operation") == ["shutdown"]
    assert _parameter_values(
        power_off_lpar_job(operation="osshutdown"), "operation"
    ) == ["osshutdown"]


@pytest.mark.parametrize("operation", ["dumpretry", "", "reboot", "SHUTDOWN"])
def test_power_off_lpar_job_rejects_unknown_vocabulary(operation):
    """A non-member is refused before XML exists, naming the sorted permitted set."""
    with pytest.raises(ValueError) as rejected:
        power_off_lpar_job(operation=operation)
    assert ", ".join(sorted(POWER_OFF_OPERATIONS)) in str(rejected.value)


def test_power_off_lpar_job_gates_dumprestart_behind_the_opt_in():
    """dumprestart crashes the partition, so it is refused without the opt-in."""
    with pytest.raises(ValueError) as refused:
        power_off_lpar_job(operation="dumprestart")
    assert "allow_dump_restart" in str(refused.value)

    permitted = power_off_lpar_job(operation="dumprestart", allow_dump_restart=True)
    assert _parameter_values(permitted, "operation") == ["dumprestart"]


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
    # ADR 0039 containment: the target partition contains PROFILE_UUID, so a
    # call naming it passes the check rather than tripping it.
    hmc.list_child.return_value = [{"UUID": PROFILE_UUID}]
    return hmc


@pytest.mark.asyncio
async def test_power_lpar_forwards_activation_parameters():
    """PowerOn carries the caller's profile, boot mode and operation type."""
    hmc = _power_client()

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
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
async def test_power_lpar_forwards_power_off_parameters():
    """PowerOff carries the caller's restart flag and shutdown operation."""
    hmc = _power_client()

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=False,
            restart=True,
            operation="osshutdown",
        )

    path, document = hmc.submit_job.await_args.args
    assert path.endswith("/do/PowerOff")
    assert _parameter_values(document, "restart") == ["true"]
    assert _parameter_values(document, "operation") == ["osshutdown"]


@pytest.mark.asyncio
async def test_power_lpar_power_on_document_ignores_power_off_parameters():
    """The PowerOn arm builds a different document and takes none of the three."""
    hmc = _power_client()

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=True,
            force=True,
            restart=True,
            operation="dumprestart",
            allow_dump_restart=True,
        )

    path, document = hmc.submit_job.await_args.args
    assert path.endswith("/do/PowerOn")
    assert _parameter_values(document, "restart") == []
    assert _parameter_values(document, "operation") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"operation": "dumpretry"}, "dumprestart, osshutdown, shutdown"),
        ({"operation": "dumprestart"}, "allow_dump_restart"),
    ],
)
async def test_power_lpar_refuses_before_any_side_effect(kwargs, expected):
    """A refused PowerOff reads nothing, submits nothing and audits nothing."""
    hmc = _power_client()
    resolver = AsyncMock(return_value=LPAR_UUID)

    with (
        patch("hmcpctl.operations.lpar.core.resolve_lpar_uuid", new=resolver),
        pytest.raises(ValueError) as refused,
    ):
        await power_lpar(hmc, None, LPAR_UUID, power_on=False, **kwargs)

    assert expected in str(refused.value)
    resolver.assert_not_awaited()
    hmc.submit_job.assert_not_awaited()
    hmc.get_quick_property.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("immediate", [False, True])
async def test_decommission_power_off_document_is_unchanged(immediate):
    """The decommission path shares the builder, so its defaults must not move.

    #872 widened power_off_lpar_job with restart and operation. This workflow
    passes neither, so it must keep emitting restart=false and
    operation=shutdown; epic #871 freezes this document. The private
    ``_power_off`` is called deliberately: the contract is exactly which
    document that call site builds.
    """
    hmc = _power_client()
    inventory = SimpleNamespace(
        state="running", lpar_uuid=LPAR_UUID, lpar_name="lpar-a"
    )

    with patch(
        "hmcpctl.operations.lpar.decommission.wait_for_submitted_job",
        new=AsyncMock(
            return_value={
                "UUID": "job-uuid",
                "Resource": {"JobID": "job-uuid", "Status": "COMPLETED_OK"},
            }
        ),
    ):
        await decommission._power_off(
            hmc,
            inventory,
            immediate=immediate,
            timeout_seconds=30,
            poll_interval=1,
        )

    path, document = hmc.submit_job.await_args.args
    assert path.endswith("/do/PowerOff")
    assert _parameter_values(document, "immediate") == [
        "true" if immediate else "false"
    ]
    assert _parameter_values(document, "restart") == ["false"]
    assert _parameter_values(document, "operation") == ["shutdown"]


@pytest.mark.asyncio
async def test_power_lpar_power_off_document_is_unchanged():
    """The PowerOff arm builds a different document and takes none of the three."""
    hmc = _power_client()

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
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

    with patch("hmcpctl.operations.lpar.core.power_lpar", new=forwarded):
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
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        requested = await power_lpar(
            hmc, None, LPAR_UUID, power_on=True, boot_mode="sms"
        )
        plain = await power_lpar(hmc, None, LPAR_UUID, power_on=True)

    hmc.submit_job.assert_not_awaited()
    assert requested.job["already_running"] is True
    # Only the parameter actually supplied is named, and the force=True advice
    # is not repeated as a way to apply it.
    assert "The requested boot mode was not applied" in requested.job["message"]
    for unsupplied in ("partition profile", "operation type"):
        assert unsupplied not in requested.job["message"]
    assert requested.job["message"].count("force=True") == 1
    # An ordinary already-running call says exactly what it always said.
    assert "not applied" not in plain.job["message"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ""),
        (
            {"boot_mode": "sms"},
            (
                " The requested boot mode was not applied;"
                " power the partition off first."
            ),
        ),
        (
            {"partition_profile_uuid": PROFILE_UUID, "operation_type": "activate"},
            (
                " The requested partition profile and operation type were not"
                " applied; power the partition off first."
            ),
        ),
        (
            {
                "boot_mode": "of",
                "partition_profile_uuid": PROFILE_UUID,
                "operation_type": "activate",
            },
            (
                " The requested boot mode, partition profile and operation type"
                " were not applied; power the partition off first."
            ),
        ),
    ],
)
def test_unapplied_activation_clause_names_only_what_was_supplied(kwargs, expected):
    """The clause enumerates supplied parameters only, with matching grammar."""
    assert (
        _unapplied_activation_clause(
            kwargs.get("boot_mode", "norm"),
            kwargs.get("partition_profile_uuid"),
            kwargs.get("operation_type"),
        )
        == expected
    )


# --------------------------------------------------------------------------- #
# ADR 0039 containment: a supplied profile must belong to the target partition
# --------------------------------------------------------------------------- #

OTHER_PROFILE_UUID = "00000000-0000-0000-0000-0000000000bb"


@pytest.mark.asyncio
async def test_power_lpar_accepts_a_profile_contained_by_the_target_partition():
    """A profile in the partition's own feed reaches the job document."""
    hmc = _power_client()
    hmc.list_child.return_value = [{"UUID": PROFILE_UUID}, {"UUID": OTHER_PROFILE_UUID}]

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=True,
            partition_profile_uuid=PROFILE_UUID,
        )

    hmc.list_child.assert_awaited_once_with(
        "LogicalPartition", LPAR_UUID, "LogicalPartitionProfile"
    )
    _, document = hmc.submit_job.await_args.args
    assert _parameter_values(document, "LogicalPartitionProfile") == [PROFILE_UUID]


@pytest.mark.asyncio
async def test_power_lpar_refuses_a_profile_the_target_partition_does_not_contain():
    """ADR 0039: the tool declares exhaustive targets, so a foreign profile is refused.

    Without this the declared `lpar` selector bounds nothing here: a narrow
    ``targets = {lpar = ["A"]}`` grant would still activate A against another
    partition's profile, because no target selector is minted for this argument.
    """
    hmc = _power_client()
    hmc.list_child.return_value = [{"UUID": OTHER_PROFILE_UUID}]

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ), pytest.raises(ValueError) as refused:
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=True,
            partition_profile_uuid=PROFILE_UUID,
        )

    hmc.submit_job.assert_not_awaited()
    # The rejected value is not echoed, matching the module's other refusals.
    assert PROFILE_UUID not in str(refused.value)


@pytest.mark.asyncio
async def test_power_lpar_reads_no_profile_feed_when_no_profile_is_supplied():
    """The containment read costs nothing on the ordinary PowerOn path."""
    hmc = _power_client()

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(hmc, None, LPAR_UUID, power_on=True, boot_mode="sms")

    hmc.list_child.assert_not_awaited()
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_power_lpar_matches_a_profile_uuid_case_insensitively():
    """The HMC renders UUIDs lower-case; ``is_uuid`` admits upper-case hex.

    Mirrors ``_hosts_partition`` in operations/lpar/ownership.py, the other
    place a user-supplied UUID is compared against HMC output.
    """
    hmc = _power_client()
    hmc.list_child.return_value = [{"UUID": PROFILE_UUID.lower()}]

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        await power_lpar(
            hmc,
            None,
            LPAR_UUID,
            power_on=True,
            partition_profile_uuid=PROFILE_UUID.upper(),
        )

    # The partition's own spelling reaches the wire, not the caller's: the match
    # is casefolded, so emitting the caller's string would send a value the
    # containment check never compared.
    _, document = hmc.submit_job.await_args.args
    assert _parameter_values(document, "LogicalPartitionProfile") == [
        PROFILE_UUID.lower()
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "feed", [[], [{"ResourceType": "LogicalPartitionProfile"}], [{"UUID": None}]]
)
async def test_power_lpar_distinguishes_a_degraded_profile_feed_from_a_refusal(feed):
    """An empty or unparsed feed is not evidence the profile is foreign."""
    hmc = _power_client()
    hmc.list_child.return_value = feed

    with (
        patch(
            "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
            new=AsyncMock(return_value=LPAR_UUID),
        ),
        pytest.raises(ValueError) as refused,
    ):
        await power_lpar(
            hmc, None, LPAR_UUID, power_on=True, partition_profile_uuid=PROFILE_UUID
        )

    hmc.submit_job.assert_not_awaited()
    assert "came back empty" in str(refused.value)
    assert "is not a profile of the target partition" not in str(refused.value)


@pytest.mark.asyncio
async def test_power_lpar_refuses_an_invalid_boot_mode_on_the_already_running_path():
    """Criterion 2's refusal must not be swallowed by the early return.

    The already-running branch returns without building a document, so the
    builder's own check never runs on that path.
    """
    hmc = _power_client()
    hmc.get_quick_property.return_value = "running"

    with (
        patch(
            "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
            new=AsyncMock(return_value=LPAR_UUID),
        ),
        pytest.raises(ValueError, match="PowerOn boot mode must be one of"),
    ):
        await power_lpar(hmc, None, LPAR_UUID, power_on=True, boot_mode="warp")

    hmc.submit_job.assert_not_awaited()


# --------------------------------------------------------------------------- #
# #981: a partition profile that lacks current adapters is warned about
# --------------------------------------------------------------------------- #


def _profile_power_client(profile_slots: list[str], current: dict) -> AsyncMock:
    hmc = _power_client()
    subclasses = [
        {"ProfileVirtualSCSIClientAdapter": {"VirtualSlotNumber": slot}}
        for slot in profile_slots
    ]
    hmc.list_child.return_value = [
        {
            "UUID": PROFILE_UUID,
            "Resource": {
                "IOConfigurationInstance": {
                    "ProfileVirtualIOAdapters": {
                        "ProfileVirtualIOAdapterSubclass": subclasses
                    }
                }
            },
        }
    ]

    async def list_adapters(lpar_uuid, adapter_type):
        return [
            {"Resource": {"VirtualSlotNumber": slot}}
            for slot in current.get(adapter_type, [])
        ]

    hmc.list_adapters.side_effect = list_adapters
    return hmc


async def _power_on_with_profile(hmc: AsyncMock) -> LparPowerResult:
    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        return await power_lpar(
            hmc, None, LPAR_UUID, power_on=True, partition_profile_uuid=PROFILE_UUID
        )


@pytest.mark.asyncio
async def test_power_on_warns_for_each_current_adapter_the_profile_lacks():
    hmc = _profile_power_client(
        ["2"],
        {"VirtualSCSIClientAdapter": ["2", "3"], "ClientNetworkAdapter": ["4"]},
    )

    result = await _power_on_with_profile(hmc)

    assert len(result.warnings) == 2
    assert "VirtualSCSIClientAdapter in virtual slot 3" in result.warnings[0]
    assert "ClientNetworkAdapter in virtual slot 4" in result.warnings[1]
    # Warn, not refuse: the activation is still submitted with the profile.
    _, document = hmc.submit_job.await_args.args
    assert _parameter_values(document, "LogicalPartitionProfile") == [PROFILE_UUID]


@pytest.mark.asyncio
async def test_power_on_does_not_warn_when_the_profile_has_every_adapter():
    hmc = _profile_power_client(["2", "3"], {"VirtualSCSIClientAdapter": ["2", "3"]})

    result = await _power_on_with_profile(hmc)

    assert result.warnings == ()
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_power_on_submits_with_a_warning_when_an_adapter_feed_read_fails():
    hmc = _profile_power_client([], {})
    hmc.list_adapters.side_effect = HMCError("GET adapters failed", 500)

    result = await _power_on_with_profile(hmc)

    assert result.warnings == (
        "Partition profile adapter check not run: GET adapters failed (HTTP 500)",
    )
    hmc.submit_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_power_on_without_a_profile_reads_no_adapter_feed():
    hmc = _profile_power_client([], {"VirtualSCSIClientAdapter": ["2"]})

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        result = await power_lpar(hmc, None, LPAR_UUID, power_on=True)

    assert result.warnings == ()
    hmc.list_adapters.assert_not_awaited()


@pytest.mark.asyncio
async def test_power_on_lpar_outcome_carries_the_profile_warnings():
    hmc = _profile_power_client([], {"VirtualSCSIClientAdapter": ["2"]})

    with patch(
        "hmcpctl.operations.lpar.core.resolve_lpar_uuid",
        new=AsyncMock(return_value=LPAR_UUID),
    ):
        outcome = await power_on_lpar(
            hmc, LPAR_UUID, partition_profile_uuid=PROFILE_UUID
        )

    assert len(outcome.warnings) == 1
    assert "virtual slot 2" in outcome.warnings[0]
