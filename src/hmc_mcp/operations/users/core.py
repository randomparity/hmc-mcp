"""Presentation-neutral HMC user and remote-access operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from hmc_mcp.client.core import HMCClient

from ...documents import (
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
