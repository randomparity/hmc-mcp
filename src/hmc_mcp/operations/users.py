"""Presentation-neutral HMC user and remote-access operations."""

from __future__ import annotations

from typing import Any

from ..client import HMCClient
from ..documents import AuthenticationType, build_hmc_user_document, build_remote_access_document


async def _create_user(
    hmc: HMCClient,
    console_uuid: str,
    user_id: str,
    password: str,
    authentication_type: AuthenticationType,
    **fields: Any,
) -> dict[str, Any] | None:
    document = build_hmc_user_document(
        user_id=user_id,
        password=password,
        authentication_type=authentication_type,
        **fields,
    )
    return await hmc.create_hmc_user(console_uuid, document)


async def _modify_user(
    hmc: HMCClient,
    console_uuid: str,
    user_profile_uuid: str,
    **fields: Any,
) -> dict[str, Any] | None:
    document = build_hmc_user_document(**fields)
    return await hmc.modify_hmc_user(console_uuid, user_profile_uuid, document)


async def _delete_user(
    hmc: HMCClient, console_uuid: str, user_profile_uuid: str
) -> None:
    await hmc.delete_hmc_user(console_uuid, user_profile_uuid)


async def _configure_remote_access(
    hmc: HMCClient,
    console_uuid: str,
    values: dict[str, str | int | bool] | None,
    clear_fields: list[str] | None,
) -> dict[str, Any] | None:
    build_remote_access_document(values, clear_fields)
    return await hmc.configure_remote_access(console_uuid, values, clear_fields)
