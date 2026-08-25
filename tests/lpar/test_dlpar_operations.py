"""Async DLPAR processor and memory operations (issue #365, ADR 0094).

These exercise ``operations_lpar.set_lpar_processors`` /
``set_lpar_memory`` directly. Every test here is an ``async def`` running under
``pytest.mark.asyncio``, so the operation is called *from inside an already
running event loop* — the property issue #365 exists to establish, and the one
the pre-extraction ``asyncio.run`` tool bodies could not satisfy.

The document builders and the raw ``modify_logical_partition`` POST stay covered
by ``test_dlpar.py``; the MCP tool delegation is covered by ``test_power_tools.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from conftest import make_config

from hmc_mcp import audit
from hmc_mcp.client import HMCClient
from hmc_mcp.client_resolution import MAX_PARENT_DISCOVERY_SYSTEMS
from hmc_mcp.documents import LparResources
from hmc_mcp.errors import HMCError
from hmc_mcp.operations_lpar import set_lpar_memory, set_lpar_processors

SYSTEM_UUID = "cccc0000-0000-0000-0000-000000000001"
OTHER_SYSTEM_UUID = "cccc0000-0000-0000-0000-000000000002"
LPAR_UUID = "aaaa0000-0000-0000-0000-000000000001"
COLLIDING_LPAR_UUID = "aaaa0000-0000-0000-0000-000000000002"
LPAR_NAME = "lpar1"
SYSTEM_NAME = "server1"

SYSTEM_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>{name}</SystemName>
    </ManagedSystem>
  </content>
</entry>
"""

LPAR_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>{name}</PartitionName>
      <PartitionState>running</PartitionState>
    </LogicalPartition>
  </content>
</entry>
"""


def _feed(*entries: str) -> str:
    """Wrap rendered Atom entries in the feed envelope ``parse_feed`` expects."""
    inner = "".join(
        entry.split("?>", 1)[1].strip().replace(
            ' xmlns="http://www.w3.org/2005/Atom"', "", 1
        )
        for entry in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">' + inner + "</feed>"
    )


def _mock_lpar_detail(router) -> None:
    """``resolve_lpar_ownership_names`` reads the partition name from this GET."""
    router.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200, text=LPAR_ENTRY.format(uuid=LPAR_UUID, name=LPAR_NAME)
        )
    )


def _mock_system_detail(router) -> None:
    router.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200, text=SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name=SYSTEM_NAME)
        )
    )
    _mock_partition_feed(router, SYSTEM_UUID, LPAR_UUID)


def _mock_partition_feed(router, system_uuid: str, *lpar_uuids: str) -> None:
    """The per-system feed both containment checks read."""
    router.get(f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                *(
                    LPAR_ENTRY.format(uuid=uuid, name=LPAR_NAME)
                    for uuid in lpar_uuids
                )
            ),
        )
    )


def _mock_fleet(router) -> None:
    """Bounded parent discovery: the fleet, then each system's partition feed."""
    router.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                SYSTEM_ENTRY.format(uuid=OTHER_SYSTEM_UUID, name="server0"),
                SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name=SYSTEM_NAME),
            ),
        )
    )
    router.get(
        f"/rest/api/uom/ManagedSystem/{OTHER_SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(200, text=_feed()))
    router.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200, text=_feed(LPAR_ENTRY.format(uuid=LPAR_UUID, name=LPAR_NAME))
        )
    )


def _mock_modify(router) -> httpx.Response:
    return router.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200, text=LPAR_ENTRY.format(uuid=LPAR_UUID, name=LPAR_NAME)
        )
    )


def _owned_by(agent_id: str) -> AsyncMock:
    return AsyncMock(return_value=f"[hmc-mcp owner:{agent_id} created:2026-08-14]")


def _description(text: str) -> AsyncMock:
    return AsyncMock(return_value=text)


# ------------------------------------------------------------------ #
# Callable from inside a running event loop (the #365 acceptance criterion)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_set_lpar_processors_runs_inside_a_running_event_loop(mock_hmc):
    """The operation completes on a loop that is already running.

    ``asyncio.run`` raises ``RuntimeError`` here, so this passing at all is the
    proof that no nested ``asyncio.run`` remains on the path.
    """
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description", new=_owned_by("hmc-mcp")
    ):
        async with HMCClient(make_config()) as hmc:
            result = await set_lpar_processors(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.5, desired_vcpus=3),
                system_name_or_uuid=SYSTEM_UUID,
            )

    assert result["Resource"]["PartitionName"] == LPAR_NAME
    body = route.calls.last.request.content.decode()
    assert "PartitionProcessorConfiguration" in body
    assert "DesiredProcessingUnits" in body and ">1.5<" in body
    assert "PartitionMemoryConfiguration" not in body


@pytest.mark.asyncio
async def test_set_lpar_memory_runs_inside_a_running_event_loop(mock_hmc):
    """The memory operation completes on an already-running loop."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description", new=_owned_by("hmc-mcp")
    ):
        async with HMCClient(make_config()) as hmc:
            result = await set_lpar_memory(
                hmc,
                LPAR_UUID,
                LparResources(desired_memory=8192, min_memory=1024, max_memory=16384),
                system_name_or_uuid=SYSTEM_UUID,
            )

    assert result["Resource"]["PartitionName"] == LPAR_NAME
    body = route.calls.last.request.content.decode()
    assert "PartitionMemoryConfiguration" in body
    assert "DesiredMemory" in body and ">8192<" in body
    assert "PartitionProcessorConfiguration" not in body


# ------------------------------------------------------------------ #
# ADR 0092 §3.2: Reconfiguring — guarded unconditionally
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_foreign_owner_is_rejected_before_any_mutation(mock_hmc, operation):
    """A partition another agent owns is refused, and nothing is POSTed."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=_owned_by("bob")):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            with pytest.raises(PermissionError, match="ownership_override=true"):
                await operation(
                    hmc,
                    LPAR_UUID,
                    LparResources(desired_procs=1.0, desired_memory=1024),
                    system_name_or_uuid=SYSTEM_UUID,
                )

    assert not route.called


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_malformed_ownership_token_is_rejected(mock_hmc, operation):
    """A malformed hmc-mcp stamp fails closed rather than reading as unowned."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=_description("[hmc-mcp owner:broken]"),
    ):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            with pytest.raises(PermissionError, match="ownership_override=true"):
                await operation(
                    hmc,
                    LPAR_UUID,
                    LparResources(desired_procs=1.0, desired_memory=1024),
                    system_name_or_uuid=SYSTEM_UUID,
                )

    assert not route.called


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_ownership_override_bypasses_the_guard_without_reading(
    mock_hmc, operation
):
    """ADR 0092 §5: the per-call override skips the token read and mutates."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)
    read = _owned_by("bob")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            await operation(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.0, desired_memory=1024),
                system_name_or_uuid=SYSTEM_UUID,
                ownership_override=True,
            )

    read.assert_not_awaited()
    assert route.called


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_an_unstamped_partition_is_allowed(mock_hmc, operation):
    """ADR 0011 is advisory: a partition with no token is not foreign-owned."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=_description("legacy partition"),
    ):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            await operation(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.0, desired_memory=1024),
                system_name_or_uuid=SYSTEM_UUID,
            )

    assert route.called


# ------------------------------------------------------------------ #
# ADR 0094: the guard's system selector when the caller omits one
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_omitted_system_is_discovered_and_named_to_the_guard(mock_hmc):
    """With no selector the owning system is found and passed to the guard."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    _mock_fleet(mock_hmc)
    route = _mock_modify(mock_hmc)
    read = _owned_by("hmc-mcp")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        async with HMCClient(make_config()) as hmc:
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    assert route.called
    # The guard reads the token by CLI names, so the discovered system must be
    # the one that actually contains the partition — not merely any system.
    assert read.await_args.args[1:] == (SYSTEM_NAME, LPAR_NAME)


@pytest.mark.asyncio
async def test_a_supplied_system_skips_fleet_discovery(mock_hmc):
    """The discovery cost lands only on the omitted-selector path."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    _mock_fleet(mock_hmc)
    _mock_modify(mock_hmc)

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description", new=_owned_by("hmc-mcp")
    ):
        async with HMCClient(make_config()) as hmc:
            await set_lpar_processors(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.0),
                system_name_or_uuid=SYSTEM_UUID,
            )

    paths = [call.request.url.path for call in mock_hmc.calls]
    assert "/rest/api/uom/ManagedSystem" not in paths


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_an_override_without_a_selector_skips_discovery_entirely(
    mock_hmc, operation
):
    """ADR 0092 §4: the override path pays nothing — and is blocked by nothing.

    Discovery exists only to feed the guard, so an approved override must not
    depend on it. Registering no fleet routes at all is the assertion: respx
    raises on an unmocked request, so any discovery call fails this test.
    """
    route = _mock_modify(mock_hmc)
    read = _owned_by("bob")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            await operation(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.0, desired_memory=1024),
                ownership_override=True,
            )

    read.assert_not_awaited()
    assert route.called
    assert [call.request.url.path for call in mock_hmc.calls if
            call.request.method == "GET"] == []


@pytest.mark.asyncio
async def test_an_override_audits_the_selectors_the_caller_supplied(mock_hmc, caplog):
    """With no system named, the audit records that absence rather than a guess."""
    _mock_modify(mock_hmc)

    with (
        patch("hmc_mcp.operations_lpar.get_lpar_description", new=AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        async with HMCClient(make_config(agent_id="alice")) as hmc:
            await set_lpar_processors(
                hmc,
                LPAR_UUID,
                LparResources(desired_procs=1.0),
                ownership_override=True,
            )

    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == audit.AUDIT_LOGGER_NAME
    ]
    assert len(records) == 1, "an absence assertion over an empty capture proves nothing"
    assert records[0]["event"] == "ownership-override"
    assert records[0]["lpar"] == LPAR_UUID
    assert records[0]["system"] == ""


@pytest.mark.asyncio
async def test_discovery_reports_frames_it_could_not_read(mock_hmc):
    """A skipped frame is named in the failure, not silently absorbed.

    Skipping never widens what may be mutated — the walk only returns on a
    positive UUID match — but a degraded fleet must read as degraded rather
    than as an absent partition.
    """
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name=SYSTEM_NAME).replace(
                    f"<id>urn:uuid:{SYSTEM_UUID}</id>", "<id></id>"
                ),
                SYSTEM_ENTRY.format(uuid=OTHER_SYSTEM_UUID, name="server0"),
            ),
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/ManagedSystem/{OTHER_SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(500, text="<error>frame down</error>"))
    route = _mock_modify(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="2 could not be read") as exc_info:
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    message = str(exc_info.value)
    assert "incomplete inventory metadata" in message
    assert OTHER_SYSTEM_UUID in message
    assert "supply managed-system scope" in message
    assert not route.called


@pytest.mark.asyncio
async def test_an_unhealthy_frame_does_not_block_a_healthy_one(mock_hmc):
    """A frame that errors ahead of the owner is skipped, not fatal.

    Before ADR 0094 the selector-less call touched no frame but the partition's
    own; a walk that aborted on the first unhealthy frame would make every
    DLPAR call hostage to an unrelated system's health, ordered by inventory.
    """
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                SYSTEM_ENTRY.format(uuid=OTHER_SYSTEM_UUID, name="server0"),
                SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name=SYSTEM_NAME),
            ),
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/ManagedSystem/{OTHER_SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(500, text="<error>frame down</error>"))
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    route = _mock_modify(mock_hmc)
    read = _owned_by("hmc-mcp")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        async with HMCClient(make_config()) as hmc:
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    assert route.called
    assert read.await_args.args[1:] == (SYSTEM_NAME, LPAR_NAME)


@pytest.mark.asyncio
async def test_discovery_rejects_an_oversized_fleet(mock_hmc):
    """The fan-out cap names the fleet size, not a partition-name ambiguity."""
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                *(
                    SYSTEM_ENTRY.format(
                        uuid=f"cccc0000-0000-0000-0000-{index:012d}",
                        name=f"server{index}",
                    )
                    for index in range(MAX_PARENT_DISCOVERY_SYSTEMS + 1)
                )
            ),
        )
    )
    route = _mock_modify(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="discovery exceeds") as exc_info:
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    assert "ambiguous" not in str(exc_info.value)
    assert "supply managed-system scope" in str(exc_info.value)
    assert not route.called


@pytest.mark.asyncio
async def test_discovery_translates_its_own_timeout(mock_hmc, monkeypatch):
    """A slow fleet reports the timeout and the remedy, not a bare CancelledError.

    The delay sits on the HMC boundary rather than on a zero-second deadline,
    which would race the mock transport and flake either way.
    """
    monkeypatch.setattr(
        "hmc_mcp.operations_lpar.PARENT_DISCOVERY_TIMEOUT_SECONDS", 0.01
    )

    async def _slow_fleet(request):
        await asyncio.sleep(1)
        raise AssertionError("the deadline should have fired first")

    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(side_effect=_slow_fleet)
    route = _mock_modify(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="parent discovery timed out"):
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    assert not route.called


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_a_partition_uuid_off_the_selected_system_is_rejected(
    mock_hmc, operation
):
    """The guard never reads a token from a system the partition does not live on.

    ``resolve_lpar_uuid`` passes a canonical UUID straight through, so nothing
    upstream pairs the selector with the partition. Left unchecked, a
    cross-system partition-name collision would let the guard approve against a
    different partition's token.
    """
    _mock_lpar_detail(mock_hmc)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{OTHER_SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200, text=SYSTEM_ENTRY.format(uuid=OTHER_SYSTEM_UUID, name="server0")
        )
    )
    # server0 hosts a *different* partition that happens to share lpar1's name.
    _mock_partition_feed(mock_hmc, OTHER_SYSTEM_UUID, COLLIDING_LPAR_UUID)
    route = _mock_modify(mock_hmc)
    read = _owned_by("hmc-mcp")

    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        async with HMCClient(make_config()) as hmc:
            with pytest.raises(ValueError, match="does not belong to managed system"):
                await operation(
                    hmc,
                    LPAR_UUID,
                    LparResources(desired_procs=1.0, desired_memory=1024),
                    system_name_or_uuid=OTHER_SYSTEM_UUID,
                )

    read.assert_not_awaited()
    assert not route.called


@pytest.mark.asyncio
async def test_undiscoverable_system_names_the_operator_remedy(mock_hmc):
    """No owning system found: the error names the selector that fixes it."""
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(
            200, text=_feed(SYSTEM_ENTRY.format(uuid=OTHER_SYSTEM_UUID, name="server0"))
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/ManagedSystem/{OTHER_SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(200, text=_feed()))
    route = _mock_modify(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="supply managed-system scope"):
            await set_lpar_processors(
                hmc, LPAR_UUID, LparResources(desired_procs=1.0)
            )

    assert not route.called


# ------------------------------------------------------------------ #
# Error translation
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("operation", [set_lpar_processors, set_lpar_memory])
@pytest.mark.asyncio
async def test_http_406_is_translated_to_an_actionable_error(mock_hmc, operation):
    """The extraction keeps the tool-body 406 hint on the operation."""
    _mock_lpar_detail(mock_hmc)
    _mock_system_detail(mock_hmc)
    mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(406, text="<error>nope</error>")
    )

    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description", new=_owned_by("hmc-mcp")
    ):
        async with HMCClient(make_config()) as hmc:
            with pytest.raises(HMCError, match="HMC_SCHEMA_VERSION"):
                await operation(
                    hmc,
                    LPAR_UUID,
                    LparResources(desired_procs=1.0, desired_memory=1024),
                    system_name_or_uuid=SYSTEM_UUID,
                )
