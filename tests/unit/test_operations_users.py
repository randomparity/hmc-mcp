"""Tests for presentation-neutral user profile operations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations.users import (
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
    assert '<UserID kb="CUR" kxe="false">alice</UserID>' in document
    assert '<UserDescription kb="CUR" kxe="false">operator</UserDescription>' in document
    assert result == {"Resource": {"UserID": "alice"}}


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
    assert '<UserDescription kb="CUR" kxe="false"></UserDescription>' in document
    assert '<AssociatedResourceRoles kb="CUR" kxe="false"/>' in document
    assert '<AllowSSHRemoteAccess kb="CUR" kxe="false">false</AllowSSHRemoteAccess>' in document
    assert result is None
