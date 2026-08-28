"""Presentation-neutral HMC user and remote-access operations."""

from __future__ import annotations

from typing import Any

from ..client import HMCClient
from ..documents import (
    AuthenticationType,
    build_hmc_user_document,
)


async def create_user(
    hmc: HMCClient,
    console_uuid: str,
    user_id: str,
    password: str,
    authentication_type: AuthenticationType,
    *,
    description: str | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
) -> dict[str, Any] | None:
    document = build_hmc_user_document(
        user_id=user_id,
        password=password,
        authentication_type=authentication_type,
        description=description,
        associated_task_role=associated_task_role,
        associated_resource_roles=associated_resource_roles,
        password_expiry=password_expiry,
        session_timeout=session_timeout,
        verify_session_timeout=verify_session_timeout,
        idle_session_timeout=idle_session_timeout,
        user_inactivity=user_inactivity,
        minimum_password_age=minimum_password_age,
        allow_web_remote_access=allow_web_remote_access,
        allow_ssh_remote_access=allow_ssh_remote_access,
        remote_user_id=remote_user_id,
    )
    return await hmc.create_hmc_user(console_uuid, document)


async def modify_user(
    hmc: HMCClient,
    console_uuid: str,
    user_profile_uuid: str,
    *,
    password: str | None = None,
    description: str | None = None,
    authentication_type: AuthenticationType | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
) -> dict[str, Any] | None:
    document = build_hmc_user_document(
        password=password,
        description=description,
        authentication_type=authentication_type,
        associated_task_role=associated_task_role,
        associated_resource_roles=associated_resource_roles,
        password_expiry=password_expiry,
        session_timeout=session_timeout,
        verify_session_timeout=verify_session_timeout,
        idle_session_timeout=idle_session_timeout,
        user_inactivity=user_inactivity,
        minimum_password_age=minimum_password_age,
        allow_web_remote_access=allow_web_remote_access,
        allow_ssh_remote_access=allow_ssh_remote_access,
        remote_user_id=remote_user_id,
    )
    return await hmc.modify_hmc_user(console_uuid, user_profile_uuid, document)


async def delete_user(
    hmc: HMCClient, console_uuid: str, user_profile_uuid: str
) -> None:
    await hmc.delete_hmc_user(console_uuid, user_profile_uuid)


async def configure_remote_access(
    hmc: HMCClient,
    console_uuid: str,
    values: dict[str, str | int | bool] | None,
    clear_fields: list[str] | None,
) -> dict[str, Any] | None:
    return await hmc.configure_remote_access(console_uuid, values, clear_fields)
