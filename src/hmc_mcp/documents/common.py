"""XML request-document builders for the HMC REST API.

Each ``build_*_document`` produces the XML body for one HMC operation: LPAR /
VIOS / managed-system create and modify (LogicalPartition and ManagedSystem
documents), DLPAR processor/memory change documents, virtual adapters
(vSCSI / vFC / network), storage (volume groups, virtual disks, vSCSI
mappings), networking (virtual networks), virtual media (media repository,
optical media, brokered file upload and ISO import), and web resources
(session logon, HMC users, password policies, LDAP configuration).

The HMC creates an LPAR from a PUT of a LogicalPartition document to
/rest/api/uom/ManagedSystem/{uuid}/LogicalPartition, and modifies one with a
POST of a (partial) document to /rest/api/uom/LogicalPartition/{uuid}.

VIOS partitions use the same endpoint; partition_type="Virtual IO Server"
distinguishes them. build_vios_document is a thin wrapper that fixes that
type and exposes only the parameters relevant to VIOS provisioning.
A managed system is modified with a POST of a (partial) ManagedSystem document
to /rest/api/uom/ManagedSystem/{uuid}.

Only the fields we set are included; the HMC fills in everything else with
defaults (profiles, virtual adapters, boot mode, etc.).
"""

# Shared symbols are imported by domain modules through this module.
# ruff: noqa: F401
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml import ElementTree as DET

from ..documents_shared import document_envelope, lpar_envelope
from ..xmlutil import ATOM_NS, WEB_NS, escapes_string_arguments

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"

# Valid partition environments.
PARTITION_TYPES: tuple[PartitionType, ...] = ("AIX/Linux", "OS400", "Virtual IO Server")

# Closed vocabularies for the string/int attributes the document builders
# validate. The sets mirror what the HMC accepts for each property; os_type
# and keylock are LogicalPartition attributes, the rest are ManagedSystem
# modify attributes (vocabularies per IBM's ansible-power-hmc collection).
OS_TYPES = ("aix", "linux", "ibmi")
KEYLOCK_POSITIONS = ("normal", "manual", "auto")
# power_off_policy: 1 powers the managed system off after all partitions
# shut down, 0 leaves it powered on.
POWER_OFF_POLICIES = (0, 1)
POWER_ON_LPAR_START_POLICIES = ("autostart", "userinit", "autorecovery")
MEM_MIRRORING_MODES = ("none", "sys_firmware_only")

# Type aliases for the closed sets above, used to annotate the document-builder
# and MCP-tool parameters so MCP renders them as enums and static checkers catch
# typos before they reach the HMC.
PartitionType = Literal["AIX/Linux", "OS400", "Virtual IO Server"]
OsType = Literal["aix", "linux", "ibmi"]
Keylock = Literal["normal", "manual", "auto"]
PowerOffPolicy = Literal[0, 1]
PowerOnLparStartPolicy = Literal["autostart", "userinit", "autorecovery"]
MemoryMirroringMode = Literal["none", "sys_firmware_only"]
StorageKind = Literal["PhysicalVolume", "VirtualDisk"]
AuthenticationType = Literal["Local", "LDAP", "Kerberos"]
SharingMode = Literal[
    "capped",
    "uncapped",
    "keep_idle_procs",
    "share_idle_procs",
    "share_idle_procs_active",
    "share_idle_procs_always",
]

STORAGE_KINDS = frozenset(get_args(StorageKind))
AUTHENTICATION_TYPES = frozenset(get_args(AuthenticationType))
SHARING_MODES = frozenset(get_args(SharingMode))


# Boot device selectors for LPAR boot order configuration.
# These match IBM HMC boot device types and are used to construct
# PendingBootString values for boot order operations.
BOOT_DEVICE_SELECTORS: tuple[BootDeviceSelector, ...] = (
    "cd",  # Optical/CD-ROM device
    "disk",  # Disk device (SCSI, direct-attached, SAN)
    "network",  # Network boot (PXE, NIM, etc.)
)

# Type alias for boot device selectors used in PendingBootString construction.
BootDeviceSelector = Literal["cd", "disk", "network"]


@dataclass(frozen=True)
class LparResources:
    """Memory / processor resource fields shared by LPAR create, modify, DLPAR.

    This is the single source of truth for the resource vocabulary used by the
    LPAR document builders (``build_lpar_document``, ``build_dlpar_*``) and the
    MCP/CLI tool parameters that feed them. Memory values are in MiB. For
    shared partitions (``dedicated=False``) ``procs`` are processing units
    (may be fractional, e.g. 0.5) and ``vcpus`` are virtual processor counts;
    with ``dedicated=True`` the ``procs`` are whole CPU counts. A ``None``
    field means "not specified": the HMC supplies a default on create and
    leaves the value unchanged on modify / DLPAR. ``dedicated`` and ``uncapped``
    are also ``None``-able so a modify can change processor counts without
    silently flipping the sharing mode.
    """

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
    min_memory = resources.min_memory
    desired_memory = resources.desired_memory
    max_memory = resources.max_memory
    if min_memory is None and desired_memory is None and max_memory is None:
        return ""
    parts = [
        '  <PartitionMemoryConfiguration kb="CUD" kxe="false">',
        "    <Metadata><Atom/></Metadata>",
    ]
    if desired_memory is not None:
        parts.append(
            f'    <DesiredMemory kb="CUD" kxe="false">{desired_memory}</DesiredMemory>'
        )
    if max_memory is not None:
        parts.append(
            f'    <MaximumMemory kb="CUD" kxe="false">{max_memory}</MaximumMemory>'
        )
    if min_memory is not None:
        parts.append(
            f'    <MinimumMemory kb="CUD" kxe="false">{min_memory}</MinimumMemory>'
        )
    parts.append("  </PartitionMemoryConfiguration>")
    return "\n".join(parts)


def _validate_sharing_mode(sharing_mode: SharingMode | None) -> None:
    if sharing_mode is not None and (
        not isinstance(sharing_mode, str) or sharing_mode not in SHARING_MODES
    ):
        legal_values = ", ".join(sorted(SHARING_MODES))
        raise ValueError(f"sharing_mode must be one of: {legal_values}")


def _dedicated_processor_body(resources: LparResources) -> list[str]:
    parts = [
        '    <DedicatedProcessorConfiguration kb="CUD" kxe="false">',
        "      <Metadata><Atom/></Metadata>",
    ]
    fields = (
        ("DesiredProcessors", resources.desired_procs),
        ("MaximumProcessors", resources.max_procs),
        ("MinimumProcessors", resources.min_procs),
    )
    parts.extend(
        f'      <{name} kb="CUD" kxe="false">{int(value)}</{name}>'
        for name, value in fields
        if value is not None
    )
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
    parts: list[str] = []
    if resources.dedicated is False:
        parts.append(
            '    <HasDedicatedProcessors kb="CUD" kxe="false">false</HasDedicatedProcessors>'
        )
    parts.extend(
        (
            '    <SharedProcessorConfiguration kb="CUD" kxe="false">',
            "      <Metadata><Atom/></Metadata>",
        )
    )
    fields = (
        ("DesiredProcessingUnits", resources.desired_procs),
        ("MaximumProcessingUnits", resources.max_procs),
        ("MinimumProcessingUnits", resources.min_procs),
        ("DesiredVirtualProcessors", resources.desired_vcpus),
        ("MaximumVirtualProcessors", resources.max_vcpus),
        ("MinimumVirtualProcessors", resources.min_vcpus),
    )
    for name, value in fields:
        if value is not None:
            rendered = (
                int(value) if isinstance(value, float) and value.is_integer() else value
            )
            parts.append(f'      <{name} kb="CUD" kxe="false">{rendered}</{name}>')
    if resources.uncapped is False:
        parts.append('      <UncappedWeight kb="CUD" kxe="false">0</UncappedWeight>')
    parts.append("    </SharedProcessorConfiguration>")
    sharing_mode = (
        "uncapped"
        if resources.uncapped is True
        else resources.sharing_mode
        or ("capped" if resources.uncapped is False else None)
    )
    if sharing_mode:
        parts.append(
            f'    <SharingMode kb="CUD" kxe="false">{sharing_mode}</SharingMode>'
        )
    return parts


def _processor_config(resources: LparResources) -> str:
    """Build PartitionProcessorConfiguration.

    dedicated=True  -> DedicatedProcessorConfiguration (whole CPUs).
    dedicated=False -> SharedProcessorConfiguration (processing units) plus
                       virtual processor min/desired/max.
    dedicated=None  -> the sharing mode is left unchanged: the processor
                       values are still emitted but HasDedicatedProcessors is
                       omitted so the HMC applies them to the current mode.
    For shared partitions, procs are processing units (may be fractional,
    e.g. 0.5); vcpus are the virtual processor counts (ints). uncapped=None
    likewise leaves SharingMode / UncappedWeight unchanged.
    """
    _validate_sharing_mode(resources.sharing_mode)
    have_procs = any(
        value is not None
        for value in (resources.min_procs, resources.desired_procs, resources.max_procs)
    )
    have_vcpus = any(
        value is not None
        for value in (resources.min_vcpus, resources.desired_vcpus, resources.max_vcpus)
    )
    if not (have_procs or have_vcpus):
        return ""

    parts = [
        '  <PartitionProcessorConfiguration kb="CUD" kxe="false">',
        "    <Metadata><Atom/></Metadata>",
    ]

    body_builder = (
        _dedicated_processor_body
        if resources.dedicated is True
        else _shared_processor_body
    )
    parts.extend(body_builder(resources))

    parts.append("  </PartitionProcessorConfiguration>")
    return "\n".join(parts)

