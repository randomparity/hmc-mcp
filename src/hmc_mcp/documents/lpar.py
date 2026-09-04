from __future__ import annotations

from ..documents_shared import lpar_envelope
from ..xmlutil import escapes_string_arguments
from .common import (
    KEYLOCK_POSITIONS,
    OS_TYPES,
    PARTITION_TYPES,
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    _memory_config,
    _processor_config,
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
