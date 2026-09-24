from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 - reads an element the caller parsed with defusedxml
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
            "description": "Whether processors are dedicated rather than shared. On a "
            "modify it must match the partition's current mode; a switch is refused."
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
        '  <PartitionMemoryConfiguration kb="CUD" kxe="false" schemaVersion="V1_0">',
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


def _render_units(value: float) -> str:
    return str(int(value) if isinstance(value, float) and value.is_integer() else value)


def _shared_sharing_mode(resources: LparResources) -> str | None:
    return (
        "uncapped"
        if resources.uncapped is True
        else resources.sharing_mode
        or ("capped" if resources.uncapped is False else None)
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
            parts.append(f'      <{name} kb="CUD" kxe="false">{_render_units(value)}</{name}>')
    if resources.uncapped is False:
        parts.append('      <UncappedWeight kb="CUD" kxe="false">0</UncappedWeight>')
    parts.append("    </SharedProcessorConfiguration>")
    mode = _shared_sharing_mode(resources)
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
    name: str,
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    resources: LparResources | None = None,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
) -> str:
    """Build a LogicalPartition document to PUT (create).

    `name` is required; the rest are optional (the HMC supplies defaults).
    `resources` carries the memory/processor fields; None means no resource
    block is emitted. A modify goes through :func:`partition_updates` instead:
    V10R3 rejects a sparse LogicalPartition POST, and ``PartitionType`` is
    create-only.

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

    body_parts.append(f'  <PartitionName kb="CUR" kxe="false">{name}</PartitionName>')

    if os_type is not None:
        body_parts.append(
            f'  <OperatingSystemType kb="ROR" kxe="false">{os_type}</OperatingSystemType>'
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


_UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"
_PPC = "PartitionProcessorConfiguration"
_MODE_SWITCH_REFUSAL = (
    "Refusing to switch the partition between dedicated and shared processors: the "
    "partition read carries no configuration for the other mode, so nothing was written. "
    "Omit `dedicated`, or pass the partition's current mode."
)


def _changed(pairs: tuple[tuple[str, object], ...]) -> dict[str, str]:
    return {path: str(value) for path, value in pairs if value is not None}


def _processor_updates(lpar: ET.Element, resources: LparResources) -> dict[str, str]:
    _validate_sharing_mode(resources.sharing_mode)
    current = lpar.findtext(f"{{{_UOM_NS}}}{_PPC}/{{{_UOM_NS}}}HasDedicatedProcessors")
    dedicated = current == "true" if current is not None else bool(resources.dedicated)
    if resources.dedicated is not None and resources.dedicated != dedicated:
        raise ValueError(_MODE_SWITCH_REFUSAL)
    if dedicated:
        if resources.uncapped is not None or any(
            value is not None
            for value in (resources.min_vcpus, resources.desired_vcpus, resources.max_vcpus)
        ):
            raise ValueError(
                "Virtual processor counts and capping apply only to a shared-processor "
                "partition; this partition has dedicated processors. Nothing was written."
            )
        config = f"{_PPC}/DedicatedProcessorConfiguration"
        updates = _changed(
            tuple(
                (f"{config}/{name}", None if value is None else int(value))
                for name, value in (
                    ("DesiredProcessors", resources.desired_procs),
                    ("MaximumProcessors", resources.max_procs),
                    ("MinimumProcessors", resources.min_procs),
                )
            )
        )
        mode = resources.sharing_mode
    else:
        config = f"{_PPC}/SharedProcessorConfiguration"
        updates = _changed(
            tuple(
                (f"{config}/{name}", None if value is None else _render_units(value))
                for name, value in (
                    ("DesiredProcessingUnits", resources.desired_procs),
                    ("MaximumProcessingUnits", resources.max_procs),
                    ("MinimumProcessingUnits", resources.min_procs),
                )
            )
            + (
                (f"{config}/DesiredVirtualProcessors", resources.desired_vcpus),
                (f"{config}/MaximumVirtualProcessors", resources.max_vcpus),
                (f"{config}/MinimumVirtualProcessors", resources.min_vcpus),
            )
        )
        mode = _shared_sharing_mode(resources)
    if mode:
        updates[f"{_PPC}/SharingMode"] = mode
    if updates:
        updates[f"{_PPC}/HasDedicatedProcessors"] = str(dedicated).lower()
    return updates


def partition_updates(
    lpar: ET.Element,
    *,
    name: str | None = None,
    resources: LparResources | None = None,
) -> dict[str, str]:
    """Map a rename or resource change to the partition elements it sets.

    Returns element text keyed by a slash path relative to *lpar*, the
    ``LogicalPartition`` element of a whole-partition read, for
    ``HMCClient.update_logical_partition``. Processor paths follow the
    partition's current mode (``HasDedicatedProcessors``); a requested
    ``dedicated`` value that differs from it raises ``ValueError``, because
    switching needs a configuration element the read does not carry.
    ``UncappedWeight`` is never written: V10R3 drops it on capping and recreates
    it as 0 on uncapping.
    """
    resources = resources or LparResources()
    updates = {} if name is None else {"PartitionName": name}
    updates |= _changed(
        (
            ("PartitionMemoryConfiguration/DesiredMemory", resources.desired_memory),
            ("PartitionMemoryConfiguration/MaximumMemory", resources.max_memory),
            ("PartitionMemoryConfiguration/MinimumMemory", resources.min_memory),
        )
    )
    return updates | _processor_updates(lpar, resources)
