"""Virtual-network mutation scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp import Client

from .observation import ExpectedOutcome
from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

_REST_CREATE_UNSUPPORTED = ExpectedOutcome(
    operation="network.create_network",
    variant="rest-virtual-network-creation",
    reason="HMC firmware returns HTTP 406 for REST VirtualNetwork create "
    "(same PUT limitation as LPAR create)",
    error_codes=frozenset({"406", "not acceptable"}),
)

# ---------------------------------------------------------------------------
# ST9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------


async def mutate_virtual_networking(client: Client, state: RunState) -> None:
    artifacts = state.artifacts
    print("\n=== ST9: Virtual Networking Mutations ===")

    if artifacts.test_vlan_id is None:
        _skip_network_mutation(state, "no unused VLAN ID found in ST2")
        return

    vswitch_id = (
        artifacts.test_vswitch_id if artifacts.test_vswitch_id is not None else 0
    )
    await _create_network_and_nettest_lpar(client, state, vswitch_id)
    await _attach_network_adapter(client, state, vswitch_id)
    await _cleanup_network_mutation(client, state)


def _skip_network_mutation(state: RunState, reason: str) -> None:
    for name in (
        "hmc_create_virtual_network",
        "hmc_create_lpar (nettest)",
        "hmc_add_network_adapter",
        "hmc_list_adapters (post-add)",
        "hmc_delete_adapter",
        "hmc_delete_virtual_network",
        "hmc_delete_lpar (nettest)",
    ):
        state.skip(9, name, reason)


async def _create_network_and_nettest_lpar(
    client: Client, state: RunState, vswitch_id: int
) -> None:
    config = state.config
    artifacts = state.artifacts

    st, data = await state.call(
        client,
        "hmc_create_virtual_network",
        expected=[_REST_CREATE_UNSUPPORTED],
        system_name_or_uuid=config.system_name,
        name=f"mcp-test-vlan{artifacts.test_vlan_id}",
        vlan_id=artifacts.test_vlan_id,
        virtual_switch_id=vswitch_id,
        tagged=False,
    )
    state.record_with_expected(
        9, "hmc_create_virtual_network", st, data, [_REST_CREATE_UNSUPPORTED]
    )
    if st == "PASS" and isinstance(data, dict):
        artifacts.test_network_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=config.system_name
    )
    state.record(9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not artifacts.test_network_uuid:
        for e in entries(data):
            resource = get_resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if str(vlan) == str(artifacts.test_vlan_id):
                artifacts.test_network_uuid = e.get("UUID") or e.get("uuid")
                break

    # Use all_resources=1 path (no explicit resource args) — avoids HSCL0622
    # proc-unit validation failure on this HMC firmware.
    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=config.system_name,
        name=config.nettest_name,
    )
    state.record(9, "hmc_create_lpar (nettest)", st, data)
    if st == "PASS" and isinstance(data, dict):
        artifacts.nettest_uuid = data.get("uuid") or data.get("UUID")


async def _attach_network_adapter(
    client: Client, state: RunState, vswitch_id: int
) -> None:
    config = state.config
    artifacts = state.artifacts
    if artifacts.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_add_network_adapter",
            lpar_name_or_uuid=config.nettest_name,
            port_vlan_id=artifacts.test_vlan_id,
            virtual_switch_id=vswitch_id,
        )
        state.record(9, "hmc_add_network_adapter", st, data)

        st, data = await state.call(
            client,
            "hmc_list_adapters",
            lpar_name_or_uuid=config.nettest_name,
            adapter_type="ClientNetworkAdapter",
        )
        state.record(9, "hmc_list_adapters (post-add)", st, data)
        if st == "PASS":
            for e in entries(data):
                artifacts.test_adapter_uuid = e.get("UUID") or e.get("uuid")
                break
    else:
        state.skip(
            9,
            "hmc_add_network_adapter",
            "virtual network not created (REST 406)",
        )
        state.skip(
            9,
            "hmc_list_adapters (post-add)",
            "virtual network not created (REST 406)",
        )


async def _cleanup_network_mutation(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    if artifacts.test_adapter_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_adapter",
            lpar_name_or_uuid=config.nettest_name,
            adapter_type="ClientNetworkAdapter",
            adapter_uuid=artifacts.test_adapter_uuid,
        )
        state.record(9, "hmc_delete_adapter", st, data)
    else:
        state.skip(9, "hmc_delete_adapter", "no adapter UUID captured")

    if artifacts.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_virtual_network",
            system_name_or_uuid=config.system_name,
            network_uuid=artifacts.test_network_uuid,
        )
        state.record(9, "hmc_delete_virtual_network", st, data)
        if st == "PASS":
            artifacts.test_network_uuid = None
    else:
        state.skip(9, "hmc_delete_virtual_network", "no network UUID captured")

    if artifacts.nettest_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_lpar",
            system_name_or_uuid=config.system_name,
            lpar_name_or_uuid=config.nettest_name,
        )
        state.record(9, "hmc_delete_lpar (nettest)", st, data)
        if st == "PASS":
            artifacts.nettest_uuid = None
    else:
        state.skip(9, "hmc_delete_lpar (nettest)", "nettest LPAR not created")


# ---------------------------------------------------------------------------
# ST2 — Network Inventory
# ---------------------------------------------------------------------------


async def _discover_virtual_switch(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    st, data = await state.call(
        client, "hmc_list_virtual_switches", system_name_or_uuid=config.system_name
    )
    state.record(2, "hmc_list_virtual_switches", st, data)
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            sid = resource.get("SwitchID") or resource.get("switch_id")
            if sid is not None:
                artifacts.test_vswitch_id = int(sid)
                break
        if artifacts.test_vswitch_id is None:
            artifacts.test_vswitch_id = 0


def _unused_vlan(data: Any, start: int, end: int) -> tuple[int | None, list[object]]:
    used_vlans: set[int] = set()
    malformed_vlans: list[object] = []
    for entry in entries(data):
        resource = get_resource(entry)
        vlan = (
            resource.get("NetworkVLANID")
            or resource.get("VLANId")
            or resource.get("vlan_id")
        )
        if vlan is not None:
            try:
                used_vlans.add(int(vlan))
            except (TypeError, ValueError):
                malformed_vlans.append(vlan)
    available = None
    if not malformed_vlans:
        available = next(
            (
                candidate
                for candidate in range(start, end + 1)
                if candidate not in used_vlans
            ),
            None,
        )
    return available, malformed_vlans


async def _select_unused_vlan(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts

    st, data = await state.call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=config.system_name
    )
    if st == "PASS":
        artifacts.test_vlan_id, malformed_vlans = _unused_vlan(
            data, config.vlan_range_start, config.vlan_range_end
        )
        if malformed_vlans:
            st = "FAIL"
            data = (
                "Virtual-network inventory contains unparsable VLAN identifiers: "
                + ", ".join(repr(value) for value in malformed_vlans)
            )
    state.record(2, "hmc_list_virtual_networks", st, data)


async def _record_network_adapters(client: Client, state: RunState) -> None:
    config = state.config
    st, data = await state.call(
        client, "hmc_list_network_bridges", system_name_or_uuid=config.system_name
    )
    state.record(2, "hmc_list_network_bridges", st, data)

    st, data = await state.call(
        client, "hmc_list_fc_ports", system_name_or_uuid=config.system_name
    )
    state.record(2, "hmc_list_fc_ports", st, data)

    st, data = await state.call(
        client, "hmc_list_sea_adapters", system_name_or_uuid=config.system_name
    )
    state.record(2, "hmc_list_sea_adapters", st, data)

    st, data = await state.call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=config.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    state.record(2, "hmc_list_adapters (CNA lp3)", st, data)


async def inventory_network(client: Client, state: RunState) -> None:
    artifacts = state.artifacts
    print("\n=== ST2: Network Inventory ===")
    await _discover_virtual_switch(client, state)
    await _select_unused_vlan(client, state)
    print(
        f"  Test VLAN ID: {artifacts.test_vlan_id}  VSwitch ID: {artifacts.test_vswitch_id}"
    )
    await _record_network_adapters(client, state)
