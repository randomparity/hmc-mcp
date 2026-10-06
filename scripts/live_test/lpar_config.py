"""LPAR configuration, DLPAR and boot-order scenarios: the lpar-config arm (#1345).

Subtask 39 creates one run-unique scratch partition, changes its configuration
through the six operations under test, activates it to SMS for the activated
DLPAR case, and deletes it. It touches no other partition: every mutating call
names the scratch partition's UUID.
"""

from __future__ import annotations

import asyncio
import math
import re
import shlex
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from hmcpctl.operations.lpar.ownership import parse_lpar_ownership_caller_token
from hmcpctl.xmlutil import leaf_text

from .observation import Assertion, CallFailure, judge_create_result
from .pcie import _ABSENCE_REREAD_DELAY_S, partition_not_found
from .results import field as result_field
from .results import resource

if TYPE_CHECKING:
    from live_test_runner import RunState

SUBTASK = 39
GROUP = "lpar-config"
#: Reserved for this arm: recovery reports any partition carrying it as stranded.
NAME_PREFIX = "hmcpctl-live-lpar-"
TOKEN_PREFIX = "lparcfg-"
SCENARIO = "st39-lpar-config"

#: Shared and uncapped, sized like the bare-cec fixture so it reaches firmware.
RESOURCES: dict[str, Any] = {
    "min_memory": 1024,
    "desired_memory": 2048,
    "max_memory": 4096,
    "dedicated": False,
    "min_procs": 0.1,
    "desired_procs": 0.5,
    "max_procs": 1.0,
    "min_vcpus": 1,
    "desired_vcpus": 1,
    "max_vcpus": 2,
    "uncapped": True,
}
#: Two Open Firmware paths in the `BootDeviceList` shape (#980 design).
BOOT_PATHS = [
    "/vdevice/v-scsi@30000002/disk@8100000000000000",
    "/vdevice/l-lan@30000003",
]
_CLEAR_REFUSAL = "Refusing to clear the pending boot order"
_ACTIVATED_GAP_NOTE = (
    "refused while activated without RMC: the gap's prerequisite is an operating "
    "system with an active RMC connection"
)

_MEMORY = ("PartitionMemoryConfiguration",)
_SHARED = ("PartitionProcessorConfiguration", "SharedProcessorConfiguration")
_FIELDS = (
    ("min_memory", _MEMORY, "MinimumMemory"),
    ("desired_memory", _MEMORY, "DesiredMemory"),
    ("max_memory", _MEMORY, "MaximumMemory"),
    ("desired_procs", _SHARED, "DesiredProcessingUnits"),
    ("max_procs", _SHARED, "MaximumProcessingUnits"),
    ("desired_vcpus", _SHARED, "DesiredVirtualProcessors"),
    ("max_vcpus", _SHARED, "MaximumVirtualProcessors"),
)

_JOB_TIMEOUT_S = 900
_JOB_POLL_INTERVAL_S = 10
_STATE_POLL_ATTEMPTS = 30
_STATE_POLL_DELAY_S = 10.0
_COMPARE_REREAD_DELAY_S = 30.0
_NOT_ACTIVATED = "not activated"
_FIRMWARE_STATES = frozenset({"open firmware", "running"})
_UUID_AT_END = re.compile(
    r"/([0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12})/?\Z"
)


@dataclass(frozen=True)
class _Pools:
    """The system-wide reads the run must leave as it found them."""

    names: frozenset[str]
    procs: str
    memory: str


@dataclass
class _Run:
    """What the arm has established, read by teardown and the deferred observations."""

    system: str
    name: str
    token: str
    region: int
    uuid: str | None = None
    created: bool = False
    create_attempted: bool = False
    deleted: bool = False
    held: dict[str, bool] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def hold(self, operation: str, assertion: str, value: bool) -> None:
        self.held[f"{operation}:{assertion}"] = value

    def assertions(self, operation: str, *ids: str) -> list[Assertion]:
        return [Assertion(i, self.held.get(f"{operation}:{i}", False)) for i in ids]


def scratch_partitions(names: Iterable[str]) -> list[str]:
    """The partition names only this arm may have created."""
    return sorted(name for name in names if name.startswith(NAME_PREFIX))


def _quoted(run: _Run) -> tuple[str, str]:
    return shlex.quote(run.system), shlex.quote(run.name)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def _cli(client: Client, state: RunState, cmd: str) -> str | None:
    st, data = await state.call(client, "hmc_run_command", cmd=cmd)
    return data.strip() if st == "PASS" and isinstance(data, str) else None


async def _read_pools(
    client: Client, state: RunState, system: str
) -> tuple[_Pools, int] | None:
    """The partition names, free processing units, free memory and region size."""
    quoted = shlex.quote(system)
    names = await _cli(client, state, f"lssyscfg -r lpar -m {quoted} -F name")
    procs = await _cli(
        client,
        state,
        f"lshwres -r proc -m {quoted} --level sys -F curr_avail_sys_proc_units",
    )
    memory = await _cli(
        client,
        state,
        f"lshwres -r mem -m {quoted} --level sys -F curr_avail_sys_mem,mem_region_size",
    )
    if names is None or procs is None or memory is None:
        return None
    available, _, region = memory.partition(",")
    if not region.strip().isdigit():
        return None
    pools = _Pools(frozenset(names.splitlines()), procs, available.strip())
    return pools, int(region)


def _section(data: object, path: tuple[str, ...]) -> Mapping[str, object]:
    node: object = resource(data) if isinstance(data, Mapping) else {}
    for name in path:
        node = node.get(name) if isinstance(node, Mapping) else None
    return node if isinstance(node, Mapping) else {}


def _number(value: object) -> float | None:
    text = leaf_text(value)
    try:
        return float(text) if isinstance(text, str) else None
    except ValueError:
        return None


async def _read_lpar(client: Client, state: RunState, run: _Run) -> tuple[str, Any]:
    return await state.call(
        client,
        "hmc_get_lpar",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
    )


async def _values(client: Client, state: RunState, run: _Run) -> dict[str, float]:
    """The partition's configured memory and shared-processor values, by resource name."""
    st, data = await _read_lpar(client, state, run)
    if st != "PASS":
        return {}
    values = {}
    for key, path, element in _FIELDS:
        number = _number(_section(data, path).get(element))
        if number is not None:
            values[key] = number
    return values


async def _token_of(
    client: Client, state: RunState, run: _Run, lpar: str
) -> str | None:
    st, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=lpar,
    )
    if st != "PASS" or not isinstance(data, str):
        return None
    return parse_lpar_ownership_caller_token(data)


async def _absent(client: Client, state: RunState, run: _Run, name: str) -> bool:
    """Whether *name* answers HSCL8012, read twice unless the first answer is plain.

    The `pcie.name_absent` rule (#906): an SSH view can lag a REST write, so any
    answer other than HSCL8012 or a readable description is read once more.
    """

    async def lookup() -> tuple[str, Any]:
        return await state.call(
            client,
            "hmc_get_lpar_description",
            system_name_or_uuid=run.system,
            lpar_name_or_uuid=name,
        )

    st, data = await lookup()
    if partition_not_found(st, data):
        return True
    if st == "PASS" and isinstance(data, str):
        return False
    await asyncio.sleep(_ABSENCE_REREAD_DELAY_S)
    st, data = await lookup()
    return partition_not_found(st, data)


async def _lpar_state(client: Client, state: RunState, run: _Run) -> str | None:
    st, data = await state.call(
        client,
        "hmc_get_lpar_state",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
    )
    return data.strip().lower() if st == "PASS" and isinstance(data, str) else None


async def _wait_for_state(
    client: Client, state: RunState, run: _Run, wanted: frozenset[str]
) -> str | None:
    observed = None
    for attempt in range(_STATE_POLL_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_STATE_POLL_DELAY_S)
        observed = await _lpar_state(client, state, run)
        if observed in wanted:
            break
    return observed


# ---------------------------------------------------------------------------
# Mutations: one literal dispatch each, always naming the scratch UUID
# ---------------------------------------------------------------------------


async def _dlpar_mem(
    client: Client, state: RunState, run: _Run, resources: dict[str, Any]
) -> tuple[str, Any]:
    st, data = await state.call(
        client,
        "hmc_dlpar_mem",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        resources=resources,
    )
    return st, data


async def _dlpar_proc(
    client: Client, state: RunState, run: _Run, resources: dict[str, Any]
) -> tuple[str, Any]:
    st, data = await state.call(
        client,
        "hmc_dlpar_proc",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        resources=resources,
    )
    return st, data


def _reads_back(
    before: dict[str, float], after: dict[str, float], expected: dict[str, float]
) -> bool:
    """Each expected value reads back, and every other configured value is unchanged."""
    if not before or not after:
        return False
    for key, _, _ in _FIELDS:
        want = expected.get(key, before.get(key))
        got = after.get(key)
        if want is None or got is None or not math.isclose(want, got, abs_tol=1e-6):
            return False
    return True


def _refused_empty(status: str, data: object) -> bool:
    return (
        status == "FAIL"
        and isinstance(data, CallFailure)
        and "Nothing to change" in data.message
    )


async def _resource_cases(
    client: Client,
    state: RunState,
    run: _Run,
    operation: str,
    small: dict[str, float],
    large: dict[str, float],
    no_op: tuple[str, ...],
) -> None:
    """Small, large, no-op and empty requests through one DLPAR tool."""
    change = _dlpar_mem if operation == "lpar.dlpar_mem" else _dlpar_proc
    tool = "hmc_dlpar_mem" if change is _dlpar_mem else "hmc_dlpar_proc"
    readings: dict[str, Any] = {}
    for label, request in (("small", small), ("large", large)):
        before = await _values(client, state, run)
        st, data = await change(client, state, run, request)
        state.record(SUBTASK, f"{tool} ({label})", st, data)
        after = await _values(client, state, run)
        readings[label] = after
        run.hold(
            operation,
            f"{label}-change-read-back",
            st == "PASS" and _reads_back(before, after, request),
        )
    before = await _values(client, state, run)
    request = {key: before[key] for key in no_op if key in before}
    st, data = await change(client, state, run, request)
    state.record(SUBTASK, f"{tool} (no-op)", st, data)
    after = await _values(client, state, run)
    run.hold(
        operation,
        "no-op-accepted-unchanged",
        st == "PASS" and len(request) == len(no_op) and _reads_back(before, after, {}),
    )
    st, data = await change(client, state, run, {})
    refused = _refused_empty(st, data)
    # The refusal is the expected answer, so it is the PASS row.
    state.record(
        SUBTASK, f"{tool} (empty request)", "PASS" if refused else "FAIL", data
    )
    run.hold(operation, "empty-request-refused", refused)
    run.data[tool] = readings


async def _memory_cases(client: Client, state: RunState, run: _Run) -> None:
    current = await _values(client, state, run)
    desired, maximum = current.get("desired_memory"), current.get("max_memory")
    if desired is None or maximum is None:
        state.skip(SUBTASK, "hmc_dlpar_mem", "the partition's memory did not read")
        return
    await _resource_cases(
        client,
        state,
        run,
        "lpar.dlpar_mem",
        {"desired_memory": desired + run.region},
        {"desired_memory": maximum},
        ("desired_memory",),
    )
    before = await _values(client, state, run)
    st, data = await state.call(
        client,
        "hmc_dlpar_mem",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        resources={"desired_memory": maximum + run.region},
    )
    # Recorded, never asserted: the answer itself is what this run captures.
    state.record(
        SUBTASK,
        "hmc_dlpar_mem (over maximum)",
        "PASS",
        {
            "status": st,
            "call": data,
            "before": before,
            "after": await _values(client, state, run),
        },
    )


async def _processor_cases(client: Client, state: RunState, run: _Run) -> None:
    current = await _values(client, state, run)
    if not {"desired_procs", "max_procs", "max_vcpus"} <= current.keys():
        state.skip(SUBTASK, "hmc_dlpar_proc", "the partition's processors did not read")
        return
    await _resource_cases(
        client,
        state,
        run,
        "lpar.dlpar_proc",
        {"desired_procs": round(current["desired_procs"] + 0.1, 2)},
        {"desired_procs": current["max_procs"], "desired_vcpus": current["max_vcpus"]},
        ("desired_procs", "desired_vcpus"),
    )


async def _modify_case(client: Client, state: RunState, run: _Run) -> None:
    """Criterion 3's re-capture: the call ST8 declared an expected HTTP 406."""
    before = await _values(client, state, run)
    request = {
        "desired_memory": RESOURCES["desired_memory"] + run.region,
        "max_memory": RESOURCES["max_memory"] + run.region,
        "desired_procs": 0.6,
    }
    st, data = await state.call(
        client,
        "hmc_modify_lpar",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        resources=request,
    )
    state.record(SUBTASK, "hmc_modify_lpar (memory and units)", st, data)
    after = await _values(client, state, run)
    memory = {key: request[key] for key in ("desired_memory", "max_memory")}
    run.hold(
        "lpar.modify",
        "memory-read-back",
        st == "PASS"
        and all(math.isclose(after.get(k, -1), v) for k, v in memory.items()),
    )
    run.hold(
        "lpar.modify",
        "processing-units-read-back",
        st == "PASS" and math.isclose(after.get("desired_procs", -1), 0.6),
    )
    run.hold(
        "lpar.modify",
        "other-values-unchanged",
        st == "PASS" and _reads_back(before, after, request),
    )
    run.data["hmc_modify_lpar"] = {"request": request, "after": after}


def _uuid_of(data: object) -> str | None:
    if not isinstance(data, Mapping):
        return None
    value = (
        data.get("UUID")
        or data.get("uuid")
        or leaf_text(resource(data).get("PartitionUUID"))
    )
    return value if isinstance(value, str) else None


async def _rename(client: Client, state: RunState, run: _Run, new_name: str) -> bool:
    st, data = await state.call(
        client,
        "hmc_rename_lpar",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=run.uuid,
        new_name=new_name,
    )
    state.record(SUBTASK, f"hmc_rename_lpar (to {new_name})", st, data)
    return st == "PASS"


async def _rename_case(client: Client, state: RunState, run: _Run) -> None:
    original, renamed = run.name, f"{run.name}-rn"
    if not await _rename(client, state, run, renamed):
        return
    run.name = renamed
    st, data = await state.call(
        client,
        "hmc_get_lpar",
        lpar_name_or_uuid=renamed,
        system_name_or_uuid=run.system,
    )
    run.hold(
        "lpar.rename",
        "renamed-same-uuid",
        st == "PASS" and (_uuid_of(data) or "").lower() == (run.uuid or "").lower(),
    )
    run.hold(
        "lpar.rename", "old-name-absent", await _absent(client, state, run, original)
    )
    run.hold(
        "lpar.rename",
        "ownership-stamp-kept",
        await _token_of(client, state, run, renamed) == run.token,
    )
    if await _rename(client, state, run, original):
        run.name = original
    run.hold(
        "lpar.rename",
        "original-name-restored",
        run.name == original
        and await _token_of(client, state, run, original) == run.token,
    )
    run.data["hmc_rename_lpar"] = {"renamed_to": renamed, "restored": run.name}


async def _pending_boot_string(
    client: Client, state: RunState, run: _Run
) -> tuple[bool, object]:
    st, data = await state.call(
        client,
        "hmc_read_lpar_boot_order",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=run.uuid,
    )
    return st == "PASS", result_field(data, "pending_boot_string")


async def _boot_cases(client: Client, state: RunState, run: _Run) -> None:
    st, data = await state.call(
        client,
        "hmc_set_lpar_boot_order",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=run.uuid,
        devices=BOOT_PATHS,
    )
    state.record(SUBTASK, "hmc_set_lpar_boot_order (two paths)", st, data)
    read, pending = await _pending_boot_string(client, state, run)
    run.hold(
        "boot_order.set",
        "pending-boot-string-read-back",
        st == "PASS" and read and pending == " ".join(BOOT_PATHS),
    )
    run.data["hmc_set_lpar_boot_order"] = {"pending_boot_string": pending}

    st, data = await state.call(
        client,
        "hmc_clear_lpar_boot_order",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=run.uuid,
    )
    refused = (
        st == "FAIL"
        and isinstance(data, CallFailure)
        and _CLEAR_REFUSAL in data.message
    )
    state.record(
        SUBTASK,
        "hmc_clear_lpar_boot_order (refusal)",
        "PASS" if refused else "FAIL",
        data,
    )
    after_read, after = await _pending_boot_string(client, state, run)
    run.hold("boot_order.clear", "clear-refused-after-authorization", refused)
    run.hold(
        "boot_order.clear",
        "pending-boot-string-unchanged",
        read and after_read and after == pending,
    )
    run.data["hmc_clear_lpar_boot_order"] = {"pending_boot_string": after}


# ---------------------------------------------------------------------------
# Activated case (#1170): recorded, never observed
# ---------------------------------------------------------------------------


def _profile_uuid(data: object) -> str | None:
    link = (
        resource(data).get("AssociatedPartitionProfile")
        if isinstance(data, Mapping)
        else None
    )
    if isinstance(link, Mapping) and isinstance(link.get("@attrs"), Mapping):
        link = link["@attrs"]
    href = link.get("href") if isinstance(link, Mapping) else None
    match = _UUID_AT_END.search(href) if isinstance(href, str) else None
    return match.group(1) if match else None


async def _configuration(client: Client, state: RunState, run: _Run) -> dict[str, Any]:
    """Both whole configuration sections, `Current*` leaves included, as read."""
    st, data = await _read_lpar(client, state, run)
    if st != "PASS":
        return {}
    return {
        "memory": dict(_section(data, _MEMORY)),
        "processor": dict(_section(data, ("PartitionProcessorConfiguration",))),
    }


async def _record_activated(
    client: Client, state: RunState, run: _Run, tool: str, st: str, data: Any
) -> None:
    after = await _configuration(client, state, run)
    location = result_field(data, "change_location") if st == "PASS" else None
    note = _ACTIVATED_GAP_NOTE if st != "PASS" else "accepted while activated"
    state.record(
        SUBTASK,
        f"{tool} (activated)",
        "PASS" if st == "PASS" else "SKIP",
        {"call": data, "change_location": location, "read_back": after},
        note,
    )


async def _power_on(client: Client, state: RunState, run: _Run) -> bool:
    st, data = await _read_lpar(client, state, run)
    profile_uuid = _profile_uuid(data) if st == "PASS" else None
    if profile_uuid is None:
        state.skip(SUBTASK, "activated DLPAR", "no associated partition profile read")
        return False
    st, data = await state.call(
        client,
        "hmc_power_on_lpar",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        partition_profile_uuid=profile_uuid,
        boot_mode="sms",
        wait=True,
        timeout_seconds=_JOB_TIMEOUT_S,
        poll_interval=_JOB_POLL_INTERVAL_S,
    )
    state.record(SUBTASK, "hmc_power_on_lpar (to SMS)", st, data)
    reached = await _wait_for_state(client, state, run, _FIRMWARE_STATES)
    if reached not in _FIRMWARE_STATES:
        state.skip(
            SUBTASK,
            "activated DLPAR",
            f"the partition read {reached!r}, not open firmware or running",
        )
        return False
    return True


async def _power_off(client: Client, state: RunState, run: _Run) -> bool:
    st, data = await state.call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=run.uuid,
        system_name_or_uuid=run.system,
        immediate=True,
        wait=True,
        timeout_seconds=_JOB_TIMEOUT_S,
        poll_interval=_JOB_POLL_INTERVAL_S,
    )
    state.record(SUBTASK, "hmc_power_off_lpar (immediate)", st, data)
    observed = await _wait_for_state(client, state, run, frozenset({_NOT_ACTIVATED}))
    return observed == _NOT_ACTIVATED


async def _activated_cases(client: Client, state: RunState, run: _Run) -> None:
    if not await _power_on(client, state, run):
        return
    st, data = await _dlpar_mem(
        client, state, run, {"desired_memory": 2048 + run.region}
    )
    await _record_activated(client, state, run, "hmc_dlpar_mem", st, data)
    st, data = await _dlpar_proc(client, state, run, {"desired_procs": 0.7})
    await _record_activated(client, state, run, "hmc_dlpar_proc", st, data)


# ---------------------------------------------------------------------------
# Create, teardown, observations
# ---------------------------------------------------------------------------


async def _create(client: Client, state: RunState, run: _Run) -> bool:
    run.create_attempted = True
    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=run.system,
        name=run.name,
        caller_token=run.token,
        resources=RESOURCES,
    )
    record_status, reason = judge_create_result(st, data)
    state.record(SUBTASK, "hmc_create_lpar (scratch)", record_status, data, reason)
    created = result_field(data, "lpar") if st == "PASS" else None
    if isinstance(created, Mapping):
        run.uuid = _uuid_of(created)
    if run.uuid is None and not await _adopt_by_name(client, state, run):
        return False
    run.created = record_status == "PASS"
    return run.created


async def _adopt_by_name(client: Client, state: RunState, run: _Run) -> bool:
    """Resolve the UUID by name, only for a partition carrying this run's token."""
    if await _token_of(client, state, run, run.name) != run.token:
        return False
    st, data = await state.call(
        client,
        "hmc_get_lpar",
        lpar_name_or_uuid=run.name,
        system_name_or_uuid=run.system,
    )
    run.uuid = _uuid_of(data) if st == "PASS" else None
    return run.uuid is not None


async def _gone(client: Client, state: RunState, run: _Run) -> bool:
    """Whether the system lists no partition under this run's name, renamed or not.

    Read from the listing rather than one name, so a rename the HMC applied but
    reported as failed cannot hide the partition; read twice, as `_absent` does.
    """
    base = NAME_PREFIX + run.token.removeprefix(TOKEN_PREFIX)
    command = f"lssyscfg -r lpar -m {shlex.quote(run.system)} -F name"
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(_ABSENCE_REREAD_DELAY_S)
        listing = await _cli(client, state, command)
        if listing is not None and not any(
            name.startswith(base) for name in listing.splitlines()
        ):
            return True
    return False


async def _delete(client: Client, state: RunState, run: _Run) -> str | None:
    """Delete the scratch partition; return why it is not confirmed gone, or None."""
    if run.uuid is None and not await _adopt_by_name(client, state, run):
        if await _gone(client, state, run):
            run.deleted = True
            return None
        return "its UUID could not be resolved to a partition carrying this run's token"
    if await _lpar_state(client, state, run) != _NOT_ACTIVATED and not await _power_off(
        client, state, run
    ):
        return "it did not reach Not Activated"
    if await _token_of(client, state, run, run.uuid or "") != run.token:
        return "its description no longer carries this run's caller token"
    st, data = await state.call(
        client,
        "hmc_delete_lpar",
        system_name_or_uuid=run.system,
        lpar_name_or_uuid=run.uuid,
    )
    state.record(SUBTASK, "hmc_delete_lpar (scratch)", st, data)
    if st != "PASS":
        return "the delete was refused"
    run.deleted = await _gone(client, state, run)
    return (
        None if run.deleted else "its absence could not be confirmed after the delete"
    )


async def _teardown(client: Client, state: RunState, run: _Run) -> None:
    if not run.create_attempted:
        return
    why = await _delete(client, state, run)
    if why is None:
        return
    system, name = _quoted(run)
    state.record(
        SUBTASK,
        "scratch partition teardown",
        "FAIL",
        f"MANUAL RECOVERY REQUIRED: partition {run.name!r} on {run.system!r} was NOT "
        f"deleted: {why}. Once its description shows caller token {run.token!r}: "
        f"`chsysstate -m {system} -r lpar -n {name} -o shutdown --immed` (when not "
        f"Not Activated), then `rmsyscfg -r lpar -m {system} -n {name}`.",
    )


async def _compare(
    client: Client, state: RunState, run: _Run, baseline: _Pools
) -> bool:
    reads = []
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(_COMPARE_REREAD_DELAY_S)
        read = await _read_pools(client, state, run.system)
        reads.append(read[0] if read else None)
        if reads[-1] == baseline:
            break
    same = reads[-1] == baseline
    state.record(
        SUBTASK,
        "system baseline compare",
        "PASS" if same else "FAIL",
        {
            "baseline": _pools_data(baseline),
            "reads": [_pools_data(read) if read else None for read in reads],
        },
    )
    return same


def _pools_data(pools: _Pools) -> dict[str, Any]:
    return {
        "partitions": sorted(pools.names),
        "available_processing_units": pools.procs,
        "available_memory_mib": pools.memory,
    }


def _observe(state: RunState, run: _Run, cleanup: str) -> None:
    """The resource observations, at literal call sites the scenario-gap and
    assertion-id guards can read."""
    state.record_verified(
        SUBTASK,
        "hmc_modify_lpar",
        operation="lpar.modify",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "memory-read-back", run.held.get("lpar.modify:memory-read-back", False)
            ),
            Assertion(
                "processing-units-read-back",
                run.held.get("lpar.modify:processing-units-read-back", False),
            ),
            Assertion(
                "other-values-unchanged",
                run.held.get("lpar.modify:other-values-unchanged", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_modify_lpar"),
    )
    state.record_verified(
        SUBTASK,
        "hmc_dlpar_mem",
        operation="lpar.dlpar_mem",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "small-change-read-back",
                run.held.get("lpar.dlpar_mem:small-change-read-back", False),
            ),
            Assertion(
                "large-change-read-back",
                run.held.get("lpar.dlpar_mem:large-change-read-back", False),
            ),
            Assertion(
                "no-op-accepted-unchanged",
                run.held.get("lpar.dlpar_mem:no-op-accepted-unchanged", False),
            ),
            Assertion(
                "empty-request-refused",
                run.held.get("lpar.dlpar_mem:empty-request-refused", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_dlpar_mem"),
    )
    state.record_verified(
        SUBTASK,
        "hmc_dlpar_proc",
        operation="lpar.dlpar_proc",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "small-change-read-back",
                run.held.get("lpar.dlpar_proc:small-change-read-back", False),
            ),
            Assertion(
                "large-change-read-back",
                run.held.get("lpar.dlpar_proc:large-change-read-back", False),
            ),
            Assertion(
                "no-op-accepted-unchanged",
                run.held.get("lpar.dlpar_proc:no-op-accepted-unchanged", False),
            ),
            Assertion(
                "empty-request-refused",
                run.held.get("lpar.dlpar_proc:empty-request-refused", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_dlpar_proc"),
    )


def _observe_identity(state: RunState, run: _Run, cleanup: str) -> None:
    """The rename and boot-order observations; literal sites, as `_observe`."""
    state.record_verified(
        SUBTASK,
        "hmc_rename_lpar",
        operation="lpar.rename",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "renamed-same-uuid",
                run.held.get("lpar.rename:renamed-same-uuid", False),
            ),
            Assertion(
                "old-name-absent", run.held.get("lpar.rename:old-name-absent", False)
            ),
            Assertion(
                "ownership-stamp-kept",
                run.held.get("lpar.rename:ownership-stamp-kept", False),
            ),
            Assertion(
                "original-name-restored",
                run.held.get("lpar.rename:original-name-restored", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_rename_lpar"),
    )
    state.record_verified(
        SUBTASK,
        "hmc_set_lpar_boot_order",
        operation="boot_order.set",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "pending-boot-string-read-back",
                run.held.get("boot_order.set:pending-boot-string-read-back", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_set_lpar_boot_order"),
    )
    state.record_verified(
        SUBTASK,
        "hmc_clear_lpar_boot_order",
        operation="boot_order.clear",
        scenario=SCENARIO,
        assertions=[
            Assertion(
                "clear-refused-after-authorization",
                run.held.get(
                    "boot_order.clear:clear-refused-after-authorization", False
                ),
            ),
            Assertion(
                "pending-boot-string-unchanged",
                run.held.get("boot_order.clear:pending-boot-string-unchanged", False),
            ),
        ],
        cleanup=cleanup,
        data=run.data.get("hmc_clear_lpar_boot_order"),
    )


async def exercise_lpar_config(client: Client, state: RunState) -> None:
    print("\n=== ST39: LPAR configuration, DLPAR and boot order (issue #1345) ===")
    if state.group != GROUP:
        state.skip(
            SUBTASK,
            "lpar-config arm",
            f"creates and deletes a partition: runs only in the {GROUP!r} group",
        )
        return
    system = state.config.system_name
    read = await _read_pools(client, state, system)
    if read is None:
        state.skip(SUBTASK, "lpar-config arm", "the system baseline did not read")
        return
    baseline, region = read
    state.record(SUBTASK, "system baseline", "PASS", _pools_data(baseline))
    if stranded := scratch_partitions(baseline.names):
        state.skip(
            SUBTASK,
            "lpar-config arm",
            f"{stranded} already exist: run live_test_recovery.py first",
        )
        return
    suffix = uuid.uuid4().hex[:8]
    run = _Run(system, f"{NAME_PREFIX}{suffix}", f"{TOKEN_PREFIX}{suffix}", region)
    try:
        if await _create(client, state, run):
            await _modify_case(client, state, run)
            await _memory_cases(client, state, run)
            await _processor_cases(client, state, run)
            await _rename_case(client, state, run)
            await _boot_cases(client, state, run)
            await _activated_cases(client, state, run)
    finally:
        await _teardown(client, state, run)
    same = await _compare(client, state, run, baseline)
    if run.created:
        cleanup = "passed" if run.deleted and same else "failed"
        _observe(state, run, cleanup)
        _observe_identity(state, run, cleanup)
