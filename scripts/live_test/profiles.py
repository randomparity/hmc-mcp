"""LPAR profile inventory scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST4 — LPAR Properties & Profile Inventory
# ---------------------------------------------------------------------------


async def inventory_lpar_profiles(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST4: LPAR Properties & Profile Inventory ===")

    st, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_description", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_msp", st, data)

    st, data = await state.call(
        client, "hmc_get_proc_compat_modes", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_get_proc_compat_modes", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_proc_compat", st, data)

    st, data = await state.call(
        client,
        "hmc_list_vnics",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_list_vnics", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_memopt_score",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_memopt_score", st, data)

    st, data = await state.call(
        client, "hmc_list_lpar_memopt_scores", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_list_lpar_memopt_scores", st, data)

    st, data = await state.call(
        client, "hmc_get_system_memopt_score", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_get_system_memopt_score", st, data)

    st, data = await state.call(
        client, "hmc_plan_lpar_memopt_scores", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_plan_lpar_memopt_scores", st, data)

    st, data = await state.call(
        client, "hmc_plan_system_memopt_score", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_plan_system_memopt_score", st, data)

    st, data = await state.call(
        client,
        "hmc_list_resource_group_memopt_scores",
        system_name_or_uuid=context.system_name,
    )
    state.record(4, "hmc_list_resource_group_memopt_scores", st, data)

    st, data = await state.call(
        client,
        "hmc_plan_resource_group_memopt_scores",
        system_name_or_uuid=context.system_name,
    )
    state.record(4, "hmc_plan_resource_group_memopt_scores", st, data)

    st, data = await state.call(
        client,
        "hmc_get_minimum_affinity_policy",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_minimum_affinity_policy", st, data)
