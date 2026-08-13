"""MCP tool for the hmc_provision_lpar composite workflow.

Composes create_logical_partition + add_network_adapter + add_vscsi_adapter +
map_storage_to_lpar + power-on into a single call with a structured per-step
result and an optional dry-run that validates preconditions only.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _resolve_system_uuid,
    _run,
    mcp,
)

from .client import HMCError
from .common import client_from_env
from .config import HMCConfig
from .documents import LparResources, PartitionType, build_lpar_document
from .jobs import power_on_lpar_job
from .ssh import HMCCLIError, create_lpar_via_cli


# ---------------------------------------------------------------------- #
# Precondition helpers
# ---------------------------------------------------------------------- #


async def _check_name_unique(hmc, name: str) -> None:
    """Raise ValueError if an LPAR with *name* already exists."""
    existing = await hmc.find_partition_by_name(name)
    if existing:
        raise ValueError(
            f"An LPAR named {name!r} already exists "
            f"(UUID {existing.get('UUID')!r}). Choose a different name "
            "or delete the existing partition first."
        )


async def _check_vlan_exists(hmc, system_uuid: str, port_vlan_id: int) -> None:
    """Raise ValueError if no VirtualNetwork with *port_vlan_id* exists."""
    networks = await hmc.list_virtual_networks(system_uuid)
    for net in networks:
        res = net.get("Resource") or {}
        vlan = res.get("NetworkVLANID")
        try:
            if int(vlan) == port_vlan_id:
                return
        except (TypeError, ValueError):
            pass
    raise ValueError(
        f"No VirtualNetwork with VLAN ID {port_vlan_id} found on system "
        f"{system_uuid!r}. Use hmc_list_virtual_networks to list available VLANs."
    )


async def _check_vg_exists(hmc, vios_uuid: str, vg_uuid: str) -> None:
    """Raise ValueError if no VolumeGroup with *vg_uuid* exists on *vios_uuid*."""
    vgs = await hmc.list_volume_groups(vios_uuid)
    found = any(vg.get("UUID") == vg_uuid for vg in vgs)
    if not found:
        raise ValueError(
            f"VolumeGroup {vg_uuid!r} not found on VIOS {vios_uuid!r}. "
            "Use hmc_list_volume_groups to list available volume groups."
        )


# ---------------------------------------------------------------------- #
# Step runner
# ---------------------------------------------------------------------- #


def _step(name: str, status: str, result: Any = None) -> dict[str, Any]:
    """Build a single step-result dict."""
    entry: dict[str, Any] = {"step": name, "status": status}
    if result is not None:
        entry["result"] = result
    return entry


# ---------------------------------------------------------------------- #
# Tool
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_provision_lpar(
    system_name_or_uuid: str,
    name: str,
    port_vlan_id: int,
    vios_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    storage_name: str,
    partition_type: PartitionType = "AIX/Linux",
    min_memory: int = 256,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 1,
    max_vcpus: int = 2,
    storage_kind: str = "VirtualDisk",
    vg_uuid: str | None = None,
    power_on: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Provision a new LPAR end-to-end: create, add network adapter, add vSCSI
    adapter, map disk storage, and power on — in a single call.

    **Always validates preconditions first** (name uniqueness, VLAN existence,
    volume-group existence). Pass ``dry_run=True`` to run *only* the
    precondition checks without creating anything; the result will show each
    step as ``{"status": "dry_run"}``.

    On partial failure the completed steps are reported as ``"ok"``, the
    failed step as ``"error"``, and remaining steps as ``"skipped"``.
    No automatic rollback is performed — clean up manually with
    ``hmc_delete_lpar`` / ``hmc_delete_adapter`` as appropriate.

    Parameters
    ----------
    system_name_or_uuid:
        Target managed system — either a SystemName or UUID.
    name:
        Name for the new LPAR. Must be unique across the HMC.
    port_vlan_id:
        PVID for the Virtual Ethernet network adapter (must match an
        existing VirtualNetwork on the system).
    vios_uuid:
        UUID of the VIOS that will host the vSCSI server adapter and the
        storage mapping.
    vios_partition_id:
        Numeric partition ID of the VIOS (for the vSCSI pairing).
    vios_slot:
        Virtual slot number of the VIOS server adapter.
    storage_name:
        Name of the VirtualDisk (logical volume) or PhysicalVolume to map.
    partition_type:
        Partition type: ``"AIX/Linux"`` (default), ``"OS400"``, or
        ``"Virtual IO Server"``.
    min_memory / desired_memory / max_memory:
        Memory bounds in MiB. Defaults: 256 / 4096 / 8192.
    desired_vcpus / max_vcpus:
        Shared-processor virtual CPU counts. Defaults: 1 / 2.
    storage_kind:
        ``"VirtualDisk"`` (default) or ``"PhysicalVolume"``.
    vg_uuid:
        UUID of the VolumeGroup to validate. When supplied, the tool
        checks the VG exists on *vios_uuid* before proceeding.
    power_on:
        Submit a PowerOn job after provisioning (default ``True``).
    dry_run:
        When ``True``, run precondition checks only — no LPAR is created.

    Returns
    -------
    dict with:
    - ``created`` (bool): ``True`` when all required steps completed.
    - ``dry_run`` (bool): mirrors the input flag.
    - ``steps`` (list): per-step result dicts ``{step, status, result?}``.
      status is ``"ok"``, ``"error"``, ``"skipped"``, or ``"dry_run"``.
    - ``warnings`` (list): non-fatal notices (currently always empty).
    """

    async def _go() -> dict[str, Any]:
        async with client_from_env() as hmc:
            # ----------------------------------------------------------------
            # 1. Resolve system UUID
            # ----------------------------------------------------------------
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)

            # ----------------------------------------------------------------
            # 2. Preconditions (always, including dry-run)
            # ----------------------------------------------------------------
            await _check_name_unique(hmc, name)
            await _check_vlan_exists(hmc, system_uuid, port_vlan_id)
            if vg_uuid is not None:
                await _check_vg_exists(hmc, vios_uuid, vg_uuid)

            # ----------------------------------------------------------------
            # 3. Dry-run exit
            # ----------------------------------------------------------------
            step_names = ["create", "network", "vscsi", "storage"]
            if power_on:
                step_names.append("power_on")

            if dry_run:
                return {
                    "created": False,
                    "dry_run": True,
                    "steps": [_step(n, "dry_run") for n in step_names],
                    "warnings": [],
                }

            # ----------------------------------------------------------------
            # 4. Build LPAR XML
            # ----------------------------------------------------------------
            lpar_xml = build_lpar_document(
                name=name,
                partition_type=partition_type,
                resources=LparResources(
                    min_memory=min_memory,
                    desired_memory=desired_memory,
                    max_memory=max_memory,
                    desired_vcpus=desired_vcpus,
                    max_vcpus=max_vcpus,
                ),
            )

            steps: list[dict[str, Any]] = []
            lpar_uuid: str | None = None
            failed = False

            # ----------------------------------------------------------------
            # Step: create — REST first, CLI fallback on HTTP 406
            # ----------------------------------------------------------------
            try:
                created_lpar = None
                try:
                    created_lpar = await hmc.create_logical_partition(system_uuid, lpar_xml)
                except HMCError as rest_exc:
                    if rest_exc.status_code != 406:
                        raise
                    # 406 → REST LPAR create not supported on this firmware;
                    # fall back to mksyscfg CLI (same approach as hmc_create_lpar).
                    from .ssh import _ssh_system_name
                    cfg = HMCConfig()
                    try:
                        sys_name = await _ssh_system_name(cfg, system_uuid)
                    except HMCCLIError:
                        sys_name = system_name_or_uuid
                    await create_lpar_via_cli(
                        cfg,
                        system_name=sys_name,
                        name=name,
                        partition_type=partition_type,
                        min_memory=min_memory,
                        desired_memory=desired_memory,
                        max_memory=max_memory,
                        desired_vcpus=desired_vcpus,
                        max_vcpus=max_vcpus,
                        # Mirror the full parameter set from hmc_create_lpar's
                        # CLI fallback so all resource axes are forwarded.
                        desired_procs=None,
                        min_procs=None,
                        max_procs=None,
                        min_vcpus=None,
                        max_virtual_slots=None,
                    )
                    # Fetch the newly created entry
                    created_lpar = await hmc.find_partition_by_name(name)

                lpar_uuid = (created_lpar or {}).get("UUID")
                steps.append(_step("create", "ok", created_lpar))
            except Exception as exc:
                steps.append(_step("create", "error", str(exc)))
                failed = True

            # ----------------------------------------------------------------
            # Step: network adapter
            # ----------------------------------------------------------------
            if not failed and lpar_uuid:
                try:
                    net_result = await hmc.add_network_adapter(
                        lpar_uuid, port_vlan_id
                    )
                    steps.append(_step("network", "ok", net_result))
                except Exception as exc:
                    steps.append(_step("network", "error", str(exc)))
                    failed = True
            elif not failed:
                steps.append(_step("network", "skipped"))
                failed = True

            # ----------------------------------------------------------------
            # Step: vSCSI adapter
            # ----------------------------------------------------------------
            if not failed and lpar_uuid:
                try:
                    vscsi_result = await hmc.add_vscsi_adapter(
                        lpar_uuid, vios_partition_id, vios_slot
                    )
                    steps.append(_step("vscsi", "ok", vscsi_result))
                except Exception as exc:
                    steps.append(_step("vscsi", "error", str(exc)))
                    failed = True
            elif not failed:
                steps.append(_step("vscsi", "skipped"))
                failed = True
            else:
                steps.append(_step("vscsi", "skipped"))

            # ----------------------------------------------------------------
            # Step: storage mapping
            # ----------------------------------------------------------------
            if not failed and lpar_uuid:
                try:
                    storage_result = await hmc.map_storage_to_lpar(
                        vios_uuid, storage_kind, storage_name, lpar_uuid
                    )
                    steps.append(_step("storage", "ok", storage_result))
                except Exception as exc:
                    steps.append(_step("storage", "error", str(exc)))
                    failed = True
            else:
                steps.append(_step("storage", "skipped"))

            # ----------------------------------------------------------------
            # Step: power on
            # ----------------------------------------------------------------
            if power_on:
                if not failed and lpar_uuid:
                    try:
                        job = await hmc.submit_job(
                            f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
                            power_on_lpar_job(),
                        )
                        steps.append(_step("power_on", "ok", job))
                    except Exception as exc:
                        steps.append(_step("power_on", "error", str(exc)))
                        failed = True
                else:
                    steps.append(_step("power_on", "skipped"))

            return {
                "created": not failed,
                "dry_run": False,
                "steps": steps,
                "warnings": [],
            }

    return _run(_go)
