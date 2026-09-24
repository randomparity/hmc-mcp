from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import hmcpctl.config as config_module
from hmcpctl.config import HMCConfig, build_config
from hmcpctl.resource_identity import (
    ResourceNotFoundError,
    resolve_lpar_uuid,
    resolve_system_name,
    resolve_vios_uuid,
)


@pytest.mark.parametrize(
    ("base", "port_is_explicit"),
    [
        (HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"}), False),
        (
            HMCConfig.from_mapping(
                {"host": "h", "user": "u", "password": "p", "port": 12443}
            ),
            True,
        ),
    ],
)
def test_build_config_preserves_port_provenance_across_unrelated_override(
    monkeypatch, base, port_is_explicit
):
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.setattr(
        config_module, "resolve_config_path", lambda: Path("config.toml")
    )
    monkeypatch.setattr(config_module, "load_profile", lambda profile=None: base)

    config = build_config(profile="prod", verify_ssl=True)

    assert ("port" in config.model_fields_set) is port_is_explicit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver", "finder_name", "resource_name", "resource_uuid"),
    [
        (resolve_lpar_uuid, "find_partition_by_name", "aix1", "lpar-uuid"),
        (resolve_vios_uuid, "find_vios_by_name", "vios1", "vios-uuid"),
    ],
)
async def test_partition_resolver_forwards_resolved_system_scope(
    resolver, finder_name, resource_name, resource_uuid
):
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    getattr(hmc, finder_name).return_value = {"UUID": resource_uuid}

    assert await resolver(
        hmc, resource_name, system_name_or_uuid="system-name"
    ) == resource_uuid

    hmc.find_system_by_name.assert_awaited_once_with("system-name")
    getattr(hmc, finder_name).assert_awaited_once_with(
        resource_name, system_uuid="system-uuid"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", [resolve_lpar_uuid, resolve_vios_uuid])
async def test_partition_resolver_uuid_pass_through_ignores_system_scope(resolver):
    hmc = AsyncMock()
    resource_uuid = "11111111-1111-1111-1111-111111111111"

    assert await resolver(
        hmc, resource_uuid, system_name_or_uuid="system-name"
    ) == resource_uuid

    hmc.find_system_by_name.assert_not_awaited()
    hmc.find_partition_by_name.assert_not_awaited()
    hmc.find_vios_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_lpar_resolver_preserves_no_match_guidance():
    hmc = AsyncMock()
    hmc.find_partition_by_name.return_value = None

    with pytest.raises(
        ResourceNotFoundError,
        match="No LPAR named 'missing' found. Use hmc_list_lpars to list available partitions.",
    ) as raised:
        await resolve_lpar_uuid(hmc, "missing")

    assert raised.value.resource_kind == "LPAR"
    assert raised.value.selector == "missing"
    assert isinstance(raised.value, ValueError)


SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        None,
        {"Resource": None},
        {"Resource": []},
        {"Resource": ["x"]},
        {"Resource": {}},
        {"Resource": {"SystemName": ""}},
        {"Resource": {"SystemName": "   "}},
        {"Resource": {"SystemName": 1}},
    ],
)
async def test_system_name_resolver_reports_unusable_payload_as_not_found(entry):
    hmc = AsyncMock()
    hmc.get_managed_system.return_value = entry

    with pytest.raises(
        ResourceNotFoundError,
        match=f"Managed system '{SYSTEM_UUID}' has no SystemName. "
        "Use hmc_list_systems to inspect available systems.",
    ) as raised:
        await resolve_system_name(hmc, SYSTEM_UUID)

    assert raised.value.resource_kind == "managed system"
    assert raised.value.selector == SYSTEM_UUID
    hmc.get_managed_system.assert_awaited_once_with(SYSTEM_UUID)


@pytest.mark.asyncio
async def test_system_name_resolver_resolves_uuid_and_passes_name_through():
    hmc = AsyncMock()
    hmc.get_managed_system.return_value = {"Resource": {"SystemName": "sys-a"}}

    assert await resolve_system_name(hmc, "sys-b") == "sys-b"
    hmc.get_managed_system.assert_not_awaited()
    assert await resolve_system_name(hmc, SYSTEM_UUID) == "sys-a"
    hmc.get_managed_system.assert_awaited_once_with(SYSTEM_UUID)
