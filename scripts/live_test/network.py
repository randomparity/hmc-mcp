"""Virtual-network mutation scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client


from .results import entries, resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------


async def mutate_virtual_networking(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST9: Virtual Networking Mutations ===")

    if context.test_vlan_id is None:
        for name in [
            "hmc_create_virtual_network",
            "hmc_create_lpar (nettest)",
            "hmc_add_network_adapter",
            "hmc_list_adapters (post-add)",
            "hmc_delete_adapter",
            "hmc_delete_virtual_network",
            "hmc_delete_lpar (nettest)",
        ]:
            state.skip(9, name, "no unused VLAN ID found in ST2")
        return

    vswitch_id = context.test_vswitch_id if context.test_vswitch_id is not None else 0

    st, data = await state.call(
        client,
        "hmc_create_virtual_network",
        system_name_or_uuid=context.system_name,
        name=f"mcp-test-vlan{context.test_vlan_id}",
        vlan_id=context.test_vlan_id,
        virtual_switch_id=vswitch_id,
        tagged=False,
    )
    state.record_expected_or_real(
        9,
        "hmc_create_virtual_network",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST VirtualNetwork create (same PUT limitation as LPAR create)",
    )
    if st == "PASS" and isinstance(data, dict):
        context.test_network_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=context.system_name
    )
    state.record(9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not context.test_network_uuid:
        for e in entries(data):
            resource = get_resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if str(vlan) == str(context.test_vlan_id):
                context.test_network_uuid = e.get("UUID") or e.get("uuid")
                break

    # Use all_resources=1 path (no explicit resource args) — avoids HSCL0622
    # proc-unit validation failure on this HMC firmware.
    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.nettest_name,
    )
    state.record(9, "hmc_create_lpar (nettest)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.nettest_uuid = data.get("uuid") or data.get("UUID")

    if context.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_add_network_adapter",
            lpar_name_or_uuid=context.nettest_name,
            port_vlan_id=context.test_vlan_id,
            virtual_switch_id=vswitch_id,
        )
        state.record(9, "hmc_add_network_adapter", st, data)

        st, data = await state.call(
            client,
            "hmc_list_adapters",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
        )
        state.record(9, "hmc_list_adapters (post-add)", st, data)
        if st == "PASS":
            for e in entries(data):
                context.test_adapter_uuid = e.get("UUID") or e.get("uuid")
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

    if context.test_adapter_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_adapter",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
            adapter_uuid=context.test_adapter_uuid,
        )
        state.record(9, "hmc_delete_adapter", st, data)
    else:
        state.skip(9, "hmc_delete_adapter", "no adapter UUID captured")

    if context.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_virtual_network",
            system_name_or_uuid=context.system_name,
            network_uuid=context.test_network_uuid,
        )
        state.record(9, "hmc_delete_virtual_network", st, data)
        if st == "PASS":
            context.test_network_uuid = None
    else:
        state.skip(9, "hmc_delete_virtual_network", "no network UUID captured")

    if context.nettest_uuid:
        st, data = await state.call(
            client, "hmc_delete_lpar", lpar_name_or_uuid=context.nettest_name
        )
        state.record(9, "hmc_delete_lpar (nettest)", st, data)
        if st == "PASS":
            context.nettest_uuid = None
    else:
        state.skip(9, "hmc_delete_lpar (nettest)", "nettest LPAR not created")
