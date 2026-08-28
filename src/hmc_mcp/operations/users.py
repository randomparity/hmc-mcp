"""Presentation-neutral HMC user and remote-access operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..client import HMCClient
from ..documents import (
    AuthenticationType,
    build_hmc_user_document,
)


@dataclass(frozen=True)
class CreateUserRequest:
    """Fields required to create an HMC user profile."""

    user_id: str
    password: str
    authentication_type: AuthenticationType
    description: str | None = None
    associated_task_role: str | None = None
    associated_resource_roles: list[str] | None = None
    password_expiry: int | None = None
    session_timeout: int | None = None
    verify_session_timeout: bool | None = None
    idle_session_timeout: int | None = None
    user_inactivity: int | None = None
    minimum_password_age: int | None = None
    allow_web_remote_access: bool | None = None
    allow_ssh_remote_access: bool | None = None
    remote_user_id: str | None = None


@dataclass(frozen=True)
class ModifyUserPatch:
    """User-profile fields to replace; omitted values remain unchanged."""

    password: str | None = None
    description: str | None = None
    authentication_type: AuthenticationType | None = None
    associated_task_role: str | None = None
    associated_resource_roles: list[str] | None = None
    password_expiry: int | None = None
    session_timeout: int | None = None
    verify_session_timeout: bool | None = None
    idle_session_timeout: int | None = None
    user_inactivity: int | None = None
    minimum_password_age: int | None = None
    allow_web_remote_access: bool | None = None
    allow_ssh_remote_access: bool | None = None
    remote_user_id: str | None = None


async def create_user(
    hmc: HMCClient,
    console_uuid: str,
    request: CreateUserRequest,
) -> dict[str, Any] | None:
    """Create an HMC user profile."""
    document = build_hmc_user_document(**asdict(request))
    return await hmc.create_hmc_user(console_uuid, document)


async def modify_user(
    hmc: HMCClient,
    console_uuid: str,
    user_profile_uuid: str,
    patch: ModifyUserPatch,
) -> dict[str, Any] | None:
    """Apply the supplied fields to an HMC user profile."""
    document = build_hmc_user_document(**asdict(patch))
    return await hmc.modify_hmc_user(console_uuid, user_profile_uuid, document)


async def delete_user(
    hmc: HMCClient, console_uuid: str, user_profile_uuid: str
) -> None:
    """Delete an HMC user profile."""
    await hmc.delete_hmc_user(console_uuid, user_profile_uuid)


async def configure_remote_access(
    hmc: HMCClient,
    console_uuid: str,
    values: dict[str, str | int | bool] | None,
    clear_fields: list[str] | None,
) -> dict[str, Any] | None:
    """Set and clear HMC remote-access fields."""
    return await hmc.configure_remote_access(console_uuid, values, clear_fields)
