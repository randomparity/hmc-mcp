from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

from ..xmlutil import escapes_string_arguments
from .common import document_envelope

PARTITION_TYPES: tuple[PartitionType, ...] = ("AIX/Linux", "OS400", "Virtual IO Server")
OS_TYPES = ("aix", "linux", "ibmi")
KEYLOCK_POSITIONS = ("normal", "manual", "auto")
PartitionType = Literal["AIX/Linux", "OS400", "Virtual IO Server"]
OsType = Literal["aix", "linux", "ibmi"]
Keylock = Literal["normal", "manual", "auto"]
SharingMode = Literal[
    "capped",
    "uncapped",
    "keep_idle_procs",
    "share_idle_procs",
    "share_idle_procs_active",
    "share_idle_procs_always",
]
SHARING_MODES = frozenset(get_args(SharingMode))


def lpar_envelope(body: str) -> str:
    return document_envelope("LogicalPartition", body)


@dataclass(frozen=True)
class LparResources:
    min_memory: int | None = field(
        default=None, metadata={"description": "Minimum memory in MiB."}
    )
    desired_memory: int | None = field(
        default=None, metadata={"description": "Desired memory in MiB."}
    )
    max_memory: int | None = field(
        default=None, metadata={"description": "Maximum memory in MiB."}
    )
    dedicated: bool | None = field(
        default=None,
        metadata={
            "description": "Whether processors are dedicated rather than shared."
        },
    )
    min_procs: float | None = field(
        default=None,
        metadata={
            "description": "Minimum whole CPUs when dedicated, or processing units when shared."
        },
    )
    desired_procs: float | None = field(
        default=None,
        metadata={
            "description": "Desired whole CPUs when dedicated, or processing units when shared."
        },
    )
    max_procs: float | None = field(
        default=None,
        metadata={
            "description": "Maximum whole CPUs when dedicated, or processing units when shared."
        },
    )
    min_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Minimum virtual processor count for a shared partition."
        },
    )
    desired_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Desired virtual processor count for a shared partition."
        },
    )
    max_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Maximum virtual processor count for a shared partition."
        },
    )
    sharing_mode: SharingMode | None = field(
        default=None, metadata={"description": "HMC processor sharing mode."}
    )
    uncapped: bool | None = field(
        default=None,
        metadata={
            "description": "Whether a shared partition may consume spare processing capacity."
        },
    )


def _memory_config(resources: LparResources) -> str:
    fields = (
        ("DesiredMemory", resources.desired_memory),
        ("MaximumMemory", resources.max_memory),
        ("MinimumMemory", resources.min_memory),
    )
    if all(value is None for _, value in fields):
        return ""
    parts = [
        '  <PartitionMemoryConfiguration kb="CUD" kxe="false">',
        "    <Metadata><Atom/></Metadata>",
    ]
    parts.extend(
        f'    <{name} kb="CUD" kxe="false">{value}</{name}>'
        for name, value in fields
        if value is not None
    )
    return "\n".join([*parts, "  </PartitionMemoryConfiguration>"])


def _validate_sharing_mode(value: SharingMode | None) -> None:
    if value is not None and (not isinstance(value, str) or value not in SHARING_MODES):
        raise ValueError(
            f"sharing_mode must be one of: {', '.join(sorted(SHARING_MODES))}"
        )


def _dedicated_processor_body(resources: LparResources) -> list[str]:
    parts = [
        '    <DedicatedProcessorConfiguration kb="CUD" kxe="false">',
        "      <Metadata><Atom/></Metadata>",
    ]
    for name, value in (
        ("DesiredProcessors", resources.desired_procs),
        ("MaximumProcessors", resources.max_procs),
        ("MinimumProcessors", resources.min_procs),
    ):
        if value is not None:
            parts.append(f'      <{name} kb="CUD" kxe="false">{int(value)}</{name}>')
    parts.extend(
        (
            "    </DedicatedProcessorConfiguration>",
            '    <HasDedicatedProcessors kb="CUD" kxe="false">true</HasDedicatedProcessors>',
        )
    )
    if resources.sharing_mode:
        parts.append(
            f'    <SharingMode kb="CUD" kxe="false">{resources.sharing_mode}</SharingMode>'
        )
    return parts


def _shared_processor_body(resources: LparResources) -> list[str]:
    parts = (
        [
            '    <HasDedicatedProcessors kb="CUD" kxe="false">false</HasDedicatedProcessors>'
        ]
        if resources.dedicated is False
        else []
    )
    parts.extend(
        (
            '    <SharedProcessorConfiguration kb="CUD" kxe="false">',
            "      <Metadata><Atom/></Metadata>",
        )
    )
    for name, value in (
        ("DesiredProcessingUnits", resources.desired_procs),
        ("MaximumProcessingUnits", resources.max_procs),
        ("MinimumProcessingUnits", resources.min_procs),
        ("DesiredVirtualProcessors", resources.desired_vcpus),
        ("MaximumVirtualProcessors", resources.max_vcpus),
        ("MinimumVirtualProcessors", resources.min_vcpus),
    ):
        if value is not None:
            rendered = (
                int(value) if isinstance(value, float) and value.is_integer() else value
            )
            parts.append(f'      <{name} kb="CUD" kxe="false">{rendered}</{name}>')
    if resources.uncapped is False:
        parts.append('      <UncappedWeight kb="CUD" kxe="false">0</UncappedWeight>')
    parts.append("    </SharedProcessorConfiguration>")
    mode = (
        "uncapped"
        if resources.uncapped is True
        else resources.sharing_mode
        or ("capped" if resources.uncapped is False else None)
    )
    if mode:
        parts.append(f'    <SharingMode kb="CUD" kxe="false">{mode}</SharingMode>')
    return parts


def _processor_config(resources: LparResources) -> str:
    _validate_sharing_mode(resources.sharing_mode)
    if not any(
        value is not None
        for value in (
            resources.min_procs,
            resources.desired_procs,
            resources.max_procs,
            resources.min_vcpus,
            resources.desired_vcpus,
            resources.max_vcpus,
        )
    ):
        return ""
    body = (
        _dedicated_processor_body
        if resources.dedicated is True
        else _shared_processor_body
    )
    return "\n".join(
        [
            '  <PartitionProcessorConfiguration kb="CUD" kxe="false">',
            "    <Metadata><Atom/></Metadata>",
            *body(resources),
            "  </PartitionProcessorConfiguration>",
        ]
    )


@escapes_string_arguments
def build_lpar_document(
    name: str | None,
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    resources: LparResources | None = None,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
) -> str:
    """Build a LogicalPartition document for PUT (create) or POST (modify).

    For a create, `name` is required; the rest are optional (the HMC supplies
    defaults). For a modify, supply only the fields to change (pass name=None
    to omit it). `resources` carries the memory/processor fields; None means
    no resource block is emitted.

    os_type: target OS type — ``aix``, ``linux``, or ``ibmi``.
    keylock: initial keylock position — ``normal``, ``manual``, or ``auto``.
    max_virtual_slots: maximum number of virtual I/O slots.
    """
    if partition_type not in PARTITION_TYPES:
        raise ValueError(
            f"partition_type must be one of {PARTITION_TYPES}, got {partition_type!r}"
        )
    if os_type is not None and os_type not in OS_TYPES:
        raise ValueError(f"os_type must be one of {OS_TYPES}, got {os_type!r}")
    if keylock is not None and keylock not in KEYLOCK_POSITIONS:
        raise ValueError(f"keylock must be one of {KEYLOCK_POSITIONS}, got {keylock!r}")

    resources = resources or LparResources()

    body_parts = ["  <Metadata><Atom/></Metadata>"]
    if partition_id is not None:
        body_parts.append(
            f'  <PartitionID kb="COD" kxe="false">{partition_id}</PartitionID>'
        )

    mem = _memory_config(resources)
    if mem:
        body_parts.append(mem)

    if keylock is not None:
        body_parts.append(
            f'  <KeylockPosition kb="CUD" kxe="false">{keylock}</KeylockPosition>'
        )

    if max_virtual_slots is not None:
        body_parts.append(
            f'  <MaximumVirtualIoSlots kb="CUD" kxe="false">{max_virtual_slots}</MaximumVirtualIoSlots>'
        )

    if name is not None:
        body_parts.append(
            f'  <PartitionName kb="CUR" kxe="false">{name}</PartitionName>'
        )

    if os_type is not None:
        body_parts.append(
            f'  <OperatingSystemType kb="CUD" kxe="false">{os_type}</OperatingSystemType>'
        )

    proc = _processor_config(resources)
    if proc:
        body_parts.append(proc)

    body_parts.append(
        f'  <PartitionType kb="COD" kxe="false">{partition_type}</PartitionType>'
    )

    body = "\n".join(body_parts)
    return lpar_envelope(body)


VIOS_DEFAULT_RESOURCES = LparResources(
    min_memory=512,
    desired_memory=4096,
    max_memory=8192,
    desired_vcpus=2,
    min_vcpus=1,
    max_vcpus=4,
    desired_procs=0.5,
    min_procs=0.1,
    max_procs=1.0,
    uncapped=True,
)


@escapes_string_arguments
def build_vios_document(
    name: str,
    resources: LparResources = VIOS_DEFAULT_RESOURCES,
) -> str:
    """Build a LogicalPartition document for creating a Virtual IO Server.

    Wraps build_lpar_document with partition_type='Virtual IO Server' and
    shared-processor defaults appropriate for VIOS provisioning.
    """
    return build_lpar_document(
        name=name,
        partition_type="Virtual IO Server",
        resources=resources,
    )


@escapes_string_arguments
def build_dlpar_proc_document(resources: LparResources | None = None) -> str:
    """Minimal LogicalPartition document containing only PartitionProcessorConfiguration.

    Used for DLPAR processor hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    For shared partitions, procs are processing units (may be fractional, e.g. 0.5);
    vcpus are the virtual processor counts (ints).
    Set dedicated=True to assign whole CPUs; dedicated=False (default) for shared.
    """
    resources = resources or LparResources()
    proc = _processor_config(resources)
    body = "  <Metadata><Atom/></Metadata>"
    if proc:
        body = body + "\n" + proc
    return lpar_envelope(body)


@escapes_string_arguments
def build_dlpar_mem_document(resources: LparResources | None = None) -> str:
    """Minimal LogicalPartition document containing only PartitionMemoryConfiguration.

    Used for DLPAR memory hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    Memory values are in MiB. Only the min/desired/max memory fields of
    `resources` are emitted; processor fields are ignored.
    """
    resources = resources or LparResources()
    mem = _memory_config(resources)
    body = "  <Metadata><Atom/></Metadata>"
    if mem:
        body = body + "\n" + mem
    return lpar_envelope(body)
