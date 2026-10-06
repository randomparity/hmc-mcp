"""VIOS I/O-configuration backup and restore scenario for the live harness (#1349).

ST37 verifies `vios.list_backups`, `vios.backup -t viosioconfig` and
`vios.restore -t viosioconfig` on the VIOS that serves the test partition: it takes
a baseline, backs up the VIOS's I/O configuration, removes the test partition's disk
VTD, restores the backup (`-r` lets the HMC restart the VIOS), asserts the mapping
and baseline came back, and removes the backup with `rmviosbk`. hmcpctl has no
backup-removal tool (#698), so that one step goes through `hmc_run_command`, as do
the VIOS-side `rmvdev`/`mkvdev`.

A read that fails after a mutation permits no further mutation: the arm then keeps
the backup for the operator, and the recovery check names what is left.
"""

from __future__ import annotations

import asyncio
import csv
import difflib
import io
import re
import secrets
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from hmcpctl.config import HMCConfig

from .observation import Assertion
from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

SUBTASK = 37
GROUP = "vios-backup"
SCENARIO = "st37-vios-io-backup-restore"
BACKUP_PREFIX = "hmcpctl-live-st37-"
#: With `-r` the HMC restarts the VIOS inside `rstviosbk`; this bounds the wait for
#: its RMC connection and `ioslevel` to answer again afterwards, the same bound as
#: the SSH timeout the arm requires. Module-level so tests can shorten it.
RESTORE_WAIT_SECONDS = 2400
POLL_SECONDS = 30
#: `rstviosbk -r` restarts the VIOS and retries inside one CLI call, which the
#: default 300-second SSH timeout would cut off with the restore still running.
MIN_SSH_TIMEOUT = 2400

#: How the SSH transport words a command the HMC ran to completion and failed.
_HMC_EXIT = "failed with exit status"
_DISK_KINDS = frozenset({"VirtualDisk", "PhysicalVolume"})
#: VIOS listings compared against the baseline as sets of normalized lines.
_LISTINGS = ("lsmap -all", "lsmap -all -net", "lsmap -all -npiv", "lsdev -virtual")
#: VIOS-side names interpolated into a `viosvrcmd -c "..."` string, which the VIOS
#: shell parses again: only plain device-name characters are admitted.
_DEVICE_NAME = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")
_UUID = re.compile(r"[0-9A-Fa-f-]{36}")

MappingKey = tuple[Any, Any, Any, Any]


@dataclass(frozen=True)
class Target:
    """The VIOS serving the test partition, and that partition's one disk mapping."""

    vios: str
    vios_uuid: str
    mapping_id: str
    backing: str

    @property
    def adapter(self) -> str:
        return self.mapping_id.split("/", 1)[0]

    @property
    def vtd(self) -> str:
        return self.mapping_id.split("/", 1)[1]


@dataclass(frozen=True)
class Snapshot:
    """The VIOS state the restore must reproduce."""

    mappings: frozenset[MappingKey]
    listings: Mapping[str, str]


def vios_command(system: str, vios: str, command: str) -> str:
    """`viosvrcmd` running *command*, whose names the caller has validated."""
    return f'viosvrcmd -m {shlex.quote(system)} -p {shlex.quote(vios)} -c "{command}"'


def rmviosbk_command(system: str, vios: str, name: str) -> str:
    """The `rmviosbk` that removes this arm's backup; the runbook names it too."""
    return (
        f"rmviosbk -t viosioconfig -m {shlex.quote(system)} -p {shlex.quote(vios)} "
        f"-f {shlex.quote(name)}"
    )


def mkvdev_command(adapter: str, backing: str, vtd: str) -> str:
    """The VIOS command that recreates the test partition's disk mapping."""
    return f"mkvdev -vdev {backing} -vadapter {adapter} -dev {vtd}"


def normalized_lines(text: str) -> frozenset[str]:
    """A listing's non-empty lines, whitespace-collapsed, order ignored."""
    return frozenset(
        " ".join(line.split()) for line in text.splitlines() if line.strip()
    )


def has_disk_mapping(mappings: frozenset[MappingKey], target: Target) -> bool:
    return any(
        key[0] == target.mapping_id and key[3] == target.backing for key in mappings
    )


def _mapping_keys(data: object) -> frozenset[MappingKey] | None:
    if not isinstance(data, list) or not all(isinstance(m, Mapping) for m in data):
        return None
    return frozenset(
        (m.get("id"), m.get("lpar_uuid"), m.get("backing_kind"), m.get("backing_name"))
        for m in data
    )


def _backup_rows(data: object) -> list[tuple[str, str]] | None:
    """`(name, type)` per parsed catalog row, or None when the listing did not parse."""
    if not isinstance(data, list):
        return None
    rows = []
    for row in data:
        if not isinstance(row, Mapping):
            return None
        name, kind = row.get("name"), row.get("type")
        if not isinstance(name, str) or not isinstance(kind, str):
            return None
        rows.append((name, kind))
    return rows


@dataclass(frozen=True)
class _Restore:
    accepted: bool
    mapping_restored: bool
    baseline_restored: bool


class _Arm:
    """One ST37 run: the calls it makes and the outcomes it accumulates."""

    def __init__(self, client: Client, state: RunState) -> None:
        self.client = client
        self.state = state
        self.system = state.config.system_name
        self.lpar = state.config.lp3_name

    def record(self, tool: str, status: str, data: Any, note: str = "") -> None:
        self.state.record(SUBTASK, tool, status, data, note)

    async def run(self, label: str, cmd: str) -> str | None:
        """Run one HMC command, record it, and return its output when it passed."""
        status, data = await self.state.call(self.client, "hmc_run_command", cmd=cmd)
        self.record(f"hmc_run_command ({label})", status, data)
        return data if status == "PASS" and isinstance(data, str) else None

    async def on_vios(self, target: Target, command: str) -> str | None:
        return await self.run(command, vios_command(self.system, target.vios, command))

    async def mappings(
        self, target: Target, label: str
    ) -> frozenset[MappingKey] | None:
        status, data = await self.state.call(
            self.client,
            "hmc_list_storage_mappings",
            vios_name_or_uuid=target.vios_uuid,
            system_name_or_uuid=self.system,
        )
        self.record(f"hmc_list_storage_mappings ({label})", status, data)
        return _mapping_keys(data) if status == "PASS" else None

    async def snapshot(self, target: Target, label: str) -> Snapshot | None:
        mappings = await self.mappings(target, label)
        listings = {cmd: await self.on_vios(target, cmd) for cmd in _LISTINGS}
        if mappings is None or any(text is None for text in listings.values()):
            return None
        return Snapshot(mappings, {cmd: str(text) for cmd, text in listings.items()})

    async def preconditions(self) -> Target | None:
        """The arm's target, or None after recording why nothing will be changed."""
        if HMCConfig().ssh_timeout < MIN_SSH_TIMEOUT:
            return self.refuse(
                f"HMC_SSH_TIMEOUT is below {MIN_SSH_TIMEOUT}: the restore restarts "
                "the VIOS inside one rstviosbk call"
            )
        listing = await self.run(
            "partitions",
            f"lssyscfg -r lpar -m {shlex.quote(self.system)} -F name,lpar_env,state",
        )
        if listing is None:
            return self.refuse("the partition listing could not be read")
        rows = [row for row in csv.reader(io.StringIO(listing)) if row]
        states = {row[0]: row[2] for row in rows if len(row) == 3}
        if states.get(self.lpar) != "Not Activated":
            return self.refuse(f"{self.lpar} is not 'Not Activated'")
        clients = sorted(
            row[0] for row in rows if len(row) == 3 and row[1] != "vioserver"
        )
        if clients != [self.lpar]:
            return self.refuse(f"{self.lpar} is not the only client partition")
        status, data = await self.state.call(
            self.client, "hmc_list_vios", system_name_or_uuid=self.system
        )
        self.record("hmc_list_vios", status, data)
        if status != "PASS":
            return self.refuse("the VIOS listing could not be read")
        candidates = []
        for entry in entries(data):
            name = get_resource(entry).get("PartitionName")
            uuid = entry.get("UUID") or entry.get("uuid")
            if not isinstance(name, str) or not isinstance(uuid, str):
                continue
            status, found = await self.state.call(
                self.client,
                "hmc_list_storage_mappings",
                vios_name_or_uuid=uuid,
                lpar_name_or_uuid=self.lpar,
                system_name_or_uuid=self.system,
            )
            self.record(
                f"hmc_list_storage_mappings ({name} toward {self.lpar})", status, found
            )
            if status != "PASS" or not isinstance(found, list):
                return self.refuse(f"the mappings of {name} could not be read")
            disks = [
                m
                for m in found
                if isinstance(m, Mapping) and m.get("backing_kind") in _DISK_KINDS
            ]
            if disks:
                candidates.append((name, uuid, disks))
        if len(candidates) != 1 or len(candidates[0][2]) != 1:
            return self.refuse(
                f"{self.lpar} needs exactly one disk mapping on exactly one VIOS"
            )
        name, uuid, (disk,) = candidates[0]
        mapping_id, backing = disk.get("id"), disk.get("backing_name")
        names = str(mapping_id).split("/") + [str(backing)]
        if (
            not isinstance(mapping_id, str)
            or not isinstance(backing, str)
            or len(names) != 3
            or not all(_DEVICE_NAME.fullmatch(part) for part in names)
            or not _UUID.fullmatch(uuid)
        ):
            return self.refuse(
                "the disk mapping does not name a plain adapter, VTD and backing"
            )
        target = Target(name, uuid, mapping_id, backing)
        # Recorded, not gated: the operator rules on a management interface on the
        # SEA before the run (docs/live-testing.md).
        await self.on_vios(target, "lstcpip -interfaces")
        # The VIOS's management IP can sit on the SEA a restore rewrites, so the
        # operator's way back is the HMC console: require it, and RMC, up front.
        if not await self.rmc_active(target, "before backup"):
            return self.refuse(f"RMC is not active on {name}")
        serial = await self.run(
            "VIOS console adapter",
            f"lshwres -r virtualio --rsubtype serial --level lpar "
            f"-m {shlex.quote(self.system)} "
            f"--filter {shlex.quote(f'lpar_names={name}')} -F adapter_type,supports_hmc",
        )
        if serial is None or "server,1" not in serial.split():
            return self.refuse(f"{name} has no HMC console (virtual serial) adapter")
        return target

    async def rmc_active(self, target: Target, label: str) -> bool:
        """Whether the HMC reports the VIOS's RMC connection active."""
        state = await self.run(
            f"VIOS RMC state, {label}",
            f"lssyscfg -r lpar -m {shlex.quote(self.system)} "
            f"--filter {shlex.quote(f'lpar_names={target.vios}')} -F rmc_state",
        )
        return state is not None and state.strip() == "active"

    def refuse(self, reason: str) -> None:
        self.state.skip(
            SUBTASK, "vios-backup preconditions", f"{reason}; nothing changed"
        )

    async def raw_listing(self, target: Target, label: str) -> str | None:
        return await self.run(
            f"lsviosbk raw, {label}",
            f'lsviosbk -F --header --filter "vios_uuids={target.vios_uuid}"',
        )

    async def listed(self, target: Target, label: str) -> tuple[str, Any]:
        status, data = await self.state.call(
            self.client, "hmc_list_vios_backups", vios_name_or_uuid=target.vios_uuid
        )
        if label:
            self.record(f"hmc_list_vios_backups ({label})", status, data)
        return status, data

    async def wait_for_vios(self, target: Target) -> float | None:
        """Seconds until RMC is active and the VIOS answers `ioslevel`, or None.

        None means the bound passed first: the operator recovers through the HMC
        console, and the arm changes nothing more.
        """
        started = time.monotonic()
        command = vios_command(self.system, target.vios, "ioslevel")
        while True:
            status, data = "FAIL", "RMC not active"
            if await self.rmc_active(target, "after restore"):
                status, data = await self.state.call(
                    self.client, "hmc_run_command", cmd=command
                )
            elapsed = time.monotonic() - started
            if status == "PASS":
                self.record(
                    "hmc_run_command (ioslevel after restore)",
                    status,
                    data,
                    f"{elapsed:.0f}s",
                )
                return elapsed
            if elapsed >= RESTORE_WAIT_SECONDS:
                self.record(
                    "hmc_run_command (ioslevel after restore)",
                    "FAIL",
                    data,
                    f"no answer within {RESTORE_WAIT_SECONDS}s",
                )
                return None
            await asyncio.sleep(POLL_SECONDS)

    def compare(self, baseline: Snapshot, after: Snapshot) -> bool:
        """Record each source's difference from the baseline; True when none differ."""
        same = after.mappings == baseline.mappings
        self.record(
            "baseline compare (REST mappings)",
            "PASS" if same else "FAIL",
            {
                "missing": sorted(map(str, baseline.mappings - after.mappings)),
                "added": sorted(map(str, after.mappings - baseline.mappings)),
            },
        )
        for cmd in _LISTINGS:
            before, now = baseline.listings[cmd], after.listings[cmd]
            equal = normalized_lines(before) == normalized_lines(now)
            diff = "\n".join(
                difflib.unified_diff(before.splitlines(), now.splitlines(), lineterm="")
            )
            self.record(f"baseline compare ({cmd})", "PASS" if equal else "FAIL", diff)
            same = same and equal
        return same


async def exercise_vios_backup(client: Client, state: RunState) -> None:
    print("\n=== ST37: VIOS I/O configuration backup and restore ===")
    if state.group != GROUP:
        state.skip(SUBTASK, "vios-backup arm", "runs only in the vios-backup arm")
        return
    arm = _Arm(client, state)
    target = await arm.preconditions()
    if target is None:
        return
    baseline = await arm.snapshot(target, "baseline")
    if baseline is None or not has_disk_mapping(baseline.mappings, target):
        arm.refuse("the baseline could not be read")
        return
    name = BACKUP_PREFIX + secrets.token_hex(4)
    artifacts = state.artifacts
    artifacts.vios_backup_vios = target.vios
    artifacts.vios_backup_vios_uuid = target.vios_uuid
    artifacts.vios_backup_name = name
    artifacts.vios_backup_mapping = target.mapping_id
    artifacts.vios_backup_backing = target.backing

    await arm.raw_listing(target, "before")
    status, before = await arm.listed(target, "before backup")
    before_rows = _backup_rows(before) if status == "PASS" else None

    status, data = await state.call(
        client,
        "hmc_backup_vios",
        system_name_or_uuid=arm.system,
        vios_name_or_uuid=target.vios_uuid,
        backup_name=name,
        backup_type="viosioconfig",
    )
    arm.record("hmc_backup_vios (viosioconfig)", status, data)
    backup_accepted = status == "PASS"
    raw_after = await arm.raw_listing(target, "after")
    status, after_listing = await arm.listed(target, "")
    after_rows = _backup_rows(after_listing) if status == "PASS" else None
    run_rows = [kind for row_name, kind in after_rows or [] if row_name == name]
    state.record_verified(
        SUBTASK,
        "hmc_list_vios_backups",
        operation="vios.list_backups",
        scenario=SCENARIO,
        assertions=[
            Assertion("listing-parsed", after_rows is not None),
            Assertion("listing-names-run-backup", run_rows == ["viosioconfig"]),
        ],
        cleanup="not-required",
        data=after_listing,
    )
    exists = bool(run_rows) or (raw_after is not None and name in raw_after)

    restore, settled = None, True
    if backup_accepted and exists:
        restore, settled = await _round_trip(arm, target, baseline, name)
    if restore is None:
        state.skip(
            SUBTASK,
            "hmc_restore_vios (viosioconfig)",
            "not attempted: the backup was not confirmed or the delta did not complete",
        )
    final = await arm.mappings(target, "final")
    mapping_back = final is not None and has_disk_mapping(final, target)

    backup_cleanup = "not-run"
    # Unsettled means the VIOS never answered after the restore or the read after it
    # failed: the HMC may still be restoring from this backup, so it is kept.
    if exists and mapping_back and settled:
        await arm.run("rmviosbk", rmviosbk_command(arm.system, target.vios, name))
        # Absence is read from the source that showed presence: a parsed listing
        # that never named the backup cannot show it gone.
        if run_rows:
            status, listing = await arm.listed(target, "after rmviosbk")
            rows = _backup_rows(listing) if status == "PASS" else None
            gone = rows is not None and all(row_name != name for row_name, _ in rows)
        else:
            raw = await arm.raw_listing(target, "after rmviosbk")
            gone = raw is not None and name not in raw
        backup_cleanup = "passed" if gone else "failed"
    state.record_verified(
        SUBTASK,
        "hmc_backup_vios",
        operation="vios.backup",
        scenario=SCENARIO,
        assertions=[
            Assertion("backup-accepted", backup_accepted),
            Assertion(
                "backup-newly-listed",
                before_rows is not None
                and all(row_name != name for row_name, _ in before_rows)
                and bool(run_rows),
            ),
            Assertion("backup-type-viosioconfig", run_rows == ["viosioconfig"]),
        ],
        cleanup=backup_cleanup,
        data=data,
    )
    if restore is not None:
        state.record_verified(
            SUBTASK,
            "hmc_restore_vios",
            operation="vios.restore",
            scenario=SCENARIO,
            assertions=[
                Assertion("restore-accepted", restore.accepted),
                Assertion("mapping-restored", restore.mapping_restored),
                Assertion("baseline-restored", restore.baseline_restored),
            ],
            cleanup="passed" if mapping_back else "failed",
            data={"backup": name},
        )


async def _round_trip(
    arm: _Arm, target: Target, baseline: Snapshot, name: str
) -> tuple[_Restore | None, bool]:
    """Delta, restore, assert and fall back.

    Returns the restore's outcome (None when it was not attempted) and whether the
    VIOS settled: answered after the restore and was read again.
    """
    await arm.on_vios(target, f"rmvdev -vtd {target.vtd}")
    removed = await arm.mappings(target, "after rmvdev")
    adapter = await arm.on_vios(target, f"lsmap -vadapter {target.adapter}")
    if removed is None or has_disk_mapping(removed, target) or adapter is None:
        return None, removed is not None

    started = time.monotonic()
    status, data = await arm.state.call(
        arm.client,
        "hmc_restore_vios",
        system_name_or_uuid=arm.system,
        vios_name_or_uuid=target.vios_uuid,
        backup_name=name,
        backup_type="viosioconfig",
        restart_if_required=True,
    )
    arm.record(
        "hmc_restore_vios (viosioconfig, -r)",
        status,
        data,
        f"{time.monotonic() - started:.0f}s",
    )
    accepted = status == "PASS"
    # Only a passed call or an exit status the HMC reported ends `rstviosbk`. A
    # timeout or a dropped session may leave it running, still restarting the VIOS
    # and restoring: assert what is there, but change nothing.
    ended = status == "PASS" or _HMC_EXIT in str(getattr(data, "message", data))
    in_flight = not ended
    restored = baseline_back = settled = False
    if await arm.wait_for_vios(target) is not None:
        after = await arm.snapshot(target, "after restore")
        settled = after is not None and not in_flight
        if after is not None:
            restored = has_disk_mapping(after.mappings, target)
            baseline_back = arm.compare(baseline, after)
            if not restored and settled:
                await arm.on_vios(
                    target, mkvdev_command(target.adapter, target.backing, target.vtd)
                )
    return _Restore(accepted, restored, baseline_back), settled
