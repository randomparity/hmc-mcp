"""User-administration scenarios for the live HMC test harness."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastmcp import Client


if TYPE_CHECKING:
    from live_test_runner import RunState

_TEST_USER_PASSWORD = f"Aa1!{secrets.token_hex(8)}"


# ---------------------------------------------------------------------------
# ST11 — User Administration
# ---------------------------------------------------------------------------

_REST000E_SKIP = ["REST000E", "400", "not available on this HMC"]


async def administer_test_user(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST11: User Administration ===")

    st, data = await state.call(
        client,
        "hmc_create_user",
        name=context.test_user,
        taskrole="viewer",
        password=_TEST_USER_PASSWORD,
        description="MCP live test user R2",
    )
    state.record_expected_or_real(
        11,
        "hmc_create_user",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported on this HMC (expected)",
    )
    user_created = st == "PASS"

    st, data = await state.call(client, "hmc_list_users")
    state.record_expected_or_real(
        11,
        "hmc_list_users (confirm created)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )

    if user_created:
        st, data = await state.call(
            client,
            "hmc_modify_user",
            name=context.test_user,
            description="MCP live test user R2 — updated",
        )
        state.record(11, "hmc_modify_user", st, data)
    else:
        state.skip(11, "hmc_modify_user", "user not created (REST000E expected)")

    if user_created:
        st, data = await state.call(client, "hmc_delete_user", name=context.test_user)
        state.record(11, "hmc_delete_user", st, data)
    else:
        state.skip(11, "hmc_delete_user", "user not created (REST000E expected)")

    st, data = await state.call(client, "hmc_list_users")
    state.record_expected_or_real(
        11,
        "hmc_list_users (confirm deleted)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )
