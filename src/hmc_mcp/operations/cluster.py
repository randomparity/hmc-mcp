"""Presentation-neutral Cluster, SSP, and logical-unit operations."""

from __future__ import annotations

from typing import Any

from hmc_mcp.client.core import HMCClient

from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    DeviceType,
    LuType,
    validate_logical_unit_types,
    validate_wait_timing,
    wait_for_submitted_job,
)


async def list_clusters(hmc: HMCClient) -> list[dict[str, Any]]:
    """List clusters through the HMC client."""
    return await hmc.list_clusters()


async def list_shared_storage_pools(hmc: HMCClient) -> list[dict[str, Any]]:
    """List shared storage pools through the HMC client."""
    return await hmc.list_shared_storage_pools()


async def get_shared_storage_pool(
    hmc: HMCClient, ssp_uuid: str
) -> dict[str, Any] | None:
    """Get one shared storage pool by UUID."""
    return await hmc.get_shared_storage_pool(ssp_uuid)


async def create_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType,
    device_type: DeviceType,
    *,
    cloned_from: str | None = None,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Submit logical-unit creation and optionally wait for completion."""
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.create_logical_unit(
        cluster_uuid, lu_name, lu_size_gib, lu_type, device_type, cloned_from
    )
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def delete_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_udid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Submit logical-unit deletion and optionally wait for completion."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.delete_logical_unit(cluster_uuid, lu_udid)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


def validate_logical_unit_create(
    lu_type: LuType, device_type: DeviceType, wait: bool, timeout_seconds: int, poll_interval: int
) -> None:
    """Validate logical-unit creation controls before connecting."""
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)


def validate_logical_unit_wait(wait: bool, timeout_seconds: int, poll_interval: int) -> None:
    """Validate logical-unit polling controls before connecting."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
