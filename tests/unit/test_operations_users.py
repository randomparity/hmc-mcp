"""Tests for presentation-neutral user profile operations."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from hmcpctl.operations.users.core import (
    CreateUserRequest,
    ModifyUserPatch,
    create_user,
    modify_user,
)


@pytest.mark.asyncio
async def test_create_user_builds_document_from_typed_request() -> None:
    hmc = AsyncMock()
    hmc.create_hmc_user.return_value = {"Resource": {"UserID": "alice"}}
    request = CreateUserRequest(
        user_id="alice",
        password="secret",  # pragma: allowlist secret -- synthetic fixture
        authentication_type="Local",
        description="operator",
    )

    result = await create_user(hmc, "console-1", request)

    document = hmc.create_hmc_user.await_args.args[1]
    assert '<UserID ksv="V1_17_0" kb="COR" kxe="false">alice</UserID>' in document
    assert (
        '<UserDescription ksv="V1_17_0" kb="CUD" kxe="false">operator</UserDescription>'
        in document
    )
    assert result == {"Resource": {"UserID": "alice"}}


@pytest.mark.asyncio
async def test_create_user_sends_verify_session_timeout_as_minutes() -> None:
    hmc = AsyncMock()
    request = CreateUserRequest(
        user_id="alice",
        password="secret",  # pragma: allowlist secret -- synthetic fixture
        authentication_type="Local",
        verify_session_timeout=15,
    )

    await create_user(hmc, "console-1", request)

    document = hmc.create_hmc_user.await_args.args[1]
    assert (
        '<VerifySessionTimeout ksv="V1_17_0" kb="CUD" kxe="false">15'
        "</VerifySessionTimeout>" in document
    )


@pytest.mark.parametrize("value", [True, False, -1])
@pytest.mark.parametrize(
    "build",
    [
        lambda value: CreateUserRequest(
            user_id="alice",
            password="secret",  # pragma: allowlist secret -- synthetic fixture
            authentication_type="Local",
            verify_session_timeout=value,
        ),
        lambda value: ModifyUserPatch(verify_session_timeout=value),
    ],
    ids=["create", "modify"],
)
def test_verify_session_timeout_must_be_non_negative_minutes(
    build: Callable[[object], object], value: object
) -> None:
    with pytest.raises(ValueError, match="verify_session_timeout .*minutes"):
        build(value)


@pytest.mark.asyncio
async def test_modify_user_preserves_explicit_clear_values() -> None:
    hmc = AsyncMock()
    hmc.modify_hmc_user.return_value = None
    patch = ModifyUserPatch(
        description="",
        associated_resource_roles=[],
        allow_ssh_remote_access=False,
    )

    result = await modify_user(hmc, "console-1", "profile-1", patch)

    document = hmc.modify_hmc_user.await_args.args[2]
    assert (
        '<UserDescription ksv="V1_17_0" kb="CUD" kxe="false"></UserDescription>'
        in document
    )
    assert '<AssociatedResourceRoles ksv="V1_17_0" kb="CUD" kxe="false"/>' in document
    assert (
        '<AllowSSHRemoteAccess ksv="V1_17_0" kb="CUD" kxe="false">false</AllowSSHRemoteAccess>'
        in document
    )
    assert result is None
