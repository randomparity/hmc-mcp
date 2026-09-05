"""Direct contracts for the adapter client mixin delegation boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.client.client_adapters import AdaptersMixin
from hmc_mcp.client.client_contracts import ADAPTER_TYPES, validate_adapter_type


def test_adapter_type_validation_accepts_only_the_published_vocabulary():
    for adapter_type in ADAPTER_TYPES:
        assert validate_adapter_type(adapter_type) == adapter_type

    with pytest.raises(ValueError, match="Invalid adapter_type"):
        validate_adapter_type("not-an-adapter")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_and_delete_adapter_delegate_to_child_resources():
    client = SimpleNamespace(
        list_child=AsyncMock(return_value=[{"UUID": "adapter-1"}]),
        delete_child=AsyncMock(),
    )

    adapters = await AdaptersMixin.list_adapters(
        client, "lpar-1", "ClientNetworkAdapter"
    )
    await AdaptersMixin.delete_adapter(
        client, "lpar-1", "ClientNetworkAdapter", "adapter-1"
    )

    assert adapters == [{"UUID": "adapter-1"}]
    client.list_child.assert_awaited_once_with(
        "LogicalPartition", "lpar-1", "ClientNetworkAdapter"
    )
    client.delete_child.assert_awaited_once_with(
        "LogicalPartition", "lpar-1", "ClientNetworkAdapter", "adapter-1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "arguments", "adapter_type", "xml_fragment"),
    [
        (
            AdaptersMixin.add_vscsi_adapter,
            ("lpar-1", 2, 12, 4),
            "VirtualSCSIClientAdapter",
            "VirtualSCSIClientAdapter",
        ),
        (
            AdaptersMixin.add_vfc_adapter,
            ("lpar-1", 2, 12, 4),
            "VirtualFibreChannelClientAdapter",
            "VirtualFibreChannelClientAdapter",
        ),
        (
            AdaptersMixin.add_network_adapter,
            ("lpar-1", 200, 4, 0, True, "02:00:00:00:00:01"),
            "ClientNetworkAdapter",
            "ClientNetworkAdapter",
        ),
    ],
)
async def test_add_adapter_builds_document_and_creates_child(
    method, arguments, adapter_type, xml_fragment
):
    client = SimpleNamespace(create_child=AsyncMock(return_value={"UUID": "adapter-1"}))

    result = await method(client, *arguments)

    assert result == {"UUID": "adapter-1"}
    resource, lpar_uuid, actual_adapter_type, xml = client.create_child.await_args.args
    assert (resource, lpar_uuid, actual_adapter_type) == (
        "LogicalPartition",
        "lpar-1",
        adapter_type,
    )
    assert xml_fragment in xml


@pytest.mark.asyncio
async def test_list_and_delete_adapter_reject_unknown_adapter_types():
    client = SimpleNamespace(list_child=AsyncMock(), delete_child=AsyncMock())

    with pytest.raises(ValueError, match="Invalid adapter_type"):
        await AdaptersMixin.list_adapters(client, "lpar-1", "UnknownAdapter")
    with pytest.raises(ValueError, match="Invalid adapter_type"):
        await AdaptersMixin.delete_adapter(client, "lpar-1", "UnknownAdapter", "adapter-1")

    client.list_child.assert_not_awaited()
    client.delete_child.assert_not_awaited()
