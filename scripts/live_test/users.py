"""User-administration scenarios for the live HMC test harness."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastmcp import Client

from .observation import ExpectedOutcome
from .results import entries, resource

if TYPE_CHECKING:
    from live_test_runner import RunState

_TEST_USER_PASSWORD = f"Aa1!{secrets.token_hex(8)}"


# ---------------------------------------------------------------------------
# ST11 — User Administration
# ---------------------------------------------------------------------------

_HMCUSER_UNSUPPORTED = ExpectedOutcome(
    reason="HmcUser REST not supported on this HMC",
    error_codes=frozenset({"REST000E"}),
)
_HMCUSER_ENDPOINT_UNSUPPORTED = ExpectedOutcome(
    reason="HmcUser REST endpoint not supported on this HMC (expected)",
    error_codes=frozenset({"REST000E"}),
)


def _skip_reason(user_created: bool) -> str:
    """Say which of the two preconditions the user-administration path is missing."""
    if not user_created:
        return "user not created (REST000E expected)"
    return "user profile UUID not found after create"


def _profile_uuid(data: object, user_id: str) -> str | None:
    """Find the created user's profile UUID in a ``hmc_list_users`` result."""
    for entry in entries(data):
        fields = resource(entry)
        if fields.get("UserID") == user_id:
            uuid = fields.get("uuid") or fields.get("UUID") or entry.get("uuid")
            return uuid if isinstance(uuid, str) else None
    return None


async def administer_test_user(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST11: User Administration ===")

    if not artifacts.console_uuid:
        for name in (
            "hmc_create_user",
            "hmc_list_users (confirm created)",
            "hmc_modify_user",
            "hmc_delete_user",
            "hmc_list_users (confirm deleted)",
        ):
            state.skip(11, name, "no console UUID captured (ST1 may have failed)")
        return

    st, data = await state.call(
        client,
        "hmc_create_user",
        console_uuid=artifacts.console_uuid,
        user_id=config.test_user,
        password=_TEST_USER_PASSWORD,
        associated_task_role="viewer",
        description="MCP live test user R2",
    )
    state.record_with_expected(
        11, "hmc_create_user", st, data, [_HMCUSER_UNSUPPORTED]
    )
    user_created = st == "PASS"

    st, data = await state.call(
        client, "hmc_list_users", console_uuid=artifacts.console_uuid
    )
    state.record_with_expected(
        11, "hmc_list_users (confirm created)", st, data, [_HMCUSER_UNSUPPORTED]
    )
    if user_created and st == "PASS":
        artifacts.test_user_uuid = _profile_uuid(data, config.test_user)

    # The modify and delete tools address the profile by UUID, not by user id, so
    # neither can run without the identity the listing above resolves.
    if user_created and artifacts.test_user_uuid:
        st, data = await state.call(
            client,
            "hmc_modify_user",
            console_uuid=artifacts.console_uuid,
            user_profile_uuid=artifacts.test_user_uuid,
            description="MCP live test user R2 — updated",
        )
        state.record(11, "hmc_modify_user", st, data)
    else:
        state.skip(11, "hmc_modify_user", _skip_reason(user_created))

    if user_created and artifacts.test_user_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_user",
            console_uuid=artifacts.console_uuid,
            user_profile_uuid=artifacts.test_user_uuid,
        )
        state.record(11, "hmc_delete_user", st, data)
        if st == "PASS":
            artifacts.test_user_uuid = None
    else:
        state.skip(11, "hmc_delete_user", _skip_reason(user_created))

    st, data = await state.call(
        client, "hmc_list_users", console_uuid=artifacts.console_uuid
    )
    state.record_with_expected(
        11, "hmc_list_users (confirm deleted)", st, data, [_HMCUSER_UNSUPPORTED]
    )


# ---------------------------------------------------------------------------
# ST6 — User Inventory
# ---------------------------------------------------------------------------


async def inventory_users(client: Client, state: RunState) -> None:
    print("\n=== ST6: User Inventory ===")
    artifacts = state.artifacts

    if not artifacts.console_uuid:
        state.skip(
            6, "hmc_list_users", "no console UUID captured (ST1 may have failed)"
        )
        return

    st, data = await state.call(
        client, "hmc_list_users", console_uuid=artifacts.console_uuid
    )
    state.record_with_expected(
        6, "hmc_list_users", st, data, [_HMCUSER_ENDPOINT_UNSUPPORTED]
    )
