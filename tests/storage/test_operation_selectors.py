"""Managed-system scoping for VIOS storage operations."""

from __future__ import annotations

from typing import cast

import pytest

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.storage import list_volume_groups

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
VIOS_UUID = "00000000-0000-0000-0000-000000000002"


class _StorageClient:
    def __init__(self) -> None:
        self.system_queries: list[str] = []
        self.vios_queries: list[tuple[str, str | None]] = []
        self.volume_group_queries: list[str] = []

    async def find_system_by_name(self, name: str) -> dict[str, str]:
        self.system_queries.append(name)
        return {"UUID": SYSTEM_UUID}

    async def find_vios_by_name(
        self, name: str, *, system_uuid: str | None = None
    ) -> dict[str, str]:
        self.vios_queries.append((name, system_uuid))
        return {"UUID": VIOS_UUID}

    async def list_volume_groups(self, vios_uuid: str) -> list[dict[str, object]]:
        self.volume_group_queries.append(vios_uuid)
        return []


@pytest.mark.asyncio
async def test_vios_name_is_resolved_with_managed_system_scope() -> None:
    client = _StorageClient()

    await list_volume_groups(
        cast(HMCClient, client), "shared-vios", system_name_or_uuid="system-a"
    )

    assert client.system_queries == ["system-a"]
    assert client.vios_queries == [("shared-vios", SYSTEM_UUID)]
    assert client.volume_group_queries == [VIOS_UUID]


@pytest.mark.asyncio
async def test_vios_uuid_needs_no_managed_system_scope() -> None:
    client = _StorageClient()

    await list_volume_groups(cast(HMCClient, client), VIOS_UUID)

    assert client.system_queries == []
    assert client.vios_queries == []
    assert client.volume_group_queries == [VIOS_UUID]
